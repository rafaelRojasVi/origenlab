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

/** Distancia en px entre el ancla y su nota (el largo del hilo); en pantallas de toque, menor. */
const NOTE_GAP = 64;
const NOTE_GAP_TAP = 36;
/** Aire entre una nota y el borde del lienzo o los controles de toque. */
const NOTE_MARGIN = 8;

type NoteSide = 'left' | 'right' | 'top' | 'bottom';

/** Una nota sobre el lienzo y lo último que se calculó para ella. */
interface Note {
  el: HTMLElement;
  anchor: string;
  /** Lo último que se escribió en el elemento, para no repetirlo. */
  key: string;
  /** Posición del ancla y estado del beat con que se calculó, para saltarse cuadros sin cambio. */
  seenX: number;
  seenY: number;
  seenOn: boolean;
  seenShown: boolean;
  x: number;
  y: number;
  w: number;
  h: number;
  side: NoteSide;
  gap: number;
  on: boolean;
  shown: boolean;
  /** Estado `on` con que se midieron `w` y `h` (`null`: sin medir). */
  measuredOn: boolean | null;
  /** En pantallas de toque, lado impuesto por el orden vertical de las anclas. */
  forced: NoteSide | '';
}

const byY = (a: Note, b: Note) => a.y - b.y;

/** Largo mínimo del hilo: la nota nunca queda más cerca que esto de su punto. */
const MIN_LEAD = 12;

const SIDES: readonly NoteSide[] = ['left', 'right', 'top', 'bottom'];
const OPPOSITE: Record<NoteSide, NoteSide> = { left: 'right', right: 'left', top: 'bottom', bottom: 'top' };

/** Pone la nota (sin acotar) en `side` del punto, a `gap` px. */
function placeAt(n: Note, side: NoteSide, px: number, py: number, gap: number) {
  n.side = side;
  n.x = side === 'right' ? px + gap : side === 'left' ? px - gap - n.w : px - n.w / 2;
  n.y = side === 'top' ? py - gap - n.h : side === 'bottom' ? py + gap : py - n.h / 2;
}

/** Acota la nota al lienzo y por encima de `floorY`. */
function clampNote(n: Note, width: number, floorY: number) {
  n.x = Math.min(Math.max(n.x, 0), Math.max(0, width - n.w));
  n.y = Math.min(Math.max(n.y, 0), Math.max(0, floorY - n.h));
}

/** Lo que separa a la nota del punto en el eje de su lado (negativo: lo cubre). */
function leadLength(n: Note, side: NoteSide, px: number, py: number): number {
  return side === 'right' ? n.x - px : side === 'left' ? px - (n.x + n.w) : side === 'bottom' ? n.y - py : py - (n.y + n.h);
}

/** ¿Cabe la nota en `side` del punto, con `gap`, dentro del lienzo y sobre `floorY`? */
function fits(n: Note, side: NoteSide, px: number, py: number, gap: number, width: number, floorY: number): boolean {
  return side === 'right' ? px + gap + n.w <= width : side === 'left' ? px - gap - n.w >= 0 : side === 'top' ? py - gap - n.h >= 0 : py + gap + n.h <= floorY;
}

export function createStoryScene(canvas: HTMLCanvasElement, model: BuiltModel, options: ShowroomOptions = {}): StoryScene {
  const showroom = createShowroom(canvas, model, options);

  let root: HTMLElement | null = null;
  let readouts: HTMLElement[] = [];
  let shownReadout = '';
  let shownSonotrode = '';
  let overlays: Note[] = [];
  const stack: Note[] = [];
  let seenWidth = -1;
  let seenHeight = -1;
  let seenMode = '';
  // Medidas del lienzo, de los controles de toque y de cada nota: se toman sólo al
  // cambiar de tamaño, de modo o de beat, no en cada cuadro (leerlas tras escribir
  // `transform` forzaría un cálculo de estilo y de layout por nota).
  let stageWidth = -1;
  let stageHeight = -1;
  let navTop = Infinity;
  let remeasure = true;
  let stageObserver: ResizeObserver | null = null;
  let positions: AnchorPositions = {};
  let userFrame: ((anchors: AnchorPositions) => void) | null = null;

  /** ¿Taparía la nota, puesta en `side`, el ancla de otra nota visible? */
  const coversOtherAnchor = (n: Note, side: NoteSide, px: number, py: number, gap: number): boolean => {
    placeAt(n, side, px, py, gap);
    for (const o of overlays) {
      const q = positions[o.anchor];
      if (o === n || !q || !o.on) continue;
      if (q.x > n.x - 6 && q.x < n.x + n.w + 6 && q.y > n.y - 6 && q.y < n.y + n.h + 6) return true;
    }
    return false;
  };

  /**
   * Coloca cada nota a un lado de su ancla y no encima, con un hilo entre la
   * nota y el punto. El lado sale de `data-place` (`left`, `right`, `top` o
   * `bottom`, con `:gap` opcional; lo fija la historia) o, sin él, es el eje en
   * que el ancla más se aleja del centro del lienzo, hacia afuera. Si ese lado
   * no tiene sitio se usa el opuesto y, si tampoco, arriba o abajo. La nota
   * nunca sale del lienzo (x e y se acotan) ni cubre los controles de toque
   * (`[data-story-nav]`, en pantallas estrechas, con su aire). Las notas
   * visibles de un mismo lado se apilan para no taparse entre sí. `data-side`,
   * `--gap` (largo real del hilo), `--lead-x` y `--lead-y` (dónde cae el ancla
   * respecto de la nota) los usa el CSS del hilo y del punto.
   *
   * Corre una vez por cuadro de la escena, pero sólo recalcula si algún ancla
   * se movió más de 1 px, cambió el conjunto de notas visibles o el tamaño del
   * lienzo; y sólo mide (layout) si cambió el tamaño, el modo o el beat.
   */
  const apply = () => {
    const stage = overlays[0]?.el.parentElement;
    if (!stage) return;
    const mode = root?.dataset.mode ?? '';
    if (mode !== seenMode) remeasure = true;
    if (remeasure || stageWidth < 0) {
      stageWidth = stage.clientWidth;
      stageHeight = stage.clientHeight;
    }
    const width = stageWidth;
    const height = stageHeight;
    let changed = remeasure || width !== seenWidth || height !== seenHeight || mode !== seenMode;
    for (const n of overlays) {
      const p = positions[n.anchor];
      const shown = Boolean(p);
      const on = n.el.hasAttribute('data-on');
      if (shown !== n.seenShown || on !== n.seenOn || (p && (Math.abs(p.x - n.seenX) > 1 || Math.abs(p.y - n.seenY) > 1))) changed = true;
    }
    if (!changed) return;
    seenWidth = width;
    seenHeight = height;
    seenMode = mode;

    const tap = mode === 'tap';
    if (remeasure) {
      const nav = tap ? root?.querySelector<HTMLElement>('[data-story-nav]:not([hidden])') : null;
      navTop = nav ? nav.offsetTop : Infinity;
    }
    // Franja inferior de los controles de toque: de su borde superior (menos aire) hacia abajo.
    const floorY = navTop !== Infinity ? Math.min(height, navTop) - NOTE_MARGIN : height;
    const baseGap = tap ? NOTE_GAP_TAP : NOTE_GAP;

    stack.length = 0;
    for (const n of overlays) {
      const p = positions[n.anchor];
      n.seenShown = Boolean(p);
      n.seenOn = n.on = n.el.hasAttribute('data-on');
      n.shown = Boolean(p);
      n.forced = '';
      if (!p) {
        if (n.el.dataset.occluded !== 'true') n.el.dataset.occluded = 'true';
        continue;
      }
      n.seenX = p.x;
      n.seenY = p.y;
      if (n.on) stack.push(n);
    }
    // Lienzo estrecho: sin sitio a los lados, la nota del ancla más alta va arriba y
    // las demás abajo, y así ninguna cae sobre el ancla de otra.
    if (tap && stack.length > 1) {
      stack.sort((a, b) => a.seenY - b.seenY);
      stack.forEach((n, i) => (n.forced = i < stack.length / 2 ? 'top' : 'bottom'));
    }
    for (const n of overlays) {
      const p = positions[n.anchor];
      if (!p) continue;
      if (n.el.dataset.occluded !== 'false') n.el.dataset.occluded = 'false';
      if (remeasure || n.measuredOn !== n.on) {
        n.measuredOn = n.on;
        n.w = n.el.offsetWidth;
        n.h = n.el.offsetHeight;
      }
      const hint = (n.el.dataset.place ?? '').split(':');
      const gap = Math.min(Number(hint[1]) || baseGap, tap ? NOTE_GAP_TAP + 8 : Infinity);
      let side = (n.forced || hint[0]) as NoteSide;
      if (side !== 'left' && side !== 'right' && side !== 'top' && side !== 'bottom') {
        const dx = width ? (p.x - width / 2) / width : 0;
        const dy = height ? (p.y - height / 2) / height : 0;
        side = Math.abs(dx) >= Math.abs(dy) ? (dx < 0 ? 'left' : 'right') : dy < 0 ? 'top' : 'bottom';
      }
      n.gap = gap;
      // Lado pedido; si no cabe con su hilo entero, con un hilo más corto (hasta MIN_LEAD)
      // y, si tampoco, el opuesto y luego los otros dos, en ese orden.
      const order = side === 'left' || side === 'right' ? [side, OPPOSITE[side], 'top', 'bottom'] : [side, OPPOSITE[side], p.x < width / 2 ? 'right' : 'left', p.x < width / 2 ? 'left' : 'right'];
      let chosen: NoteSide = side;
      let chosenGap = gap;
      search: for (const candidate of order as NoteSide[]) {
        for (const g of [gap, Math.min(gap, 24), MIN_LEAD]) {
          if (fits(n, candidate, p.x, p.y, g, width, floorY) && !coversOtherAnchor(n, candidate, p.x, p.y, g)) {
            chosen = candidate;
            chosenGap = g;
            break search;
          }
        }
      }
      placeAt(n, chosen, p.x, p.y, chosenGap);
      n.gap = chosenGap;
    }
    // Acota cada nota al lienzo y fuera de la franja de controles.
    stack.length = 0;
    for (const n of overlays) {
      if (!n.shown) continue;
      n.x = Math.min(Math.max(n.x, 0), Math.max(0, width - n.w));
      n.y = Math.min(Math.max(n.y, 0), Math.max(0, floorY - n.h));
      if (n.on) stack.push(n);
    }
    // Las notas visibles que se pisan en horizontal se apilan hacia abajo; si la
    // pila no cabe, se sube desde el fondo.
    stack.sort(byY);
    for (let i = 1; i < stack.length; i++) {
      for (let j = 0; j < i; j++) {
        if (stack[j].x < stack[i].x + stack[i].w && stack[i].x < stack[j].x + stack[j].w) stack[i].y = Math.max(stack[i].y, stack[j].y + stack[j].h + NOTE_MARGIN);
      }
    }
    for (let i = stack.length - 1; i >= 0; i--) {
      stack[i].y = Math.min(stack[i].y, Math.max(0, floorY - stack[i].h));
      for (let j = 0; j < i; j++) {
        if (stack[j].x < stack[i].x + stack[i].w && stack[i].x < stack[j].x + stack[j].w) stack[j].y = Math.min(stack[j].y, stack[i].y - stack[j].h - NOTE_MARGIN);
      }
    }
    for (const n of overlays) {
      if (!n.shown) continue;
      const p = positions[n.anchor]!;
      // Tras acotar y apilar, la nota no puede cubrir su propio punto ni quedar a menos
      // de MIN_LEAD: se pasa al lado más cercano que lo cumpla (o al de más hilo).
      if (leadLength(n, n.side, p.x, p.y) < MIN_LEAD) {
        const fromX = n.x;
        const fromY = n.y;
        let best: NoteSide | null = null;
        let feasible = false;
        let bestCost = Infinity;
        let bestLead = -Infinity;
        for (const side of SIDES) {
          placeAt(n, side, p.x, p.y, Math.max(n.gap, MIN_LEAD));
          clampNote(n, width, floorY);
          const lead = leadLength(n, side, p.x, p.y);
          const cost = Math.abs(n.x - fromX) + Math.abs(n.y - fromY);
          if (lead >= MIN_LEAD) {
            if (!feasible || cost < bestCost) {
              best = side;
              feasible = true;
              bestCost = cost;
            }
          } else if (!feasible && lead > bestLead) {
            best = side;
            bestLead = lead;
          }
        }
        placeAt(n, best ?? n.side, p.x, p.y, Math.max(n.gap, MIN_LEAD));
        clampNote(n, width, floorY);
      }
      const x = n.x;
      const y = Math.max(0, n.y);
      const at = `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px)`;
      // El hilo mide lo que de verdad separa a la nota del punto tras acotarla.
      const len = leadLength(n, n.side, p.x, p.y);
      const leadY = Math.min(n.h - 8, Math.max(8, p.y - y));
      const leadX = Math.min(n.w - 8, Math.max(8, p.x - x));
      const key = `${at}|${n.side}|${len.toFixed(0)}|${leadY.toFixed(0)}|${leadX.toFixed(0)}`;
      if (key === n.key) continue;
      n.key = key;
      const el = n.el;
      el.style.transform = at;
      el.dataset.side = n.side;
      el.style.setProperty('--gap', `${Math.max(0, len).toFixed(0)}px`);
      el.style.setProperty('--lead-y', `${leadY.toFixed(1)}px`);
      el.style.setProperty('--lead-x', `${leadX.toFixed(1)}px`);
    }
    remeasure = false;
  };

  showroom.onFrame((anchors) => {
    positions = anchors;
    apply();
    userFrame?.(anchors);
  });

  return {
    ...showroom,
    model,
    dispose(discard) {
      stageObserver?.disconnect();
      stageObserver = null;
      showroom.dispose(discard);
    },
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
      // Otra cifra puede cambiar el ancho de la nota que la lleva.
      if (readouts.length) remeasure = true;
    },
    bindOverlays(next) {
      root = next;
      readouts = [...next.querySelectorAll<HTMLElement>('[data-readout]')];
      overlays = [...next.querySelectorAll<HTMLElement>('[data-anchor]')].map((el) => ({
        el,
        anchor: el.dataset.anchor ?? '',
        key: '',
        seenX: NaN,
        seenY: NaN,
        seenOn: false,
        seenShown: false,
        x: 0,
        y: 0,
        w: 0,
        h: 0,
        side: 'right' as NoteSide,
        gap: 0,
        on: false,
        shown: false,
        measuredOn: null,
        forced: '' as const,
      }));
      seenWidth = -1;
      remeasure = true;
      stageObserver?.disconnect();
      const stage = overlays[0]?.el.parentElement;
      stageObserver = stage ? new ResizeObserver(() => (remeasure = true)) : null;
      if (stage) stageObserver!.observe(stage);
      apply();
    },
    renderOverlays(progress) {
      root?.style.setProperty('--story-progress', progress.toFixed(4));
      // Pasado el último tramo la historia termina: la ficha vuelve a tener la barra fija.
      // Con histéresis (se enciende en 0,999, se apaga bajo 0,99) para que no parpadee en el borde.
      if (progress >= 0.999) root?.setAttribute('data-end', '');
      else if (progress < 0.99) root?.removeAttribute('data-end');
      // Las notas las coloca el cuadro de la escena (una vez por cuadro), no el scroll.
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
   * `cta` el botón «Solicitar cotización» de la página; `meter` medidor de la cifra de pantalla (`overlayData`: [etiqueta con el
   * rango «del N al M %»]); `callout` nota sobre un ancla con un valor de la
   * ficha (`overlayData`: [ancla, etiqueta]); `sonotrodes` las puntas
   * (`overlayData`: ids); `pills` las aplicaciones de la ficha.
   */
  overlayComponent?: 'facts' | 'cta' | 'hotspot' | 'meter' | 'callout' | 'sonotrodes' | 'pills';
  overlayData?: string[];
  /**
   * Para `hotspot`: dónde va la nota de cada ancla respecto de su punto,
   * `left`, `right`, `top` o `bottom`, con `:px` de hilo opcional
   * (`{ display: 'left:64' }`). Sin entrada, la nota va hacia afuera del centro
   * del lienzo. Sirve para que la nota no tape lo que describe.
   */
  overlayPlacement?: Record<string, string>;
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
