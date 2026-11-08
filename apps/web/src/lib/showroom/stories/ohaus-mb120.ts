/**
 * Historia por scroll del OHAUS MB120: seis beats sobre el modelo 3D
 * (`models/ohaus-mb120.ts`), con la forma de `hielscher-up400st.ts`.
 *
 *   hero      vuelta al equipo cerrado, en reposo
 *   screen    la pantalla táctil y su biblioteca de métodos
 *   lid       la tapa se abre: el plato con la muestra
 *   heat      en marcha: la lámpara halógena calienta
 *   dry       la humedad baja en la pantalla hasta el resultado
 *   close     vuelta al reposo y «Solicitar cotización»
 *
 * Las cifras no se escriben aquí: `{Etiqueta}` es el valor de la ficha con
 * fuente, y el texto de cada nota sale del marcador de la sala
 * (`content/ohaus-mb120.ts`, con `criterion()`).
 *
 * Cada beat fija el estado completo con `pose` (marcha, giro, tapa, secado):
 * un salto de scroll se salta los beats intermedios sin llamar a su `tick`.
 * La tapa y el secado son piezas propias del modelo: `root.userData.lidOpen`
 * (fracción 0–1; `undefined` = la tapa sigue a la marcha) y
 * `root.userData.dry` (avance 0–1; `undefined` = secado automático).
 */
import type { ProductStoryConfig, StoryScene } from '../productStory';
import { smoothstep } from '../scrollStory';

interface Pose {
  power: boolean;
  /** Giro sobre el de presentación, o `null` para el vaivén de reposo. */
  yaw: number | null;
  /** Apertura de la tapa 0–1, o `undefined` para que siga a la marcha. */
  lid?: number;
  /** Avance del secado 0–1, o `undefined` para la animación propia. */
  dry?: number;
}

function pose(scene: StoryScene, p: Pose) {
  scene.setPower(p.power);
  scene.setLevel(null);
  scene.setYaw(p.yaw);
  scene.model.root.userData.lidOpen = p.lid;
  scene.model.root.userData.dry = p.dry;
}

export const story: ProductStoryConfig = {
  modelId: 'ohaus-mb120',
  beats: [
    {
      id: 'hero',
      progressStart: 0,
      progressEnd: 0.16,
      cameraView: 'general',
      textHeadline: 'Humedad y sólidos, medidos',
      textBody: 'Analizador de humedad MB120 · capacidad {Capacidad} · legibilidad {Legibilidad}',
      animationHook: (scene, t) => pose(scene, { power: false, yaw: smoothstep(t) * Math.PI * 2, lid: 0 }),
    },
    {
      id: 'screen',
      progressStart: 0.16,
      progressEnd: 0.32,
      cameraView: 'pantalla',
      textHeadline: 'El método, en la pantalla táctil',
      textBody: 'Memoria para {Memoria}.',
      animationHook: (scene) => pose(scene, { power: false, yaw: 0, lid: 0 }),
      overlayComponent: 'hotspot',
      overlayData: ['pantalla'],
    },
    {
      id: 'lid',
      progressStart: 0.32,
      progressEnd: 0.48,
      cameraView: 'tapa',
      textHeadline: 'La tapa se abre: la muestra va al plato',
      textBody: 'Hasta {Capacidad} de muestra, pesada con {Legibilidad}.',
      animationHook: (scene, t) => pose(scene, { power: false, yaw: 0, lid: smoothstep(Math.min(1, t * 1.6)) }),
      overlayComponent: 'hotspot',
      overlayData: ['plato'],
    },
    {
      id: 'heat',
      progressStart: 0.48,
      progressEnd: 0.64,
      cameraView: 'tapa',
      textHeadline: 'La lámpara halógena calienta',
      textBody: '{Calentamiento}.',
      // En marcha la tapa queda abierta (la sigue la marcha) y la lámpara brilla.
      animationHook: (scene, t) => pose(scene, { power: true, yaw: 0, dry: 0.12 * t }),
      overlayComponent: 'hotspot',
      overlayData: ['lampara'],
    },
    {
      id: 'dry',
      progressStart: 0.64,
      progressEnd: 0.84,
      cameraView: 'pantalla',
      textHeadline: 'La humedad baja hasta el resultado',
      textBody: 'La muestra pierde masa al secarse y la pantalla da la humedad con {Legibilidad}.',
      animationHook: (scene, t) => pose(scene, { power: true, yaw: 0, dry: 0.12 + 0.88 * smoothstep(Math.min(1, t * 1.25)) }),
    },
    {
      id: 'close',
      progressStart: 0.84,
      progressEnd: 1,
      cameraView: 'general',
      textHeadline: 'Ficha técnica',
      textBody: 'Especificaciones con su fuente, aplicaciones y descargas, a continuación.',
      // Fin de la prueba: la tapa se cierra y la pantalla queda con el resultado.
      animationHook: (scene) => pose(scene, { power: false, yaw: null }),
      overlayComponent: 'cta',
    },
  ],
  specs: [
    { title: 'Medición', labels: ['Tipo', 'Capacidad', 'Legibilidad', 'Calentamiento'] },
    { title: 'Datos y conexión', labels: ['Memoria', 'Interfaces'] },
  ],
  // Las aplicaciones y los accesorios salen de la ficha del modelo.
  applications: [],
  accessories: [],
  relatedModels: ['ohaus-mb32', 'ohaus-pioneer-px'],
  faqs: [],
};
