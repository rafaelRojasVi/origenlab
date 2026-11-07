/**
 * Medición propia: seis eventos (cinco acciones y la `visita`), sin cookies, sin almacenamiento, sin terceros.
 *
 * Este archivo manda las cinco acciones (`StatsEventName`); la `visita` (origen y página) vive en
 * `visit.ts`, un trozo aparte que carga toda página sin arrastrar el escucha de clics.
 *
 * El navegador envía el evento a una ruta del propio dominio con
 * `navigator.sendBeacon`; la recibe el Worker de Cloudflare que atiende
 * `origenlab.cl/api/stats/event`. El Worker sólo acepta POST del mismo origen,
 * cuerpo JSON de hasta 2048 bytes, un `name` de los seis (los cinco de abajo y `visita`) y, en
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

const CLICK_EVENTS: readonly string[] = ['cotizacion_click', 'whatsapp_click', 'pdf_download'];
let clicksInstalled = false;

/**
 * Un solo escucha de clics en `document` para toda página de producto. Lo que se
 * mide lleva `data-track="<evento>"` y, si corresponde, `data-modelo="<slug>"`.
 * Lo llaman la página de modelo (`ModelHero`) y la ficha heredada de
 * centrifugación; instalarlo dos veces no duplica los envíos.
 */
export function installTrackClicks(): void {
  if (clicksInstalled || typeof document === 'undefined') return;
  clicksInstalled = true;
  document.addEventListener('click', (event) => {
    const link = (event.target as Element | null)?.closest<HTMLElement>('[data-track]');
    const name = link?.dataset.track;
    if (link && name && CLICK_EVENTS.includes(name)) trackEvent(name as StatsEventName, { modelo: link.dataset.modelo });
  });
}
