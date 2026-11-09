/**
 * Historia por scroll del Hielscher UP100H: seis beats sobre el modelo 3D
 * (`models/hielscher-up100h.ts`), del equipo en reposo a sus dos perillas, la
 * cavitación en la punta y el uso a mano o en el soporte.
 *
 * Las cifras no se escriben aquí: `{Etiqueta}` es el valor de la ficha con
 * fuente (`src/content/modelos/hielscher-up100h.json`), que
 * `ProductStory.astro` sustituye al construir la página; el rango del medidor
 * de amplitud también sale de esa ficha y gira la perilla «Amplitude %».
 *
 * Como en `hielscher-up400st.ts`, cada `animationHook` fija el estado completo
 * de su beat (marcha, nivel, giro, perilla) para que un salto de scroll deje la
 * escena como corresponde.
 */
import type { ProductStoryConfig, StoryScene } from '../productStory';
import { smoothstep } from '../scrollStory';

interface Pose {
  power: boolean;
  /** Nivel de marcha 0–1, o `null` para que siga a la marcha. */
  level: number | null;
  /** Giro sobre el de presentación, o `null` para el vaivén de reposo. */
  yaw: number | null;
  /** Amplitud como fracción del rango de la ficha (gira la perilla), o `null`. */
  readout: number | null;
}

function pose(scene: StoryScene, p: Pose) {
  scene.setPower(p.power);
  if (p.level === null) scene.setLevel(null);
  else scene.setPowerLevel(p.level);
  scene.setYaw(p.yaw);
  scene.setReadout(p.readout);
}

const rest: Pose = { power: false, level: null, yaw: null, readout: null };
const running: Pose = { power: true, level: 0, yaw: 0, readout: null };

export const story: ProductStoryConfig = {
  modelId: 'hielscher-up100h',
  beats: [
    {
      id: 'hero',
      progressStart: 0,
      progressEnd: 0.2,
      cameraView: 'general',
      textHeadline: 'Ultrasonido en la mano',
      textBody: 'Procesador ultrasónico UP100H · {Potencia} · {Frecuencia}',
      // Vuelta entera: termina en la pose de presentación y el beat siguiente parte de ahí.
      animationHook: (scene, t) => pose(scene, { ...rest, yaw: smoothstep(t) * Math.PI * 2 }),
    },
    {
      id: 'panel',
      progressStart: 0.2,
      progressEnd: 0.36,
      cameraView: 'perillas',
      textHeadline: 'Dos perillas, sin menús',
      textBody: '«Cycle» fija el ciclo de pulso; «Amplitude %», la intensidad. Se ajustan a mano, con el equipo en marcha.',
      animationHook: (scene) => pose(scene, running),
      overlayComponent: 'hotspot',
      overlayData: ['cycle', 'amplitude'],
      overlayPlacement: { cycle: 'right', amplitude: 'right' },
    },
    {
      id: 'amplitude',
      progressStart: 0.36,
      progressEnd: 0.5,
      cameraView: 'perillas',
      textHeadline: 'La amplitud, en la perilla',
      textBody: 'Amplitud {Amplitud}: cuánto se desplaza la punta en cada vibración.',
      animationHook: (scene, t) => pose(scene, { ...running, level: 0, readout: t }),
      overlayComponent: 'meter',
      overlayData: ['Amplitud'],
    },
    {
      id: 'sonotrodo',
      progressStart: 0.5,
      progressEnd: 0.68,
      cameraView: 'sonotrodo',
      textHeadline: 'Donde sucede la cavitación',
      textBody: 'Sonotrodo de titanio intercambiable · {Volumen de muestra}.',
      animationHook: (scene, t) => pose(scene, { ...running, level: 0.3 + 0.7 * t, readout: 0.75 }),
      overlayComponent: 'hotspot',
      overlayData: ['tip'],
      overlayPlacement: { tip: 'right' },
    },
    {
      id: 'mano',
      progressStart: 0.68,
      progressEnd: 0.84,
      cameraView: 'agarre',
      textHeadline: 'A mano o en el soporte',
      textBody: 'La carcasa es el mango: se sostiene a mano sobre el vaso o se fija con una pinza. {Peso}, un solo cable.',
      animationHook: (scene) => pose(scene, { ...running, level: 0.5, yaw: null }),
      overlayComponent: 'hotspot',
      overlayData: ['grip'],
      overlayPlacement: { grip: 'left' },
    },
    {
      id: 'close',
      progressStart: 0.84,
      progressEnd: 1,
      cameraView: 'general',
      textHeadline: 'Ficha técnica',
      textBody: 'Especificaciones con su fuente, aplicaciones y descargas, a continuación.',
      animationHook: (scene) => pose(scene, rest),
      overlayComponent: 'cta',
    },
  ],
  specs: [
    { title: 'Potencia y frecuencia', labels: ['Potencia', 'Frecuencia', 'Amplitud'] },
    { title: 'Muestra y uso', labels: ['Volumen de muestra', 'Peso', 'Régimen de trabajo'] },
  ],
  // Las aplicaciones y los accesorios salen de la ficha del modelo.
  applications: [],
  accessories: [],
  relatedModels: ['hielscher-up200st', 'hielscher-up400st', 'hielscher-uip2000hdt'],
  faqs: [],
};
