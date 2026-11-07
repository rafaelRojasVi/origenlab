/**
 * Arranque en el navegador de la historia de una página de producto
 * (`ProductStory.astro`): carga el modelo y su historia, monta el escenario
 * sobre el lienzo y conecta los beats al scroll (escritorio) o a los botones
 * «Anterior» / «Siguiente» (pantallas estrechas).
 *
 * Vive en el fragmento `scene-story`, que la página sólo pide con `import()`
 * cuando ya decidió mostrar 3D: la decisión (nivel de calidad, movimiento
 * reducido, ahorro de datos, si hay WebGL) es del script de la página, que
 * pesa poco y no carga three. Ese script pide también el modelo y la historia
 * (`loaders.ts`) y los pasa ya cargados: así este módulo no arrastra el mapa de
 * cargadores a su fragmento. Aquí se mide además el tiempo de cuadro de los
 * primeros ~2 s y, si el equipo no llega a ~30 cuadros por segundo, se baja un
 * nivel.
 *
 * Contrato con el HTML (atributos que pinta `ProductStory.astro`):
 *   `[data-story="<id>"]` raíz · `[data-story-canvas]` · `[data-story-spacer]`
 *   `[data-beats="<id> …"]` piezas que se encienden (`data-on`) en esos beats
 *   `[data-story-nav]` con `[data-story-prev]`, `[data-story-next]`, `[data-story-count]`
 *   `data-mode` en la raíz: `scroll`, `tap` o `static`.
 * La raíz recibe `data-live` mientras la historia corre y `data-beat` con el
 * beat activo; el lienzo, `data-ready="1"` en su primer cuadro y `data-tier`.
 */
import { beatsFromSpecs, createStoryScene } from './productStory';
import { activeBeat, createScrollStory, createTapStory } from './scrollStory';
import type { ProductStoryConfig } from './productStory';
import type { Tier } from './scene';
import type { ModelModule } from './types';

export interface MountOptions {
  tier: Tier;
  /** Movimiento reducido: el equipo queda quieto, sin historia; se gira a mano. */
  still: boolean;
  /** Medir los primeros ~2 s y bajar un nivel si el cuadro medio pasa de ~33 ms. */
  probe: boolean;
  /** El sondeo bajó de `medium`: la escena ya se liberó y la página vuelve a la imagen fija. */
  onLow(): void;
}

export interface MountedStory {
  dispose(): void;
}

const PROBE_SKIP = 10; // cuadros de compilación de sombreadores que no cuentan
const PROBE_MS = 2000;
const SLOW_FRAME_MS = 33;
// La escena no dibuja fuera de pantalla ni con la pestaña oculta: un hueco así
// es una pausa, no un cuadro lento, y no entra en la medida.
const PAUSE_MS = 1000;

export function mountStory(root: HTMLElement, { build }: ModelModule, story: ProductStoryConfig, options: MountOptions): MountedStory {
  const canvas = root.querySelector<HTMLCanvasElement>('[data-story-canvas]')!;
  const spacer = root.querySelector<HTMLElement>('[data-story-spacer]')!;
  const nav = root.querySelector<HTMLElement>('[data-story-nav]');

  let tier = options.tier;
  canvas.dataset.tier = tier;
  canvas.hidden = false;
  const scene = createStoryScene(canvas, build({ lite: tier === 'medium' }), { tier });
  scene.bindOverlays(root);

  const marked = [...root.querySelectorAll<HTMLElement>('[data-beats]')];
  let shown = '';
  const mark = (beat: string) => {
    if (beat === shown) return;
    shown = beat;
    root.dataset.beat = beat;
    for (const el of marked) el.toggleAttribute('data-on', (el.dataset.beats ?? '').split(' ').includes(beat));
  };

  let disposed = false;
  let runner: { dispose(): void } | null = null;
  const handle: MountedStory = {
    dispose() {
      if (disposed) return;
      disposed = true;
      runner?.dispose();
      scene.dispose();
      canvas.hidden = true;
      delete canvas.dataset.ready;
      delete root.dataset.live;
      delete root.dataset.beat;
      if (nav) nav.hidden = true;
      for (const el of marked) el.removeAttribute('data-on');
    },
  };

  // Primer cuadro: el lienzo releva a la imagen. Después, el sondeo de tiempo:
  // suma la duración de los cuadros dibujados hasta reunir ~2 s.
  let frames = 0;
  let last = 0;
  let measured = 0;
  let counted = 0;
  let probing = options.probe;
  scene.onFrame(() => {
    if (canvas.dataset.ready !== '1') canvas.dataset.ready = '1';
    if (!probing) return;
    const now = performance.now();
    const delta = now - last;
    last = now;
    if (++frames <= PROBE_SKIP || delta > PAUSE_MS) return;
    measured += delta;
    counted += 1;
    if (measured < PROBE_MS) return;
    probing = false;
    if (measured / counted <= SLOW_FRAME_MS) return;
    if (tier === 'high') {
      tier = 'medium';
      canvas.dataset.tier = tier;
      scene.setTier(tier);
    } else {
      // Ya en el nivel medio: sin escena, para no castigar un equipo lento.
      queueMicrotask(() => {
        handle.dispose();
        options.onLow();
      });
    }
  });

  if (options.still) {
    scene.setYaw(0);
    return handle;
  }

  const beats = beatsFromSpecs(story.beats);
  root.dataset.live = '';
  if (root.dataset.mode === 'tap' && nav) {
    const prev = nav.querySelector<HTMLButtonElement>('[data-story-prev]')!;
    const next = nav.querySelector<HTMLButtonElement>('[data-story-next]')!;
    const count = nav.querySelector<HTMLElement>('[data-story-count]');
    nav.hidden = false;
    const stage = canvas.parentElement!;
    let first = true;
    const tap = createTapStory(scene, beats, {
      onBeat(index) {
        mark(beats[index].id ?? '');
        prev.disabled = index === 0;
        next.disabled = index === beats.length - 1;
        if (count) count.textContent = `${index + 1} de ${beats.length}`;
        // El lienzo queda fijo arriba: tras un toque, el texto del tramo se
        // acomoda justo debajo de él para que su titular no quede tapado.
        if (first) return void (first = false);
        const section = root.querySelector<HTMLElement>(`[data-beat="${beats[index].id}"]`);
        const gap = section ? section.getBoundingClientRect().top - stage.getBoundingClientRect().bottom : 0;
        if (Math.abs(gap) > 8) {
          const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
          window.scrollBy({ top: gap, behavior: reduced ? 'auto' : 'smooth' });
        }
      },
    });
    const back = () => tap.go(tap.index() - 1);
    const forward = () => tap.go(tap.index() + 1);
    prev.addEventListener('click', back);
    next.addEventListener('click', forward);
    runner = {
      dispose() {
        tap.dispose();
        prev.removeEventListener('click', back);
        next.removeEventListener('click', forward);
      },
    };
  } else {
    runner = createScrollStory(spacer, scene, beats, {
      onProgress: (progress) => mark(activeBeat(progress, beats)?.beat.id ?? ''),
    });
  }
  return handle;
}
