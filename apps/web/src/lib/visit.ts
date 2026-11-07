/**
 * La sexta medición propia, `visita`: una por carga de página, sin cookies ni almacenamiento
 * (el contrato de las otras cinco acciones está en `stats.ts`). Va aparte para que el trozo que
 * cargan todas las páginas sea mínimo. El Worker acepta `props.origen` (una de seis palabras) y
 * `props.pagina` (`^/[a-z0-9/-]{0,80}$`); el referente entero y la query nunca salen del navegador.
 */
const PAGINA = /^(?!.*\/\/)\/[a-z0-9/-]{0,80}$/;
let sent = false;

/** Una de seis palabras. Puro: `search`, `referrer` y `hostname` los da quien llama. */
export function classifyOrigin(search: string, referrer: string, hostname: string): string {
  const q = new URLSearchParams(search);
  const src = q.get('utm_source');
  if (src === 'gbp' || q.get('utm_medium') === 'gbp') return 'perfil-google';
  if (src === 'whatsapp') return 'whatsapp';
  let host = '';
  try {
    host = referrer ? new URL(referrer).hostname.toLowerCase() : '';
  } catch {}
  if (!host || host === hostname || /(^|\.)origenlab\.cl$/.test(host)) return 'directo';
  if (/(^|\.)google\.[a-z.]{2,6}$/.test(host)) return 'google';
  if (/(^|\.)linkedin\.com$/.test(host)) return 'linkedin';
  if (/^(l\.)?wa\.me$|(^|\.)whatsapp\.com$/.test(host)) return 'whatsapp';
  return 'otro';
}

export function trackVisit(): void {
  if (sent) return;
  sent = true;
  try {
    const pagina = location.pathname.toLowerCase();
    if (!PAGINA.test(pagina)) return;
    const body = JSON.stringify({ name: 'visita', props: { origen: classifyOrigin(location.search, document.referrer, location.hostname), pagina } });
    navigator.sendBeacon('/api/stats/event', new Blob([body], { type: 'application/json' }));
  } catch {}
}
