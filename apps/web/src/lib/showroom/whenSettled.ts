/**
 * Cuándo puede montarse la escena 3D en un teléfono o en un equipo modesto:
 * no durante la carga, sino con el primer gesto del visitante (desplazamiento,
 * rueda, toque, puntero o tecla) o, sin gesto, unos 4 s después de `load`, en
 * un momento ocioso del navegador. Lo que llegue antes, una sola vez.
 *
 * Hasta entonces la página muestra la imagen fija y el hilo principal queda
 * libre para la primera lectura. No es parte de la escena: lo importan los
 * scripts de página (`Showroom`, `ProductStory`) y no pesa en `scene-*`.
 */
const GESTURES = ['scroll', 'wheel', 'touchstart', 'pointerdown', 'keydown'] as const;
const AFTER_LOAD_MS = 4000;
const IDLE_TIMEOUT_MS = 1000;

export function whenSettled(run: () => void): void {
  let done = false;
  const fire = () => {
    if (done) return;
    done = true;
    for (const type of GESTURES) removeEventListener(type, fire, true);
    run();
  };
  for (const type of GESTURES) addEventListener(type, fire, { capture: true, passive: true });
  const idle = () => ('requestIdleCallback' in window ? requestIdleCallback(fire, { timeout: IDLE_TIMEOUT_MS }) : fire());
  const later = () => setTimeout(idle, AFTER_LOAD_MS);
  if (document.readyState === 'complete') later();
  else addEventListener('load', later, { once: true });
}
