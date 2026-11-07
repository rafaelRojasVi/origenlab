/**
 * Mapa de scroll de las páginas de producto: convierte el recorrido de un
 * espaciador alto en un progreso 0–1 y lo reparte entre «beats» (tramos de la
 * narración con su propia cámara y animación).
 *
 * Sin three.js y sin acceso al DOM al importarlo: las funciones puras se
 * prueban en node (`scripts/test-scroll-story.mjs`) y `createScrollStory` sólo
 * toca `window` cuando se llama. El escenario (`StoryScene`) llega ya hecho;
 * aquí sólo se importa su tipo.
 *
 * Una sola escucha de scroll pasiva y un cuadro de animación por ráfaga: los
 * eventos de scroll sólo marcan «hay que recalcular».
 */
import type { BeatSpec, StoryScene } from './productStory';

export interface Beat {
  /** Sólo para depuración y para enlazar con el contenido de la página. */
  id?: string;
  /** Progreso en el que empieza el beat (0–1, incluido). */
  start: number;
  /** Progreso en el que termina (0–1, excluido; el último beat llega a 1 inclusive). */
  end: number;
  /** Una vez al entrar al beat, desde cualquiera de los dos lados. */
  enter?(scene: StoryScene, localT: number): void;
  /** En cada cuadro mientras el beat está activo; `localT` va de 0 a 1 dentro del beat. */
  tick?(scene: StoryScene, localT: number): void;
  /** Una vez al salir del beat. */
  leave?(scene: StoryScene): void;
}

export interface ActiveBeat {
  beat: Beat;
  localT: number;
}

export interface ScrollStoryOptions {
  /** Se llama con el progreso en cada cuadro, también con movimiento reducido. */
  onProgress?(progress: number): void;
}

export interface ScrollStory {
  /** Último progreso calculado. */
  progress(): number;
  /** Quita las escuchas y cancela el cuadro pendiente. */
  dispose(): void;
}

const clamp01 = (n: number) => Math.min(1, Math.max(0, n));

/**
 * Progreso 0–1 del recorrido de un espaciador.
 *
 * `scrollTop` es lo que ya se desplazó desde que el espaciador tocó el borde
 * superior de la ventana (`-rect.top`); el recorrido útil es la altura del
 * espaciador menos la ventana, porque la última pantalla ya no se mueve. Un
 * recorrido sin largo (espaciador colapsado) da 0.
 */
export function mapProgress(scrollTop: number, spacerHeight: number, viewport: number): number {
  const run = spacerHeight - viewport;
  if (!(run > 0)) return 0;
  const p = scrollTop / run;
  return Number.isFinite(p) ? clamp01(p) : 0;
}

/** Suavizado de entrada y salida; acotado a [0, 1]. */
export function smoothstep(t: number): number {
  const x = clamp01(t);
  return x * x * (3 - 2 * x);
}

/**
 * El beat cuyo `[start, end)` contiene el progreso, con el avance local. El
 * progreso 1 cae en el último beat que llega a 1. Entre beats sueltos, `null`.
 */
export function activeBeat(progress: number, beats: Beat[]): ActiveBeat | null {
  for (const beat of beats) {
    const inside = progress >= beat.start && (progress < beat.end || (beat.end >= 1 && progress <= 1));
    if (!inside) continue;
    const span = beat.end - beat.start;
    return { beat, localT: span > 0 ? clamp01((progress - beat.start) / span) : 1 };
  }
  return null;
}

export function createScrollStory(
  spacer: HTMLElement,
  scene: StoryScene,
  beats: Beat[],
  options: ScrollStoryOptions = {},
): ScrollStory {
  const reducedQuery = window.matchMedia?.('(prefers-reduced-motion: reduce)');
  let progress = 0;
  let current = -1; // índice del beat activo en `beats`
  let raf = 0;

  const step = (from: number, to: number) => {
    // Si el salto cruza beats intermedios, se recorren uno a uno (entrada y
    // salida) para que el estado acumulado de la escena —la pantalla encendida,
    // la vista— quede como si se hubiera pasado por ellos, hacia delante o atrás.
    const dir = to > from ? 1 : -1;
    for (let i = from; i !== to; i += dir) {
      if (i >= 0) beats[i].leave?.(scene);
      const next = i + dir;
      if (next >= 0) beats[next].enter?.(scene, dir > 0 ? 0 : 1);
    }
  };

  const frame = () => {
    raf = 0;
    const rect = spacer.getBoundingClientRect();
    progress = mapProgress(-rect.top, rect.height, window.innerHeight);
    options.onProgress?.(progress);
    // Con movimiento reducido el progreso se informa, pero la escena no se anima.
    if (!reducedQuery?.matches) {
      const hit = activeBeat(progress, beats);
      const next = hit ? beats.indexOf(hit.beat) : -1;
      if (next !== current) {
        step(current, next);
        current = next;
      }
      if (hit) hit.beat.tick?.(scene, hit.localT);
    }
    scene.renderOverlays(progress);
  };
  const request = () => {
    if (!raf) raf = window.requestAnimationFrame(frame);
  };

  window.addEventListener('scroll', request, { passive: true });
  window.addEventListener('resize', request, { passive: true });
  reducedQuery?.addEventListener?.('change', request);
  frame();

  return {
    progress: () => progress,
    dispose() {
      window.removeEventListener('scroll', request);
      window.removeEventListener('resize', request);
      reducedQuery?.removeEventListener?.('change', request);
      if (raf) window.cancelAnimationFrame(raf);
      raf = 0;
    },
  };
}

/**
 * De `BeatSpec` a `Beat` para `createScrollStory`: al entrar mueve la cámara a
 * la vista del beat y en cada cuadro corre su `animationHook`. Los ids y los
 * rangos se validan aquí, una vez, para que un error de configuración falle al
 * montar y no a mitad del scroll.
 */
export function beatsFromSpecs(specs: BeatSpec[]): Beat[] {
  let previousEnd = 0;
  return specs.map((spec) => {
    if (!(spec.progressStart >= previousEnd && spec.progressEnd > spec.progressStart && spec.progressEnd <= 1)) {
      throw new Error(`Beat «${spec.id}»: rango ${spec.progressStart}–${spec.progressEnd} fuera de orden o de 0–1`);
    }
    previousEnd = spec.progressEnd;
    return {
      id: spec.id,
      start: spec.progressStart,
      end: spec.progressEnd,
      enter: (scene) => {
        if (spec.cameraView) scene.tweenTo(spec.cameraView);
      },
      tick: (scene, localT) => spec.animationHook?.(scene, localT),
    };
  });
}
