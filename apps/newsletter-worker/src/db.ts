/**
 * Acceso a D1. Todas las consultas del Worker pasan por aquí.
 *
 * Ninguna función de este archivo registra nada. La dirección, el token y su
 * huella no aparecen en ningún log, ni siquiera en el camino de error: un
 * registro de aplicación es el sitio más fácil desde el que se filtra una lista
 * de correos, y no hay ninguna pregunta operativa que necesite ese dato.
 */
import type { SubscribeInput } from './types';

export type EventKind =
  | 'requested'
  | 'request_replaced'
  | 'request_expired'
  | 'request_discarded'
  | 'confirmed'
  | 'already_confirmed'
  | 'unsubscribed'
  | 'already_unsubscribed'
  | 'rejected_rate_limited'
  | 'rejected_trap';

export interface RequestRow {
  id: string;
  email_norm: string;
  name: string | null;
  organization: string | null;
  interests: string;
  consent_text_version: string;
  consent_at: string;
  source: string;
}

export interface SubscriptionRow {
  id: string;
  email_norm: string;
  confirmed_at: string;
  revoked_at: string | null;
}

export async function recordEvent(
  db: D1Database,
  emailNorm: string,
  kind: EventKind,
  detail: string | null = null,
): Promise<void> {
  await db
    .prepare('insert into subscription_event (at, email_norm, kind, detail) values (?, ?, ?, ?)')
    .bind(new Date().toISOString(), emailNorm, kind, detail)
    .run();
}

/**
 * Sustituye cualquier solicitud pendiente de esa dirección por una nueva.
 *
 * Que la anterior se descarte es deliberado: dos enlaces vivos para la misma
 * dirección significan que el primero sigue sirviendo después de que la persona
 * pidiera otro, que es justo lo que alguien esperaría que dejara de valer.
 */
export async function insertRequest(
  db: D1Database,
  params: {
    id: string;
    emailNorm: string;
    input: SubscribeInput;
    confirmTokenHash: string;
    expiresAt: string;
    now: string;
  },
): Promise<void> {
  const previous = await db
    .prepare('select count(*) as n from subscription_request where email_norm = ? and consumed_at is null')
    .bind(params.emailNorm)
    .first<{ n: number }>();

  await db
    .prepare('delete from subscription_request where email_norm = ? and consumed_at is null')
    .bind(params.emailNorm)
    .run();

  if ((previous?.n ?? 0) > 0) {
    await recordEvent(db, params.emailNorm, 'request_replaced');
  }

  await db
    .prepare(
      `insert into subscription_request
         (id, email_norm, name, organization, interests, consent_text_version,
          consent_at, source, confirm_token_hash, expires_at, created_at)
       values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    )
    .bind(
      params.id,
      params.emailNorm,
      params.input.name,
      params.input.organization,
      JSON.stringify(params.input.interests),
      params.input.consentTextVersion,
      params.now,
      params.input.source,
      params.confirmTokenHash,
      params.expiresAt,
      params.now,
    )
    .run();
}

export async function discardRequest(db: D1Database, id: string): Promise<void> {
  await db.prepare('delete from subscription_request where id = ?').bind(id).run();
}

export async function findLiveRequest(
  db: D1Database,
  confirmTokenHash: string,
  now: string,
): Promise<RequestRow | null> {
  return db
    .prepare(
      `select id, email_norm, name, organization, interests, consent_text_version, consent_at, source
         from subscription_request
        where confirm_token_hash = ? and consumed_at is null and expires_at > ?`,
    )
    .bind(confirmTokenHash, now)
    .first<RequestRow>();
}

export async function consumeRequest(db: D1Database, id: string, now: string): Promise<void> {
  await db
    .prepare('update subscription_request set consumed_at = ? where id = ? and consumed_at is null')
    .bind(now, id)
    .run();
}

export async function findLiveSubscription(
  db: D1Database,
  emailNorm: string,
): Promise<SubscriptionRow | null> {
  return db
    .prepare(
      'select id, email_norm, confirmed_at, revoked_at from subscription where email_norm = ? and revoked_at is null',
    )
    .bind(emailNorm)
    .first<SubscriptionRow>();
}

export async function insertSubscription(
  db: D1Database,
  params: { id: string; request: RequestRow; unsubscribeTokenHash: string; now: string },
): Promise<void> {
  await db
    .prepare(
      `insert into subscription
         (id, email_norm, name, organization, interests, consent_text_version, consent_at,
          confirmed_at, source, request_id, unsubscribe_token_hash, created_at)
       values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    )
    .bind(
      params.id,
      params.request.email_norm,
      params.request.name,
      params.request.organization,
      params.request.interests,
      params.request.consent_text_version,
      params.request.consent_at,
      params.now,
      params.request.source,
      params.request.id,
      params.unsubscribeTokenHash,
      params.now,
    )
    .run();
}

export async function findSubscriptionByUnsubscribeHash(
  db: D1Database,
  hash: string,
): Promise<SubscriptionRow | null> {
  return db
    .prepare(
      'select id, email_norm, confirmed_at, revoked_at from subscription where unsubscribe_token_hash = ?',
    )
    .bind(hash)
    .first<SubscriptionRow>();
}

/**
 * Revoca la suscripción y deja la supresión.
 *
 * La supresión es una fila nueva, no una bandera que se pone y se quita. Por
 * eso volver a suscribirse más adelante no borra nada: queda la supresión de
 * entonces y la confirmación de ahora, y la elegibilidad se decide comparando
 * sus fechas.
 */
export async function revokeSubscription(
  db: D1Database,
  params: { id: string; emailNorm: string; reason: string; now: string },
): Promise<void> {
  await db
    .prepare('update subscription set revoked_at = ?, revoked_reason = ? where id = ? and revoked_at is null')
    .bind(params.now, params.reason, params.id)
    .run();
  await db
    .prepare('insert into suppression (email_norm, reason, created_at) values (?, ?, ?)')
    .bind(params.emailNorm, params.reason, params.now)
    .run();
}

/**
 * Estado canónico de supresión de una dirección.
 *
 * Suprimida salvo que exista una suscripción viva confirmada después de la
 * última supresión. Es la consulta que un envío de campaña tiene que hacer
 * antes de enviar, y la razón por la que copiar suscriptores a mano no es una
 * garantía suficiente: una copia se queda quieta mientras esta respuesta cambia.
 */
export async function isSuppressed(db: D1Database, emailNorm: string): Promise<boolean> {
  const last = await db
    .prepare('select max(created_at) as at from suppression where email_norm = ?')
    .bind(emailNorm)
    .first<{ at: string | null }>();
  if (!last?.at) return false;

  const live = await findLiveSubscription(db, emailNorm);
  if (!live) return true;
  return live.confirmed_at <= last.at;
}

/** Cuenta y limita por ventana. La clave ya viene convertida en HMAC. */
export async function hitRateLimit(
  db: D1Database,
  keyHmac: string,
  windowStart: string,
  max: number,
): Promise<boolean> {
  await db
    .prepare(
      `insert into rate_bucket (key_hmac, window_start, hits) values (?, ?, 1)
         on conflict (key_hmac, window_start) do update set hits = rate_bucket.hits + 1`,
    )
    .bind(keyHmac, windowStart)
    .run();
  const row = await db
    .prepare('select hits from rate_bucket where key_hmac = ? and window_start = ?')
    .bind(keyHmac, windowStart)
    .first<{ hits: number }>();
  return (row?.hits ?? 0) > max;
}
