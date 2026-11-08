/**
 * Historia por scroll del OHAUS MB32: cinco beats sobre el modelo 3D
 * (`models/ohaus-mb32.ts`), con la forma de `hielscher-up400st.ts`.
 *
 *   hero       el equipo en reposo, tapa cerrada, pantalla en 0.000 g
 *   metodo     pantalla y perilla: la perilla gira y la segunda línea cambia de programa
 *   tapa       en marcha: la tapa se abre y queda abierta; notas sobre la tapa y el plato
 *   calefactor las varillas de fibra de carbono al rojo; la humedad sube en pantalla
 *   resultado  la cifra final en %MC y «Solicitar cotización»
 *
 * Las cifras no se escriben aquí: `{Etiqueta}` es el valor de la ficha con
 * fuente (`src/content/modelos/ohaus-mb32.json`) y el texto de cada nota sale
 * del marcador de la sala (`content/ohaus-mb32.ts`, con `criterion()`).
 *
 * Cada beat fija el estado completo con `pose` (marcha, giro, perilla, avance
 * del secado), para que un salto de scroll deje la escena como corresponde.
 */
import type { ProductStoryConfig, StoryScene } from '../productStory';
import type { Mb32Model } from '../models/ohaus-mb32';
import { smoothstep } from '../scrollStory';

interface Pose {
  power: boolean;
  /** Giro sobre el de presentación, o `null` para el vaivén de reposo. */
  yaw: number | null;
  /** Giro de la perilla en radianes, o `null` en reposo. */
  knob: number | null;
  /** Avance del secado 0–1, o `null` para el reloj propio del modelo. */
  drying: number | null;
}

function pose(scene: StoryScene, p: Pose) {
  scene.setPower(p.power);
  scene.setLevel(null);
  scene.setYaw(p.yaw);
  const model = scene.model as Mb32Model;
  model.setLidAngle(null);
  model.setKnob(p.knob);
  model.setDrying(p.drying);
}

const rest: Pose = { power: false, yaw: null, knob: null, drying: null };
const running: Pose = { power: true, yaw: 0, knob: null, drying: 0 };

export const story: ProductStoryConfig = {
  modelId: 'ohaus-mb32',
  beats: [
    {
      id: 'hero',
      progressStart: 0,
      progressEnd: 0.16,
      cameraView: 'general',
      textHeadline: 'Humedad, sin complicaciones',
      textBody: 'Analizador de humedad MB32 · capacidad {Capacidad}',
      animationHook: (scene) => pose(scene, rest),
    },
    {
      id: 'metodo',
      progressStart: 0.16,
      progressEnd: 0.36,
      cameraView: 'pantalla',
      textHeadline: 'Una perilla, dos programas',
      textBody: 'Programas: {Programas}. La perilla bajo la pantalla los elige.',
      // Un giro y medio: la segunda línea pasa por los dos programas.
      animationHook: (scene, t) => pose(scene, { ...rest, yaw: 0, knob: smoothstep(t) * Math.PI * 1.5 }),
      overlayComponent: 'hotspot',
      overlayData: ['pantalla', 'perilla'],
      // Las notas no tapan la cifra ni la perilla.
      overlayPlacement: { pantalla: 'top', perilla: 'bottom' },
    },
    {
      id: 'tapa',
      progressStart: 0.36,
      progressEnd: 0.56,
      cameraView: 'tapa',
      textHeadline: 'La tapa se abre, la muestra al plato',
      textBody: 'Capacidad {Capacidad}, sobre el plato de la cámara de secado.',
      animationHook: (scene) => pose(scene, running),
      overlayComponent: 'hotspot',
      overlayData: ['tapa', 'plato'],
    },
    {
      id: 'calefactor',
      progressStart: 0.56,
      progressEnd: 0.78,
      cameraView: 'calefactor',
      textHeadline: 'Calor de fibra de carbono',
      textBody: 'Calentamiento: {Calentamiento}.',
      animationHook: (scene, t) => pose(scene, { ...running, drying: 0.85 * t }),
      overlayComponent: 'hotspot',
      overlayData: ['calefactor'],
    },
    {
      id: 'resultado',
      progressStart: 0.78,
      progressEnd: 1,
      cameraView: 'pantalla',
      textHeadline: 'El resultado, en %MC',
      textBody: 'Legibilidad {Legibilidad}. Salidas: {Interfaces}.',
      animationHook: (scene, t) => pose(scene, { ...running, drying: 0.85 + 0.15 * smoothstep(Math.min(1, t * 2)) }),
      overlayComponent: 'cta',
    },
  ],
  specs: [
    { title: 'Medición', labels: ['Tipo', 'Capacidad', 'Legibilidad', 'Calentamiento'] },
    { title: 'Programas y conexión', labels: ['Programas', 'Interfaces'] },
  ],
  // Las aplicaciones y los accesorios salen de la ficha del modelo.
  applications: [],
  accessories: [],
  relatedModels: ['ohaus-mb120', 'ohaus-pioneer-px'],
  faqs: [],
};
