/**
 * Historia por scroll de la Ortoalresa Digicen 22 R: cinco beats sobre el
 * modelo 3D (`models/ortoalresa-digicen-22-r.ts`), con la forma de `ohaus-mb32.ts`.
 *
 *   hero      en reposo, tapa cerrada, una vuelta entera al equipo
 *   rotor     en marcha: la tapa se abre y el rotor basculante gira; las
 *             cubetas se abren con la velocidad. Notas sobre la tapa y el rotor
 *   pantalla  la pantalla TFT con RPM, RCF, tiempo y temperatura en marcha
 *   lateral   la rejilla de refrigeración del costado
 *   close     en reposo (el rotor frena y la tapa se cierra), de tres cuartos
 *             por el otro lado, «Solicitar cotización»
 *
 * Las cifras no se escriben aquí: `{Etiqueta}` es el valor de la ficha con
 * fuente (`src/content/modelos/ortoalresa-digicen-22-r.json`) y el texto de
 * cada nota sale del marcador de la sala (`content/ortoalresa-digicen-22-r.ts`).
 *
 * Cada beat fija el estado completo con `pose` (marcha, giro, tapa), para que
 * un salto de scroll deje la escena como corresponde.
 */
import type { ProductStoryConfig, StoryScene } from '../productStory';
import type { DigicenModel } from '../models/ortoalresa-digicen-22-r';
import { smoothstep } from '../scrollStory';

/** Giro del cierre sobre el de presentación, en radianes. */
const CLOSE_YAW = -0.9;

function pose(scene: StoryScene, power: boolean, yaw: number | null) {
  scene.setPower(power);
  scene.setLevel(null);
  scene.setYaw(yaw);
  // La tapa sigue a la marcha: se abre al centrifugar y se cierra cuando el rotor se detiene.
  (scene.model as DigicenModel).setLid?.(null);
}

export const story: ProductStoryConfig = {
  modelId: 'ortoalresa-digicen-22-r',
  // Las vistas del modelo se pensaron para la sala, más estrecha que el lienzo de la historia.
  views: {
    'rotor-historia': { from: 'rotor', scale: 1.5 },
    'pantalla-historia': { from: 'pantalla', scale: 2.2 },
    'lateral-historia': { from: 'lateral', scale: 1.25 },
  },
  beats: [
    {
      id: 'hero',
      progressStart: 0,
      progressEnd: 0.18,
      cameraView: 'general',
      textHeadline: 'Centrifugación refrigerada',
      textBody: 'Centrífuga universal Digicen 22 R · {Velocidad máxima}',
      // Vuelta entera: termina en la pose de presentación y el beat siguiente parte de ahí.
      animationHook: (scene, t) => pose(scene, false, smoothstep(t) * Math.PI * 2),
    },
    {
      id: 'rotor',
      progressStart: 0.18,
      progressEnd: 0.42,
      cameraView: 'rotor-historia',
      textHeadline: 'La tapa se abre, el rotor gira',
      textBody: 'Rotor basculante de cuatro cubetas. Capacidad: {Capacidad máxima}.',
      animationHook: (scene) => pose(scene, true, 0),
      overlayComponent: 'hotspot',
      overlayData: ['lid', 'rotor'],
    },
    {
      id: 'pantalla',
      progressStart: 0.42,
      progressEnd: 0.6,
      cameraView: 'pantalla-historia',
      textHeadline: 'Todo en la pantalla táctil',
      textBody: 'Pantalla: {Pantalla}. Velocidad, fuerza g, tiempo y temperatura a la vista.',
      animationHook: (scene) => pose(scene, true, 0),
      overlayComponent: 'hotspot',
      overlayData: ['screen'],
    },
    {
      id: 'lateral',
      progressStart: 0.6,
      progressEnd: 0.78,
      cameraView: 'lateral-historia',
      textHeadline: 'Frío en la cámara',
      textBody: 'Temperatura: {Temperatura}. Refrigerante: {Refrigerante}.',
      animationHook: (scene) => pose(scene, true, 0),
      overlayComponent: 'hotspot',
      overlayData: ['body'],
    },
    {
      id: 'close',
      progressStart: 0.78,
      progressEnd: 1,
      cameraView: 'general',
      textHeadline: 'Pida su cotización',
      textBody: 'Le enviamos una propuesta formal para su laboratorio. La ficha técnica con su fuente sigue a continuación.',
      animationHook: (scene) => pose(scene, false, CLOSE_YAW),
      overlayComponent: 'cta',
    },
  ],
  specs: [
    { title: 'Capacidad y velocidad', labels: ['Capacidad máxima', 'Velocidad máxima', 'Rotor', 'Motor', 'Nivel de ruido'] },
    { title: 'Refrigeración', labels: ['Versión', 'Temperatura', 'Temperatura a máximas RPM', 'Pre-enfriamiento', 'Sensor', 'Refrigerante'] },
    { title: 'Control y cámara', labels: ['Pantalla', 'Conectividad', 'Accesorios', 'Cámara'] },
    { title: 'Instalación', labels: ['Dimensiones (CE 259 / CE 260)', 'Peso neto', 'Alimentación CE 259', 'Alimentación CE 260'] },
  ],
  // Las aplicaciones y los accesorios salen de la ficha del modelo.
  applications: [],
  accessories: [],
  relatedModels: ['ortoalresa-digicen-22', 'ortoalresa-biocen-22-r'],
  faqs: [],
};
