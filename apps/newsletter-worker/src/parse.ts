/**
 * Lectura y validación del cuerpo del formulario.
 *
 * Separado del manejador para que se pueda probar solo, y porque es la
 * superficie por la que entra todo lo que no controlamos.
 */
import { CONSENT_TEXT_VERSIONS, FIELD_LIMITS, INTERESTS, MAX_BODY_BYTES, MAX_INTERESTS } from './config';
import { normalizeEmail, normalizeText } from './normalize';
import type { SubscribeInput, SubscribeRejection } from './types';

export type ParseOutcome =
  | { ok: true; input: SubscribeInput; emailNorm: string }
  | { ok: false; rejection: SubscribeRejection };

/**
 * Lee el cuerpo con tope duro.
 *
 * `Content-Length` no basta: puede mentir o faltar en una petición troceada, así
 * que además se cuenta lo que llega y se corta.
 */
export async function readBody(request: Request): Promise<string | null> {
  const declared = Number(request.headers.get('content-length') ?? '0');
  if (Number.isFinite(declared) && declared > MAX_BODY_BYTES) return null;

  const body = request.body;
  if (!body) return '';

  const reader = body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > MAX_BODY_BYTES) {
      await reader.cancel();
      return null;
    }
    chunks.push(value);
  }
  const merged = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    merged.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return new TextDecoder().decode(merged);
}

export function parseSubscribe(raw: string): ParseOutcome {
  const params = new URLSearchParams(raw);

  const trap = (params.get('sitio_web') ?? '').trim();
  const renderedRaw = params.get('rendered_at');
  const renderedAt = renderedRaw && /^\d{1,15}$/.test(renderedRaw) ? Number(renderedRaw) : null;

  const emailNorm = normalizeEmail(params.get('email') ?? '');
  if (!emailNorm) return { ok: false, rejection: 'invalid_email' };

  if ((params.get('consentimiento') ?? '') !== 'si') {
    return { ok: false, rejection: 'missing_consent' };
  }

  const nameRaw = params.get('nombre');
  const orgRaw = params.get('organizacion');
  if ((nameRaw ?? '').length > FIELD_LIMITS.name * 2) return { ok: false, rejection: 'field_too_long' };
  if ((orgRaw ?? '').length > FIELD_LIMITS.organization * 2) {
    return { ok: false, rejection: 'field_too_long' };
  }
  const name = normalizeText(nameRaw, FIELD_LIMITS.name);
  const organization = normalizeText(orgRaw, FIELD_LIMITS.organization);
  if (nameRaw !== null && nameRaw.trim().length > 0 && name === null) {
    return { ok: false, rejection: 'field_too_long' };
  }
  if (orgRaw !== null && orgRaw.trim().length > 0 && organization === null) {
    return { ok: false, rejection: 'field_too_long' };
  }

  const interests = params.getAll('intereses');
  if (interests.length > MAX_INTERESTS) return { ok: false, rejection: 'too_many_interests' };
  const unique = [...new Set(interests)];
  for (const interest of unique) {
    if (!(INTERESTS as readonly string[]).includes(interest)) {
      return { ok: false, rejection: 'unknown_interest' };
    }
  }

  const consentTextVersion = params.get('consent_version') ?? '';
  if (!(CONSENT_TEXT_VERSIONS as readonly string[]).includes(consentTextVersion)) {
    return { ok: false, rejection: 'unknown_consent_version' };
  }

  const source = normalizeText(params.get('source'), FIELD_LIMITS.source) ?? 'web_unknown';

  return {
    ok: true,
    emailNorm,
    input: {
      email: emailNorm,
      name,
      organization,
      interests: unique,
      consentTextVersion,
      source,
      renderedAt,
      trap,
    },
  };
}
