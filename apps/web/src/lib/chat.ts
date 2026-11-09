/**
 * Lo que la burbuja de conversación (`ChatBubble.astro`) pide al primer clic.
 * El guion, el orden de las preguntas y la validación son HTML y CSS; aquí sólo
 * se mide, se compone el WhatsApp con las respuestas y se envía la solicitud
 * igual que el formulario de `/contacto/` (mismos campos, JSON, misma ruta).
 *
 * Se construye como paquete propio (`?worker&url` en la burbuja) y corre como
 * <script> clásico en la página: por eso no importa nada y la medición repite
 * aquí el envío de `lib/stats.ts` (mismo destino, misma forma, `sendBeacon`,
 * sin almacenamiento) en vez de importarlo.
 *
 * Sin `https://` literal: la URL de WhatsApp la arma `buildWhatsAppQuoteUrl` en
 * el servidor y aquí sólo se cambia su `text`.
 */
const form = document.getElementById('cb-f') as HTMLFormElement;
const field = (name: string) => form.elements.namedItem(name) as HTMLInputElement;
const value = (name: string) => field(name).value.trim();
const chip = () => form.querySelector<HTMLInputElement>('[name=product]:checked')?.dataset;
const ok = document.querySelector<HTMLElement>('[data-cb-ok]')!;
const err = form.querySelector<HTMLElement>('[data-cb-err]')!;
const send = form.querySelector<HTMLButtonElement>('[type=submit]')!;
const modelo = send.dataset.modelo;

/** El mismo envío que `trackEvent` de `lib/stats.ts`, sólo con los dos nombres que la burbuja usa. */
const track = (name: 'cotizacion_click' | 'whatsapp_click') => {
  try {
    navigator.sendBeacon('/api/stats/event', new Blob([JSON.stringify({ name, props: modelo ? { modelo } : {} })], { type: 'application/json' }));
  } catch {
    // Medir nunca debe romper la página.
  }
};

// El Worker descarta en silencio lo enviado menos de 3 s después de `render_at`.
field('render_at').value = String(Date.now() / 1000);
// Sin `randomUUID` (Safari < 15.4) va vacío y el Worker pone uno.
field('submission_id').value = crypto.randomUUID?.() ?? '';

form.querySelector<HTMLAnchorElement>('[data-track=whatsapp_click]')!.addEventListener('click', (event) => {
  const link = event.currentTarget as HTMLAnchorElement;
  const text = [
    chip()?.m,
    value('need') && `Lo usaré para: ${value('need')}.`,
    value('name') && `Soy ${value('name')}.`,
    value('org') && `Institución: ${value('org')}.`,
  ];
  link.href = link.href.split('text=')[0] + 'text=' + encodeURIComponent(text.filter(Boolean).join(' '));
  // En la página de modelo el escucha de `data-track` (`ModelHero`) ya lo mide.
  if (!document.querySelector('[data-sticky-cta]')) track('whatsapp_click');
});

form.addEventListener('submit', (event) => {
  event.preventDefault();
  // Sólo un envío válido cuenta: el navegador no deja llegar aquí sin los campos obligatorios.
  track('cotizacion_click');
  send.disabled = true;
  err.hidden = true;
  const data: Record<string, FormDataEntryValue> = Object.fromEntries(new FormData(form));
  if (!data.product) delete data.product;
  // `need` nombra el equipo elegido: la API no guarda `product`.
  const label = chip()?.l;
  if (label && label !== 'Otro') data.need = `${label}: ${data.need}`;
  fetch(form.action, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) })
    .then((response) => {
      if (!response.ok && !response.redirected) throw 0;
      form.hidden = true;
      ok.hidden = false;
      ok.focus();
    })
    .catch(() => {
      err.hidden = false;
      send.disabled = false;
      err.focus();
    });
});
