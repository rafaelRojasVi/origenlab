/**
 * Escenario de la narración por scroll de una página de producto, y la forma
 * de su configuración.
 *
 * `StoryScene` es el `Showroom` de siempre (`scene.ts`: luz, cámara, vistas,
 * marcadores) más lo que necesita el scroll: nivel de marcha continuo, cambio
 * de sonotrodo y marcadores HTML proyectados. No hay un segundo escenario: se
 * envuelve `createShowroom`, que ahora expone `tweenTo` y `setLevel`.
 *
 * Importa three (vía `scene.ts`) y no se prueba en node, que no tiene WebGL; lo
 * cubre el QA de interacción en el navegador. Las funciones puras del mapa de
 * scroll viven aparte en `scrollStory.ts`, sin three, y sí se prueban.
 *
 * Sin acceso al DOM al importarlo.
 */
import { createShowroom, type AnchorPositions, type Showroom } from './scene';
import type { Beat } from './scrollStory';
import type { BuiltModel } from './types';

export type SonotrodeId = 'S24d7' | 'S24d14' | 'S24d22';

const SONOTRODES: readonly SonotrodeId[] = ['S24d7', 'S24d14', 'S24d22'];

export interface StoryScene extends Showroom {
  /** `Showroom.tweenTo`: `smoothstep` entre la cámara actual y la vista; 800 ms por defecto. */
  tweenTo(view: string, durationMs?: number): void;
  /**
   * Nivel de marcha 0–1 sin transición (burbujas, brillo, ondas). Es el nivel
   * del modelo, no el estado de la pantalla: ésa la enciende `setPower`.
   */
  setPowerLevel(level: number): void;
  /**
   * Muestra la pieza ancla con el nombre del sonotrodo y oculta las otras.
   * Si el modelo no trae ancla para `id`, no cambia nada.
   */
  swapSonotrode(id: SonotrodeId): void;
  /** Recoge los elementos `[data-anchor="nombre"]` de `root` para posicionarlos sobre las anclas. */
  bindOverlays(root: HTMLElement): void;
  /**
   * Coloca los elementos enlazados sobre su ancla en píxeles CSS del lienzo
   * (`transform`; `data-occluded="true"` si queda detrás) y publica el progreso
   * como `--story-progress` en el elemento raíz. El escenario también lo llama
   * en cada cuadro, así los marcadores siguen a la cámara entre scrolls.
   */
  renderOverlays(progress: number): void;
}

export function createStoryScene(canvas: HTMLCanvasElement, model: BuiltModel): StoryScene {
  const showroom = createShowroom(canvas, model);

  let root: HTMLElement | null = null;
  let overlays: { el: HTMLElement; anchor: string; at: string }[] = [];
  let positions: AnchorPositions = {};
  let userFrame: ((anchors: AnchorPositions) => void) | null = null;

  const apply = () => {
    for (const o of overlays) {
      const p = positions[o.anchor];
      const at = p ? `translate(${p.x.toFixed(1)}px, ${p.y.toFixed(1)}px)` : '';
      if (at !== o.at) {
        o.at = at;
        if (at) o.el.style.transform = at;
        o.el.dataset.occluded = p ? 'false' : 'true';
      }
    }
  };
  showroom.onFrame((anchors) => {
    positions = anchors;
    apply();
    userFrame?.(anchors);
  });

  return {
    ...showroom,
    onFrame(callback) {
      userFrame = callback;
    },
    setPowerLevel: (level) => showroom.setLevel(level),
    swapSonotrode(id) {
      if (!model.anchors[id]) return;
      for (const name of SONOTRODES) {
        const piece = model.anchors[name];
        if (piece) piece.visible = name === id;
      }
    },
    bindOverlays(next) {
      root = next;
      overlays = [...next.querySelectorAll<HTMLElement>('[data-anchor]')].map((el) => ({ el, anchor: el.dataset.anchor ?? '', at: '' }));
      apply();
    },
    renderOverlays(progress) {
      root?.style.setProperty('--story-progress', progress.toFixed(4));
      apply();
    },
  };
}

/* ── configuración de una historia ── */

export interface BeatSpec {
  id: string;
  /** Progreso 0–1 en el que empieza el beat. */
  progressStart: number;
  /** Progreso 0–1 en el que termina. */
  progressEnd: number;
  /** `general` o una vista del modelo; sin valor, el beat no mueve la cámara. */
  cameraView?: string;
  textHeadline?: string;
  textBody?: string;
  /** Se llama en cada cuadro del beat con su avance local 0–1; puede mutar la escena. */
  animationHook?: (scene: StoryScene, localT: number) => void;
  /** Nombre del componente Astro que pinta el overlay HTML de este beat. */
  overlayComponent?: string;
}

export interface SpecGroup {
  title: string;
  rows: { label: string; value: string }[];
}

export interface AccessorySpec {
  id: string;
  name: string;
  note?: string;
}

export interface ProductStoryConfig {
  modelId: string;
  beats: BeatSpec[];
  specs: SpecGroup[];
  applications: string[];
  accessories: AccessorySpec[];
  /** Ids de los modelos relacionados. */
  relatedModels: string[];
  faqs: { q: string; a: string }[];
  downloadsUrl?: string;
}

export { beatsFromSpecs } from './scrollStory';
