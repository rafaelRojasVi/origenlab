-- OrigenLab, boletín: esquema de ingreso y evidencia.
--
-- Esta base **no es la autoridad de campañas**. Es el punto de entrada de una
-- suscripción desde el sitio público y el registro que la prueba. La verdad
-- comercial duradera vive en el CRM, y el camino previsto es que una
-- confirmación válida cree allí el permiso de marketing a través del límite de
-- comandos auditado, no que alguien copie direcciones a mano. Mientras ese
-- puente no exista, esta base es un depósito de evidencia y nada más.
--
-- Cuatro decisiones que conviene no deshacer:
--
--   1. **Una solicitud pendiente no es un permiso.** Vive en su propia tabla y
--      caduca. El esquema del CRM trata la ausencia de permiso como negativa, y
--      una solicitud sin confirmar es exactamente ausencia de permiso.
--   2. **Nunca se guarda un token, sólo su huella.** Quien lea esta base no
--      puede fabricar un enlace de confirmación ni de baja con lo que ve.
--   3. **La baja no borra.** Deja una fila de supresión que se conserva, y
--      volver a suscribirse crea filas nuevas en vez de reescribir las
--      anteriores. La historia de qué se autorizó y cuándo queda legible.
--   4. **Ninguna tabla guarda una dirección IP.** El control de caudal usa un
--      HMAC con clave secreta, que sirve para contar y no para identificar.

-- Solicitud pendiente de confirmación. No es una suscripción.
create table subscription_request (
  id                   text primary key,
  email_norm           text not null,
  name                 text,
  organization         text,
  interests            text not null default '[]',
  consent_text_version text not null,
  consent_at           text not null,
  source               text not null,
  confirm_token_hash   text not null unique,
  expires_at           text not null,
  consumed_at          text,
  created_at           text not null
);
create index subscription_request_email_idx on subscription_request (email_norm);
create index subscription_request_expiry_idx on subscription_request (expires_at) where consumed_at is null;

-- Suscripción confirmada. Una viva por dirección; el histórico no tiene límite.
create table subscription (
  id                     text primary key,
  email_norm             text not null,
  name                   text,
  organization           text,
  interests              text not null default '[]',
  consent_text_version   text not null,
  consent_at             text not null,
  confirmed_at           text not null,
  source                 text not null,
  request_id             text not null,
  unsubscribe_token_hash text not null unique,
  revoked_at             text,
  revoked_reason         text,
  created_at             text not null,
  check ((revoked_at is null) = (revoked_reason is null))
);
create unique index subscription_live_key on subscription (email_norm) where revoked_at is null;
create index subscription_email_idx on subscription (email_norm);

-- Supresión. Se añade, nunca se borra ni se reescribe.
--
-- Una dirección se considera suprimida salvo que exista una suscripción viva
-- confirmada **después** de la última supresión. Así una persona puede volver a
-- suscribirse de verdad sin que eso borre la prueba de que un día pidió no
-- recibir nada.
create table suppression (
  id         integer primary key autoincrement,
  email_norm text not null,
  reason     text not null,
  created_at text not null,
  check (reason in ('unsubscribe_request', 'complaint', 'bounce_hard', 'operator_revocation'))
);
create index suppression_email_idx on suppression (email_norm, created_at);

-- Registro de sucesos. Es la evidencia, y por eso sólo se añade.
create table subscription_event (
  id         integer primary key autoincrement,
  at         text not null,
  email_norm text not null,
  kind       text not null,
  detail     text,
  check (kind in (
    'requested', 'request_replaced', 'request_expired', 'request_discarded',
    'confirmed', 'already_confirmed',
    'unsubscribed', 'already_unsubscribed',
    'rejected_rate_limited', 'rejected_trap'
  ))
);
create index subscription_event_email_idx on subscription_event (email_norm, at);

-- Control de caudal. La clave es un HMAC con clave secreta, no una IP.
create table rate_bucket (
  key_hmac     text not null,
  window_start text not null,
  hits         integer not null,
  primary key (key_hmac, window_start)
);
