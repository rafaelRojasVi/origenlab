/**
 * Constantes de política del boletín.
 *
 * Los dos plazos de aquí son **valores por defecto de ingeniería, no plazos
 * aprobados**. El plazo de conservación de una suscripción, de una baja y de su
 * evidencia es una decisión legal registrada como pendiente en
 * `apps/web/src/data/legal.ts` (`plazo-conservacion-suscripcion`), y es una de
 * las ocho puertas de activación. Nada se publica con estos números como
 * respuesta.
 */

/** Cuánto vive un enlace de confirmación sin usar. */
export const CONFIRM_TTL_HOURS = 72;

/** Envío más rápido que esto no lo hizo una persona leyendo el formulario. */
export const MIN_FILL_SECONDS = 2;

/** Tamaño máximo del cuerpo admitido, en bytes. */
export const MAX_BODY_BYTES = 4096;

/** Caudal por ventana de una hora. */
export const RATE_WINDOW_MINUTES = 60;
export const RATE_MAX_PER_CLIENT = 5;
export const RATE_MAX_PER_EMAIL = 3;

export const FIELD_LIMITS = {
  email: 254,
  name: 120,
  organization: 160,
  source: 64,
  consentTextVersion: 32,
} as const;

export const MAX_INTERESTS = 12;

/**
 * Intereses admitidos. Es la lista cerrada de `equipmentScope.ts` en el sitio.
 * Se repite aquí porque los dos despliegues son independientes, y `test/`
 * comprueba que sigan diciendo lo mismo.
 */
export const INTERESTS = [
  'pesaje-humedad',
  'dispersion-homogeneizacion',
  'sonicacion',
  'centrifugacion',
  'osmometria',
  'electroforesis',
] as const;

/** Versiones del texto de consentimiento que el endpoint acepta. */
export const CONSENT_TEXT_VERSIONS = ['2026-09-21'] as const;

/** Rutas estáticas del sitio a las que redirige el camino sin JavaScript. */
export const PAGES = {
  requestReceived: '/newsletter/solicitud-recibida/',
  notSent: '/newsletter/no-enviado/',
  confirmed: '/newsletter/confirmada/',
  invalidLink: '/newsletter/enlace-no-valido/',
  unsubscribed: '/newsletter/baja-confirmada/',
} as const;
