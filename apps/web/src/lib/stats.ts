/**
 * Medición propia: cinco eventos, sin cookies, sin almacenamiento, sin terceros.
 *
 * El navegador envía el evento a una ruta del propio dominio con
 * `navigator.sendBeacon`; la recibe el Worker de Cloudflare que atiende
 * `origenlab.cl/api/stats/event`. El Worker sólo acepta POST del mismo origen,
 * cuerpo JSON de hasta 2048 bytes, un `name` de la lista de abajo y, en
 * `props`, únicamente `modelo` (el slug del modelo, `[A-Za-z0-9._-]{1,64}`).
 *
 * Se envía un `Blob` con `application/json` para que el Worker vea ese tipo;
 * al ser del mismo origen no hay preflight. Sin `sendBeacon` no se hace nada:
 * no hay respaldo con `fetch`. `sendBeacon` sobrevive a la navegación, por eso
 * sirve para el envío del formulario y para los enlaces que abren otra pestaña.
 *
 * Este archivo no debe importar nada ni quedar dentro de un trozo `scene-*`:
 * lo cargan los guiones de página (`ModelHero`, `ProductStory`, `contacto`).
 * `scripts/validate-privacy.mjs` comprueba que lo construido no toca
 * almacenamiento ni cookies ni dominios ajenos.
 */
export type StatsEventName = 'cotizacion_click' | 'whatsapp_click' | '3d_interact' | 'form_submit' | 'pdf_download';

const ENDPOINT = '/api/stats/event';
const MODELO = /^[A-Za-z0-9._-]{1,64}$/;

export function trackEvent(name: StatsEventName, props: { modelo?: string | null } = {}): void {
  try {
    if (typeof navigator === 'undefined' || typeof navigator.sendBeacon !== 'function') return;
    const modelo = props.modelo;
    const payload = { name, props: modelo && MODELO.test(modelo) ? { modelo } : {} };
    navigator.sendBeacon(ENDPOINT, new Blob([JSON.stringify(payload)], { type: 'application/json' }));
  } catch {
    // Medir nunca debe romper la página.
  }
}
