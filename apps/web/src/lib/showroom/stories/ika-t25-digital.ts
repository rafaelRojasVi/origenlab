/**
 * Historia por scroll del IKA T 25 digital ULTRA-TURRAX: seis beats sobre el
 * modelo 3D (`models/ika-t25-digital.ts`), como `hielscher-up400st.ts`.
 *
 *   hero      vuelta al equipo, en reposo
 *   panel     pantalla LED y dial: las revoluciones suben con el scroll
 *   acople    collarín, perilla de fijación y brazo al soporte
 *   cabezal   el elemento dispersor: rotor girando dentro del estátor
 *   muestra   a plena marcha en el vaso: vórtice y cizalla
 *   close     vuelta al reposo y «Solicitar cotización»
 *
 * Las cifras no se escriben aquí: `{Etiqueta}` es el valor de la ficha con
 * fuente, que `ProductStory.astro` sustituye. La cifra de la pantalla la fija
 * `root.userData.rpmFraction` (fracción del rango de la ficha; `null` la deja
 * seguir la marcha). Cada beat fija el estado completo con `pose`, para que un
 * salto de scroll o un regreso dejen la escena como corresponde.
 */
import type { ProductStoryConfig, StoryScene } from '../productStory';
import { smoothstep } from '../scrollStory';

interface Pose {
  power: boolean;
  /** Nivel de marcha 0–1, o `null` para que siga a la marcha. */
  level: number | null;
  /** Giro sobre el de presentación, o `null` para el vaivén de reposo. */
  yaw: number | null;
  /** Cifra de la pantalla como fracción del rango de revoluciones, o `null`. */
  rpm: number | null;
}

function pose(scene: StoryScene, p: Pose) {
  scene.setPower(p.power);
  if (p.level === null) scene.setLevel(null);
  else scene.setPowerLevel(p.level);
  scene.setYaw(p.yaw);
  scene.model.root.userData.rpmFraction = p.rpm;
}

const rest: Pose = { power: false, level: null, yaw: null, rpm: null };

export const story: ProductStoryConfig = {
  modelId: 'ika-t25-digital',
  beats: [
    {
      id: 'hero',
      progressStart: 0,
      progressEnd: 0.16,
      cameraView: 'general',
      textHeadline: 'Dispersión de laboratorio',
      textBody: 'Dispersor ULTRA-TURRAX T 25 digital · {Revoluciones}',
      animationHook: (scene, t) => pose(scene, { ...rest, yaw: smoothstep(t) * Math.PI * 2 }),
    },
    {
      id: 'panel',
      progressStart: 0.16,
      progressEnd: 0.34,
      cameraView: 'panel',
      textHeadline: 'Las revoluciones, a la vista',
      textBody: 'Se fijan con el dial y se leen en la pantalla LED, en miles por minuto: {Revoluciones}.',
      // El dial sube del mínimo al máximo de la ficha con el scroll.
      animationHook: (scene, t) => pose(scene, { power: true, level: 0.2 + 0.5 * t, yaw: 0, rpm: smoothstep(t) }),
      overlayComponent: 'hotspot',
      overlayData: ['display', 'knob'],
      overlayPlacement: { display: 'left:64', knob: 'left:64' },
    },
    {
      id: 'acople',
      progressStart: 0.34,
      progressEnd: 0.5,
      cameraView: 'acople',
      textHeadline: 'Firme en el soporte',
      textBody: 'El motor va sujeto a la varilla por su brazo; el elemento dispersor entra en el collarín y queda fijo con la perilla lateral.',
      animationHook: (scene) => pose(scene, { power: false, level: null, yaw: 0, rpm: null }),
      overlayComponent: 'hotspot',
      overlayData: ['collar', 'mount'],
      overlayPlacement: { collar: 'right:48', mount: 'right:48' },
    },
    {
      id: 'cabezal',
      progressStart: 0.5,
      progressEnd: 0.68,
      cameraView: 'herramienta',
      textHeadline: 'Rotor y estátor',
      textBody: 'El rotor gira dentro del estátor dentado: aspira la muestra y la expulsa por las ranuras con una cizalla intensa. Herramienta: {Herramienta dispersora}.',
      animationHook: (scene, t) => pose(scene, { power: true, level: 0.15 + 0.35 * t, yaw: 0, rpm: 0.2 + 0.3 * t }),
      overlayComponent: 'hotspot',
      overlayData: ['herramienta'],
      overlayPlacement: { herramienta: 'left:56' },
    },
    {
      id: 'muestra',
      progressStart: 0.68,
      progressEnd: 0.86,
      cameraView: 'muestra',
      textHeadline: 'En la muestra',
      textBody: 'A plena marcha el líquido gira en vórtice y la segunda fase se rompe en gotas finas.',
      animationHook: (scene, t) => pose(scene, { power: true, level: 0.5 + 0.5 * smoothstep(t), yaw: 0, rpm: 0.5 + 0.5 * smoothstep(t) }),
      overlayComponent: 'facts',
      overlayData: ['Volumen de trabajo (H₂O)', 'Viscosidad máxima', 'Potencia'],
    },
    {
      id: 'close',
      progressStart: 0.86,
      progressEnd: 1,
      cameraView: 'general',
      textHeadline: 'Ficha técnica',
      textBody: 'Especificaciones con su fuente, aplicaciones y descargas, a continuación.',
      animationHook: (scene) => pose(scene, rest),
      overlayComponent: 'cta',
    },
  ],
  specs: [
    { title: 'Rendimiento', labels: ['Volumen de trabajo (H₂O)', 'Revoluciones', 'Potencia', 'Viscosidad máxima'] },
    { title: 'Herramienta e indicación', labels: ['Herramienta dispersora', 'Indicación'] },
  ],
  // Las aplicaciones y los accesorios salen de la ficha del modelo.
  applications: [],
  accessories: [],
  relatedModels: ['ika-t18-digital', 'ika-t10-basic'],
  faqs: [],
};
