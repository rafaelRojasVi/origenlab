/**
 * La sexta medición propia, `visita`: una por vista de página (la carga y cada vuelta con
 * Atrás/Adelante desde el bfcache), sin cookies ni almacenamiento (el contrato de las otras cinco
 * acciones está en `stats.ts`). Va aparte para que el trozo que cargan todas las páginas sea
 * mínimo. El Worker acepta `props.origen` (una de siete palabras) y `props.pagina`
 * (`^/[a-z0-9/-]{0,80}$`); el referente entero y la query nunca salen del navegador. Las páginas
 * `noindex` (404, laboratorios internos) llevan `data-noindex` en `<html>` y no se cuentan.
 */
const PAGINA = /^(?!.*\/\/)\/[a-z0-9/-]{0,80}$/;
/**
 * Apps de Android que mandan su paquete como referente (`android-app://<paquete>`). El paquete de
 * la app de Google se arma por partes: escrito entero tiene la forma `<x>.<veinte letras>` que el
 * control de higiene del repositorio público toma por un login del pooler de Supabase.
 */
const GOOGLE_APP = ['com', 'google', 'android', 'googlequicksearchbox'].join('.');
const APPS: Record<string, string> = {
  [GOOGLE_APP]: 'google',
  'com.linkedin.android': 'linkedin',
  'com.whatsapp': 'whatsapp',
};
let installed = false;

/** Una de siete palabras. Puro: `search`, `referrer` y `hostname` los da quien llama. */
export function classifyOrigin(search: string, referrer: string, hostname: string): string {
  const q = new URLSearchParams(search);
  const src = q.get('utm_source');
  if (src === 'gbp' || q.get('utm_medium') === 'gbp') return 'perfil-google';
  if (src === 'whatsapp') return 'whatsapp';
  let host = '';
  let app = false;
  try {
    const url = referrer ? new URL(referrer) : null;
    host = url?.hostname.toLowerCase() ?? '';
    app = url?.protocol === 'android-app:';
  } catch {}
  if (!host) return 'directo';
  if (app) return APPS[host] ?? 'otro';
  if (host === hostname || /(^|\.)origenlab\.cl$/.test(host)) return 'interno';
  if (/(^|\.)google\.[a-z.]{2,6}$/.test(host)) return 'google';
  if (/(^|\.)linkedin\.com$/.test(host)) return 'linkedin';
  if (/^(l\.)?wa\.me$|(^|\.)whatsapp\.com$/.test(host)) return 'whatsapp';
  return 'otro';
}

function send(): void {
  try {
    if (document.documentElement.hasAttribute('data-noindex')) return;
    const pagina = location.pathname.toLowerCase();
    if (!PAGINA.test(pagina)) return;
    const body = JSON.stringify({ name: 'visita', props: { origen: classifyOrigin(location.search, document.referrer, location.hostname), pagina } });
    navigator.sendBeacon('/api/stats/event', new Blob([body], { type: 'application/json' }));
  } catch {}
}

/** Envía la visita de esta carga y la de cada restauración desde el bfcache; una por vista. */
export function trackVisit(): void {
  if (installed) return;
  installed = true;
  send();
  addEventListener('pageshow', (e) => e.persisted && send());
}
