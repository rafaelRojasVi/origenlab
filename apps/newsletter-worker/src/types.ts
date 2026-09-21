/** Contrato del endpoint de suscripción, independiente de proveedor. */

export interface Env {
  DB: D1Database;
  ORIGENLAB_SITE_ORIGIN: string;
  /** `null` mientras no haya remitente real. Con ese valor el alta falla. */
  ORIGENLAB_EMAIL_SENDER: string;
  ORIGENLAB_TOKEN_PEPPER?: string;
  ORIGENLAB_RATE_LIMIT_KEY?: string;
}

/** Lo que el formulario envía. Nada más se lee del cuerpo. */
export interface SubscribeInput {
  email: string;
  name: string | null;
  organization: string | null;
  interests: readonly string[];
  consentTextVersion: string;
  source: string;
  /** Marca de tiempo que el formulario escribió al dibujarse. */
  renderedAt: number | null;
  /** Campo señuelo. Con contenido, el envío no es de una persona. */
  trap: string;
}

export type SubscribeRejection =
  | 'invalid_email'
  | 'missing_consent'
  | 'field_too_long'
  | 'too_many_interests'
  | 'unknown_interest'
  | 'unknown_consent_version'
  | 'body_too_large'
  | 'bad_origin'
  | 'rate_limited'
  | 'trap'
  | 'too_fast'
  | 'sender_unavailable';

export type SubscribeResult =
  | { ok: true; status: 'pending' }
  | { ok: false; rejection: SubscribeRejection };

export interface ConfirmResult {
  status: 'confirmed' | 'already_confirmed' | 'invalid';
}

export interface UnsubscribeResult {
  status: 'unsubscribed' | 'already_unsubscribed' | 'invalid';
}
