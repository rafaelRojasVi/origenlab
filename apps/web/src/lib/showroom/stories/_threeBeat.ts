/**
 * Historia por scroll de tres beats, común a los equipos sin guion propio:
 *
 *   hero    vuelta completa al equipo en la vista general, en reposo (en la
 *           página de modelo es la primera pantalla: su texto es la entrada)
 *   reveal  el equipo en marcha, la cámara en una vista propia del modelo (no
 *           la general del hero) y una nota sobre cada ancla (el texto de cada
 *           nota sale del marcador de la sala)
 *   close   en reposo, de tres cuartos por el otro lado (no el encuadre del
 *           hero), «Solicitar cotización» y paso a la ficha técnica
 *
 * Cada `stories/<id>.ts` es sólo la configuración (textos, vista, anclas y
 * grupos de la ficha) y su propio fragmento `scene-story-<id>`. Las cifras no se
 * escriben: `{Etiqueta}` en un texto es el valor de la ficha con fuente, que
 * `ProductStory.astro` sustituye al construir la página.
 *
 * Como en `hielscher-up400st.ts`, cada beat fija el estado completo (marcha,
 * nivel, giro) y no sólo lo que cambia, para que un salto de scroll o un
 * regreso al principio dejen la escena como corresponde. `extra` añade el
 * estado de las piezas propias de un equipo (la tapa de un analizador de
 * humedad) y corre después de la pose común.
 */
import type { ProductStoryConfig, SpecGroup, StoryScene } from '../productStory';
import { smoothstep } from '../scrollStory';

export type ThreeBeatStage = 'hero' | 'reveal' | 'close';

export interface ThreeBeatOptions {
  modelId: string;
  hero: { headline: string; body: string };
  reveal: {
    /** Vista con nombre del modelo (`model.views`) que encuadra las anclas; nunca `general`, que es la del hero. */
    view: string;
    /**
     * Factor sobre la distancia de esa vista: las del modelo se pensaron para la
     * sala, más estrecha que el lienzo de la historia, y de cerca dejan las anclas
     * fuera de cuadro. Sin valor, la vista tal cual.
     */
    scale?: number;
    /** Anclas con nota (2–3), con el nombre de `content/<id>.ts`. */
    anchors: string[];
    /**
     * Lado de la nota de cada ancla (`left`, `right`, `top`, `bottom`, con
     * `:px` de hilo): para que no tape lo que describe. Sin entrada, hacia
     * afuera del centro del lienzo.
     */
    placement?: Record<string, string>;
    headline: string;
    body: string;
  };
  close?: { headline: string; body: string };
  /** Toda la ficha del modelo, en grupos: la historia reemplaza a la ficha corriente de la página. */
  specs: SpecGroup[];
  relatedModels: string[];
  /** Estado propio del equipo en cada beat; `t` es el avance local 0–1. */
  extra?: (scene: StoryScene, stage: ThreeBeatStage, t: number) => void;
}

/** Giro del cierre sobre el de presentación, en radianes. */
const CLOSE_YAW = -0.9;

export function threeBeatStory(options: ThreeBeatOptions): ProductStoryConfig {
  const { modelId, hero, reveal, extra } = options;
  if (reveal.view === 'general') throw new Error(`${modelId}: el beat «reveal» necesita una vista propia del modelo, no la general del hero`);
  const close = options.close ?? {
    headline: 'Pida su cotización',
    body: 'Le enviamos una propuesta formal para su laboratorio. La ficha técnica con su fuente sigue a continuación.',
  };

  const pose = (scene: StoryScene, stage: ThreeBeatStage, t: number) => {
    scene.setPower(stage === 'reveal');
    scene.setLevel(null);
    // Vuelta entera: termina en la pose de presentación y el beat siguiente parte de ahí.
    // El cierre gira el equipo a tres cuartos por el otro lado: no repite el encuadre del hero.
    scene.setYaw(stage === 'hero' ? smoothstep(t) * Math.PI * 2 : stage === 'reveal' ? 0 : CLOSE_YAW);
    extra?.(scene, stage, t);
  };

  // La vista del beat 2, abierta por `scale` si hace falta (`StoryView.from`).
  const revealView = reveal.scale ? `${reveal.view}-historia` : reveal.view;
  return {
    modelId,
    views: reveal.scale ? { [revealView]: { from: reveal.view, scale: reveal.scale } } : undefined,
    beats: [
      {
        id: 'hero',
        progressStart: 0,
        progressEnd: 0.2,
        cameraView: 'general',
        textHeadline: hero.headline,
        textBody: hero.body,
        animationHook: (scene, t) => pose(scene, 'hero', t),
      },
      {
        id: 'reveal',
        progressStart: 0.2,
        progressEnd: 0.7,
        cameraView: revealView,
        textHeadline: reveal.headline,
        textBody: reveal.body,
        animationHook: (scene, t) => pose(scene, 'reveal', t),
        overlayComponent: 'hotspot',
        overlayData: reveal.anchors,
        overlayPlacement: reveal.placement,
      },
      {
        id: 'close',
        progressStart: 0.7,
        progressEnd: 1,
        cameraView: 'general',
        textHeadline: close.headline,
        textBody: close.body,
        animationHook: (scene, t) => pose(scene, 'close', t),
        overlayComponent: 'cta',
      },
    ],
    specs: options.specs,
    // Las aplicaciones y los accesorios salen de la ficha del modelo.
    applications: [],
    accessories: [],
    relatedModels: options.relatedModels,
    faqs: [],
  };
}
