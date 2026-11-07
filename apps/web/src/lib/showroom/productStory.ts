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
import { createShowroom, type AnchorPositions, type Showroom, type ShowroomOptions } from './scene';
import type { Beat } from './scrollStory';
import type { BuiltModel } from './types';

export type SonotrodeId = 'S24d7' | 'S24d14' | 'S24d22';

const SONOTRODES: readonly SonotrodeId[] = ['S24d7', 'S24d14', 'S24d22'];

export interface StoryScene extends Showroom {
  /**
   * El modelo montado, para las historias de equipos con piezas propias (la
   * tapa de un analizador de humedad: `model.root.userData.lidOpen`,
   * `setLidAngle`). Las historias genéricas no lo necesitan.
   */
  readonly model: BuiltModel;
  /** `Showroom.tweenTo`: `smoothstep` entre la cámara actual y la vista; 800 ms por defecto. */
  tweenTo(view: string, durationMs?: number): void;
  /**
   * Nivel de marcha 0–1 sin transición (burbujas, brillo, ondas). Es el nivel
   * del modelo, no el estado de la pantalla: ésa la enciende `setPower`. Se
   * publica también como `--story-level` en la raíz de los overlays.
   */
  setPowerLevel(level: number): void;
  /**
   * Muestra la pieza ancla con el nombre del sonotrodo y oculta las otras, y
   * lo anota como `data-sonotrode` en la raíz de los overlays. Si el modelo no
   * trae ancla para `id`, no cambia nada.
   */
  swapSonotrode(id: SonotrodeId): void;
  /**
   * Cifra de la pantalla como fracción 0–1 del rango que la raíz de los
   * overlays declara en `data-readout-from` / `data-readout-to` (sale de la
   * ficha con fuente, no del código); se escribe en la pantalla 3D y en cada
   * `[data-readout]`, y la fracción como `--story-readout` en la raíz. `null`
   * devuelve la pantalla a su animación propia.
   */
  setReadout(fraction: number | null): void;
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

export function createStoryScene(canvas: HTMLCanvasElement, model: BuiltModel, options: ShowroomOptions = {}): StoryScene {
  const showroom = createShowroom(canvas, model, options);

  let root: HTMLElement | null = null;
  let readouts: HTMLElement[] = [];
  let shownReadout = '';
  let shownSonotrode = '';
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
    model,
    onFrame(callback) {
      userFrame = callback;
    },
    setPowerLevel(level) {
      showroom.setLevel(level);
      root?.style.setProperty('--story-level', Math.min(1, Math.max(0, level)).toFixed(3));
    },
    swapSonotrode(id) {
      if (!model.anchors[id] || id === shownSonotrode) return;
      shownSonotrode = id;
      for (const name of SONOTRODES) {
        const piece = model.anchors[name];
        if (piece) piece.visible = name === id;
      }
      if (root) root.dataset.sonotrode = id;
    },
    setReadout(fraction) {
      if (fraction === null) {
        model.setReadout?.(null);
        return;
      }
      const f = Math.min(1, Math.max(0, fraction));
      const from = Number(root?.dataset.readoutFrom ?? 0);
      const to = Number(root?.dataset.readoutTo ?? 100);
      const value = Math.round(from + (to - from) * f);
      model.setReadout?.(value);
      root?.style.setProperty('--story-readout', f.toFixed(3));
      const text = `${value} %`;
      if (text === shownReadout) return;
      shownReadout = text;
      for (const el of readouts) el.textContent = text;
    },
    bindOverlays(next) {
      root = next;
      readouts = [...next.querySelectorAll<HTMLElement>('[data-readout]')];
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
  /**
   * Titular y línea del beat. `{Etiqueta}` se sustituye, al construir la
   * página, por el valor de la ficha del modelo con esa etiqueta (la de la
   * colección `modelos`, con fuente): las cifras no se escriben aquí. Una
   * etiqueta que la ficha no tenga detiene el build.
   */
  textHeadline?: string;
  textBody?: string;
  /**
   * Se llama en cada cuadro del beat con su avance local 0–1; puede mutar la
   * escena. Debe fijar todo el estado que el beat necesita (marcha, nivel,
   * giro, sonotrodo), no sólo lo que cambia: un salto de scroll puede saltarse
   * los beats intermedios sin llamar a su `tick`.
   */
  animationHook?: (scene: StoryScene, localT: number) => void;
  /**
   * Pieza HTML que acompaña al beat; `ProductStory.astro` la pinta:
   * `facts` cifras de la ficha (`overlayData`: etiquetas); `hotspot` una nota
   * sobre cada ancla de `overlayData` (una o varias; el texto sale del
   * marcador de la sala);
   * `meter` medidor de la cifra de pantalla (`overlayData`: [etiqueta con el
   * rango «del N al M %»]); `callout` nota sobre un ancla con un valor de la
   * ficha (`overlayData`: [ancla, etiqueta]); `sonotrodes` las puntas
   * (`overlayData`: ids); `pills` las aplicaciones de la ficha.
   */
  overlayComponent?: 'facts' | 'hotspot' | 'meter' | 'callout' | 'sonotrodes' | 'pills';
  overlayData?: string[];
}

/** Un grupo de la ficha técnica: título y etiquetas de la ficha del modelo, en orden. */
export interface SpecGroup {
  title: string;
  labels: string[];
}

export interface AccessorySpec {
  id: string;
  name: string;
  note?: string;
}

export interface ProductStoryConfig {
  /** Igual al nombre del archivo en `models/` y en `stories/`. */
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
