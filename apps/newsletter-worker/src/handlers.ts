/**
 * Los tres caminos del boletín: alta, confirmación y baja.
 *
 * Están separados del enrutado para poder probarlos contra una base real sin
 * levantar un Worker. El enrutado decide la forma de la respuesta (JSON,
 * redirección o texto plano), estos manejadores deciden qué ocurre.
 */
import {
  CONFIRM_TTL_HOURS,
  MIN_FILL_SECONDS,
  RATE_MAX_PER_CLIENT,
  RATE_MAX_PER_EMAIL,
  RATE_WINDOW_MINUTES,
} from './config';
import {
  consumeRequest,
  discardRequest,
  findLiveRequest,
  findLiveSubscription,
  findSubscriptionByUnsubscribeHash,
  hitRateLimit,
  insertRequest,
  insertSubscription,
  recordEvent,
  revokeSubscription,
} from './db';
import { SenderUnavailable, type EmailSender } from './email';
import { hashToken, isTokenShape, mintToken, rateKey } from './tokens';
import type { ConfirmResult, SubscribeInput, SubscribeResult, UnsubscribeResult } from './types';

export interface HandlerContext {
  db: D1Database;
  sender: EmailSender;
  pepper: string;
  rateSecret: string;
  siteOrigin: string;
  /** Identificador del cliente para el caudal. Nunca se almacena en claro. */
  clientId: string;
  now: Date;
}

function windowStart(now: Date): string {
  const ms = RATE_WINDOW_MINUTES * 60 * 1000;
  return new Date(Math.floor(now.getTime() / ms) * ms).toISOString();
}

/**
 * Alta.
 *
 * Dos cosas que conviene leer despacio:
 *
 * 1. **La respuesta es la misma para una dirección nueva, una ya suscrita y una
 *    dada de baja.** Distinguirlas convertiría el endpoint en un comprobador de
 *    direcciones ajenas, que es una filtración a cambio de nada.
 * 2. **Una trampa devuelve la misma respuesta y no guarda nada.** Decirle a un
 *    envío automático que ha sido detectado sólo sirve para que la siguiente
 *    versión lo evite. Una persona no puede caer en ninguna de las dos trampas:
 *    el campo señuelo no se ve ni se tabula, y el mínimo de tiempo es de
 *    segundos.
 */
export async function handleSubscribe(
  context: HandlerContext,
  input: SubscribeInput,
  emailNorm: string,
): Promise<SubscribeResult> {
  if (input.trap.length > 0) {
    return { ok: true, status: 'pending' };
  }
  if (input.renderedAt !== null) {
    const elapsed = (context.now.getTime() - input.renderedAt) / 1000;
    if (elapsed >= 0 && elapsed < MIN_FILL_SECONDS) {
      return { ok: true, status: 'pending' };
    }
  }

  const bucket = windowStart(context.now);
  const clientKey = await rateKey(context.clientId, context.rateSecret);
  const emailKey = await rateKey(emailNorm, context.rateSecret);
  const clientOver = await hitRateLimit(context.db, clientKey, bucket, RATE_MAX_PER_CLIENT);
  const emailOver = await hitRateLimit(context.db, emailKey, bucket, RATE_MAX_PER_EMAIL);
  if (clientOver || emailOver) {
    await recordEvent(context.db, emailNorm, 'rejected_rate_limited');
    return { ok: false, rejection: 'rate_limited' };
  }

  const nowIso = context.now.toISOString();
  const expiresAt = new Date(
    context.now.getTime() + CONFIRM_TTL_HOURS * 60 * 60 * 1000,
  ).toISOString();

  const requestId = crypto.randomUUID();
  const token = mintToken();
  const confirmTokenHash = await hashToken(token, context.pepper);

  await insertRequest(context.db, {
    id: requestId,
    emailNorm,
    input,
    confirmTokenHash,
    expiresAt,
    now: nowIso,
  });
  await recordEvent(context.db, emailNorm, 'requested', input.source);

  try {
    await context.sender.send({
      to: emailNorm,
      confirmUrl: `${context.siteOrigin}/api/newsletter/confirm?t=${token}`,
      consentTextVersion: input.consentTextVersion,
    });
  } catch (error) {
    /*
     * Fallo cerrado. Si no se puede enviar la confirmación, la solicitud no
     * sirve para nada: nadie podría confirmarla y habríamos guardado datos
     * personales a cambio de un enlace muerto. Se descarta y se responde con un
     * error explícito, nunca con un éxito.
     */
    await discardRequest(context.db, requestId);
    await recordEvent(context.db, emailNorm, 'request_discarded', 'sender_unavailable');
    if (error instanceof SenderUnavailable) {
      return { ok: false, rejection: 'sender_unavailable' };
    }
    throw error;
  }

  return { ok: true, status: 'pending' };
}

/** Confirmación. Sólo por POST: un GET no cambia nada. */
export async function handleConfirm(
  context: HandlerContext,
  token: string,
): Promise<ConfirmResult> {
  if (!isTokenShape(token)) return { status: 'invalid' };

  const nowIso = context.now.toISOString();
  const hash = await hashToken(token, context.pepper);
  const request = await findLiveRequest(context.db, hash, nowIso);
  if (!request) return { status: 'invalid' };

  /* Un enlace sirve una sola vez, valga lo que valga el resto. */
  await consumeRequest(context.db, request.id, nowIso);

  const live = await findLiveSubscription(context.db, request.email_norm);
  if (live) {
    await recordEvent(context.db, request.email_norm, 'already_confirmed');
    return { status: 'already_confirmed' };
  }

  const subscriptionId = crypto.randomUUID();
  const unsubscribeToken = await deriveUnsubscribeToken(subscriptionId, context.pepper);
  await insertSubscription(context.db, {
    id: subscriptionId,
    request,
    unsubscribeTokenHash: await hashToken(unsubscribeToken, context.pepper),
    now: nowIso,
  });
  await recordEvent(context.db, request.email_norm, 'confirmed', request.consent_text_version);
  return { status: 'confirmed' };
}

/**
 * Baja. Sólo por POST, y el mismo efecto por los dos caminos: el botón de la
 * página intermedia y el POST de un clic de RFC 8058. Lo único que cambia entre
 * ellos es la forma de la respuesta.
 */
export async function handleUnsubscribe(
  context: HandlerContext,
  token: string,
): Promise<UnsubscribeResult> {
  if (!isTokenShape(token)) return { status: 'invalid' };

  const nowIso = context.now.toISOString();
  const hash = await hashToken(token, context.pepper);
  const subscription = await findSubscriptionByUnsubscribeHash(context.db, hash);
  if (!subscription) return { status: 'invalid' };

  if (subscription.revoked_at !== null) {
    await recordEvent(context.db, subscription.email_norm, 'already_unsubscribed');
    return { status: 'already_unsubscribed' };
  }

  await revokeSubscription(context.db, {
    id: subscription.id,
    emailNorm: subscription.email_norm,
    reason: 'unsubscribe_request',
    now: nowIso,
  });
  await recordEvent(context.db, subscription.email_norm, 'unsubscribed');
  return { status: 'unsubscribed' };
}

/**
 * Token de baja de una suscripción.
 *
 * Se **deriva** del identificador aleatorio de la fila con la clave secreta del
 * Worker, en vez de acuñarse y guardarse. Tres consecuencias, y las tres son la
 * razón de hacerlo así:
 *
 * - la base no contiene ningún token en claro, sólo huellas, de modo que un
 *   volcado robado no da de baja a nadie ni confirma suscripciones ajenas;
 * - el enlace se puede reconstruir en cada envío a partir del identificador y
 *   del secreto, sin guardar nada más y sin que caduque;
 * - sin el secreto no se puede fabricar, aunque se conozca el identificador.
 *
 * No se deriva de la dirección: el identificador es un UUID aleatorio, así que
 * el enlace no filtra de quién es.
 */
export async function deriveUnsubscribeToken(
  subscriptionId: string,
  pepper: string,
): Promise<string> {
  return hashToken(`unsubscribe:${subscriptionId}`, pepper);
}
