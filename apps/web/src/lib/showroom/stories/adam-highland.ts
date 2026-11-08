/**
 * Historia por scroll de la balanza Adam Highland (`models/adam-highland.ts`):
 * el equipo, su pantalla y teclado, el protector con el plato, una muestra que
 * se pesa, el asa para llevarla y la cotización.
 *
 * Las cifras no se escriben aquí: `{Etiqueta}` es el valor de la ficha con
 * fuente (`src/content/modelos/adam-highland.json`), que `ProductStory.astro`
 * sustituye al construir la página; el texto de cada nota sale del marcador de
 * la sala (`content/adam-highland.ts`, con `criterion()`).
 *
 * Como en `hielscher-up400st.ts`, cada beat fija el estado completo con `pose`
 * (marcha, giro y muestra en el plato) para que un salto de scroll deje la
 * escena como corresponde. La muestra la coloca el modelo al ver
 * `root.userData.sample = 1`: levanta la tapa, baja el platillo y la lectura
 * se asienta sola.
 */
import type { ProductStoryConfig, StoryScene } from '../productStory';
import { smoothstep } from '../scrollStory';

interface Pose {
  power: boolean;
  /** Giro sobre el de presentación, o `null` para el vaivén de reposo. */
  yaw: number | null;
  sample: boolean;
}

function pose(scene: StoryScene, p: Pose) {
  scene.setPower(p.power);
  scene.setLevel(null);
  scene.setYaw(p.yaw);
  scene.model.root.userData.sample = p.sample ? 1 : 0;
}

const on: Pose = { power: true, yaw: 0, sample: false };

export const story: ProductStoryConfig = {
  modelId: 'adam-highland',
  beats: [
    {
      id: 'hero',
      progressStart: 0,
      progressEnd: 0.16,
      cameraView: 'general',
      textHeadline: 'Precisión que se lleva',
      textBody: 'Balanza portátil Highland · {Capacidad}',
      // Encendida con «0.000 g», como en la fotografía; vuelta entera que termina en la pose de presentación.
      animationHook: (scene, t) => pose(scene, { power: true, yaw: smoothstep(t) * Math.PI * 2, sample: false }),
    },
    {
      id: 'pantalla',
      progressStart: 0.16,
      progressEnd: 0.34,
      cameraView: 'pantalla',
      textHeadline: 'Lectura clara, teclas directas',
      textBody: '{Pantalla}. Legibilidad {Legibilidad}.',
      animationHook: (scene) => pose(scene, on),
      overlayComponent: 'hotspot',
      overlayData: ['screen', 'keys'],
      overlayPlacement: { screen: 'top', keys: 'bottom' },
    },
    {
      id: 'protector',
      progressStart: 0.34,
      progressEnd: 0.52,
      cameraView: 'plato',
      textHeadline: 'El plato, a resguardo del aire',
      textBody: 'Protector de corrientes con tapa perforada sobre el plato. {Protección}.',
      animationHook: (scene) => pose(scene, on),
      overlayComponent: 'hotspot',
      overlayData: ['shield', 'pan'],
      overlayPlacement: { shield: 'left', pan: 'right' },
    },
    {
      id: 'muestra',
      progressStart: 0.52,
      progressEnd: 0.7,
      cameraView: 'muestra',
      textHeadline: 'Se pone la muestra, la lectura se asienta',
      textBody: 'Calibración: {Calibración}.',
      animationHook: (scene) => pose(scene, { ...on, sample: true }),
      overlayComponent: 'facts',
      overlayData: ['Capacidad', 'Legibilidad'],
    },
    {
      id: 'asa',
      progressStart: 0.7,
      progressEnd: 0.86,
      cameraView: 'asa',
      textHeadline: 'Con asa, para pesar donde haga falta',
      textBody: 'Del laboratorio a la planta o a terreno. Interfaces {Interfaces}.',
      animationHook: (scene) => pose(scene, { ...on, sample: true }),
      overlayComponent: 'hotspot',
      overlayData: ['handle'],
      overlayPlacement: { handle: 'top' },
    },
    {
      id: 'close',
      progressStart: 0.86,
      progressEnd: 1,
      cameraView: 'general',
      textHeadline: 'Ficha técnica',
      textBody: 'Especificaciones con su fuente, aplicaciones y descargas, a continuación.',
      animationHook: (scene) => pose(scene, { ...on, yaw: null, sample: true }),
      overlayComponent: 'cta',
    },
  ],
  specs: [
    { title: 'Capacidad y lectura', labels: ['Capacidad', 'Legibilidad', 'Pantalla'] },
    { title: 'Calibración y protección', labels: ['Calibración', 'Protección'] },
    { title: 'Conexión', labels: ['Interfaces'] },
  ],
  // Las aplicaciones y los accesorios salen de la ficha del modelo.
  applications: [],
  accessories: [],
  relatedModels: ['ohaus-pioneer-px', 'adam-pmb', 'adam-solis'],
  faqs: [],
};
