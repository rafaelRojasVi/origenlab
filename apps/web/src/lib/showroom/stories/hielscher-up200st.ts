/**
 * Historia por scroll del Hielscher UP200St: siete beats sobre el modelo 3D
 * (`models/hielscher-up200st.ts`), al estilo de la del UP400St: el conjunto
 * entero, la pantalla táctil del generador, el transductor en la mordaza, la
 * amplitud en la pantalla, la cavitación en la muestra, los sonotrodos
 * intercambiables y la cotización.
 *
 * Las cifras no se escriben aquí: `{Etiqueta}` es el valor de la ficha con
 * fuente (`src/content/modelos/hielscher-up200st.json`), que
 * `ProductStory.astro` sustituye al construir la página.
 *
 * Cada `animationHook` fija el estado completo de su beat con `pose`, como en
 * el UP400St, para que un salto de scroll deje la escena como corresponde. El
 * sonotrodo visible se elige con `model.root.userData.sonotrode` (S26…), que
 * el modelo lee en cada cuadro: `swapSonotrode` sólo conoce los del UP400St.
 */
import type { ProductStoryConfig, StoryScene } from '../productStory';
import type { Up200Sonotrode } from '../models/hielscher-up200st';

interface Pose {
  power: boolean;
  /** Nivel de marcha 0–1, o `null` para que siga a la marcha. */
  level: number | null;
  /** Giro sobre el de presentación, o `null` para el vaivén de reposo. */
  yaw: number | null;
  sonotrode: Up200Sonotrode;
  /** Cifra de pantalla como fracción del rango de la ficha, o `null`. */
  readout: number | null;
}

function pose(scene: StoryScene, p: Pose) {
  scene.setPower(p.power);
  if (p.level === null) scene.setLevel(null);
  else scene.setPowerLevel(p.level);
  scene.setYaw(p.yaw);
  scene.model.root.userData.sonotrode = p.sonotrode;
  scene.setReadout(p.readout);
}

const rest: Pose = { power: false, level: null, yaw: null, sonotrode: 'S26d14', readout: null };
const running: Pose = { power: true, level: 0.2, yaw: 0, sonotrode: 'S26d14', readout: null };

export const story: ProductStoryConfig = {
  modelId: 'hielscher-up200st',
  beats: [
    {
      id: 'hero',
      progressStart: 0,
      progressEnd: 0.14,
      cameraView: 'general',
      textHeadline: 'Sonicación de laboratorio',
      textBody: 'Procesador ultrasónico UP200St · {Potencia} · {Frecuencia}',
      animationHook: (scene) => pose(scene, rest),
      overlayComponent: 'facts',
      overlayData: ['Potencia', 'Frecuencia', 'Volumen de muestra'],
    },
    {
      id: 'screen',
      progressStart: 0.14,
      progressEnd: 0.3,
      cameraView: 'pantalla',
      textHeadline: 'El generador, con pantalla táctil',
      textBody: '{Registro}.',
      animationHook: (scene) => pose(scene, running),
      overlayComponent: 'hotspot',
      overlayData: ['screen'],
      overlayPlacement: { screen: 'left:72' },
    },
    {
      id: 'transducer',
      progressStart: 0.3,
      progressEnd: 0.44,
      cameraView: 'transductor',
      textHeadline: 'El transductor, en el soporte',
      textBody: 'Separado del generador y sujeto a la varilla con una mordaza. Protección: {Protección}.',
      animationHook: (scene) => pose(scene, { ...running, level: 0 }),
      overlayComponent: 'hotspot',
      overlayData: ['transducer'],
      overlayPlacement: { transducer: 'left:72' },
    },
    {
      id: 'amplitude',
      progressStart: 0.44,
      progressEnd: 0.58,
      cameraView: 'pantalla',
      textHeadline: 'La amplitud, a la vista',
      textBody: 'Amplitud {Amplitud}, ajustada y registrada desde la misma pantalla.',
      animationHook: (scene, t) => pose(scene, { ...running, level: 0.2 + 0.5 * t, readout: t }),
      overlayComponent: 'meter',
      overlayData: ['Amplitud'],
    },
    {
      id: 'cavitation',
      progressStart: 0.58,
      progressEnd: 0.74,
      cameraView: 'sonotrodo',
      textHeadline: 'Donde sucede la cavitación',
      textBody: 'La punta vibra dentro de la muestra y la mezcla. Volumen de muestra: {Volumen de muestra}.',
      animationHook: (scene, t) => pose(scene, { ...running, level: 0.3 + 0.7 * t }),
      overlayComponent: 'hotspot',
      overlayData: ['tip'],
      overlayPlacement: { tip: 'right:72' },
    },
    {
      id: 'swap',
      progressStart: 0.74,
      progressEnd: 0.88,
      cameraView: 'sonotrodo',
      textHeadline: 'Sonotrodos intercambiables',
      textBody: 'La misma unidad acepta puntas de distinto diámetro, según el volumen de la muestra.',
      animationHook: (scene, t) =>
        pose(scene, {
          ...running,
          level: 0.45,
          yaw: 0.3 * Math.sin(Math.PI * t),
          sonotrode: t < 1 / 3 ? 'S26d2' : t < 2 / 3 ? 'S26d7' : 'S26d14',
        }),
      overlayComponent: 'sonotrodes',
      overlayData: ['S26d2', 'S26d7', 'S26d14'],
    },
    {
      id: 'close',
      progressStart: 0.88,
      progressEnd: 1,
      cameraView: 'general',
      textHeadline: 'Cotice el UP200St',
      textBody: 'Especificaciones con su fuente y accesorios, a continuación.',
      animationHook: (scene) => pose(scene, { ...running, level: 0, yaw: null }),
      overlayComponent: 'cta',
    },
  ],
  specs: [
    { title: 'Potencia y frecuencia', labels: ['Potencia', 'Frecuencia', 'Amplitud'] },
    { title: 'Muestra y protección', labels: ['Volumen de muestra', 'Protección'] },
    { title: 'Control', labels: ['Registro'] },
  ],
  // Las aplicaciones salen de la ficha del modelo (`applications`).
  applications: [],
  // Sin lista propia: la página usa los accesorios con fuente de la ficha
  // (soporte ST1-16 y sonotrodos S26d2 a S26d40).
  accessories: [],
  relatedModels: ['hielscher-up400st', 'hielscher-up100h', 'hielscher-uip2000hdt'],
  faqs: [],
};
