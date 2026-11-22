/**
 * Cloudflare Turnstile en el formulario de cotización (`/contacto/` y la burbuja).
 *
 * Apagado mientras no haya clave de sitio: sin clave, el sitio se construye igual
 * que siempre (sin widget, sin script de terceros, CSP sin cambios). Con clave, los
 * dos formularios llevan el widget, la CSP de `.htaccess` admite
 * `https://challenges.cloudflare.com` y el Worker puede exigir el token
 * (`apps/dashboard-proxy/README.md`, «Turning on Turnstile»).
 *
 * La clave de sitio es pública por diseño: va en el HTML. La clave secreta nunca
 * vive aquí; es un secreto del Worker. `PUBLIC_TURNSTILE_SITE_KEY` en el entorno
 * del build reemplaza la de este archivo.
 */
export const TURNSTILE_SITE_KEY = '';

/** El origen del script y del iframe del widget, el único tercero que suma Turnstile. */
export const TURNSTILE_ORIGIN = 'https://challenges.cloudflare.com';

const SITE_KEY_RE = /^[0-9A-Za-z_-]{10,100}$/;

/**
 * La clave de sitio en uso, o '' si Turnstile está apagado. Una clave con otra forma
 * detiene el build en vez de publicar un widget roto.
 * @param {string | undefined} fromEnv
 * @returns {string}
 */
export function turnstileSiteKey(fromEnv) {
  const key = (fromEnv ?? '').trim() || TURNSTILE_SITE_KEY.trim();
  if (key && !SITE_KEY_RE.test(key)) {
    throw new Error(`Clave de sitio de Turnstile con forma inesperada: ${key.slice(0, 12)}…`);
  }
  return key;
}
