/**
 * Historia por scroll del Hielscher UP400St: nueve beats sobre el modelo 3D
 * (`models/hielscher-up400st.ts`), según el guion de la página de producto.
 *
 * Las cifras no se escriben aquí. `{Etiqueta}` en un titular o una línea es el
 * valor de la ficha con fuente (`src/content/modelos/hielscher-up400st.json`),
 * que `ProductStory.astro` sustituye al construir la página; el rango del
 * medidor de amplitud también sale de esa ficha.
 *
 * Cada `animationHook` fija el estado completo de su beat con `pose` (marcha,
 * nivel, giro, sonotrodo, cifra de pantalla) y no sólo lo que cambia: así un
 * salto de scroll, que se salta los beats intermedios sin llamar a su `tick`,
 * o un regreso al principio, dejan la escena como corresponde. El primero
 * devuelve todo al reposo.
 */
import type { ProductStoryConfig, SonotrodeId, StoryScene } from '../productStory';
import { smoothstep } from '../scrollStory';

interface Pose {
  power: boolean;
  /** Nivel de marcha 0–1, o `null` para que siga a la marcha. */
  level: number | null;
  /** Giro sobre el de presentación, o `null` para el vaivén de reposo. */
  yaw: number | null;
  sonotrode: SonotrodeId;
  /** Cifra de pantalla como fracción del rango de la ficha, o `null`. */
  readout: number | null;
}

function pose(scene: StoryScene, p: Pose) {
  scene.setPower(p.power);
  if (p.level === null) scene.setLevel(null);
  else scene.setPowerLevel(p.level);
  scene.setYaw(p.yaw);
  scene.swapSonotrode(p.sonotrode);
  scene.setReadout(p.readout);
}

const rest: Pose = { power: false, level: null, yaw: null, sonotrode: 'S24d14', readout: null };
const running: Pose = { power: true, level: 0.25, yaw: 0, sonotrode: 'S24d14', readout: null };

export const story: ProductStoryConfig = {
  modelId: 'hielscher-up400st',
  // Vistas propias (el modelo no las trae): cada beat encuadra lo que describe.
  views: {
    // La punta de cerca, desde el otro lado que «sonotrodo»: la cavitación mientras sube la amplitud.
    punta: { anchor: 'tip', direction: [-0.35, 0.05, 1], distance: 230 },
    // El matraz entero con el sonotrodo dentro, un poco desde arriba.
    muestra: { anchor: 'flask', direction: [0.35, 0.3, 1], distance: 520 },
    // El sonotrodo de tres cuartos, desde el otro lado: el diámetro de la punta se lee de perfil.
    puntas: { anchor: 'tip', direction: [-0.9, 0.1, 0.5], distance: 420 },
  },
  beats: [
    {
      id: 'hero',
      progressStart: 0,
      progressEnd: 0.08,
      cameraView: 'general',
      textHeadline: 'Precisión sónica',
      textBody: 'Procesador ultrasónico UP400St · {Potencia} · {Frecuencia}',
      animationHook: (scene) => pose(scene, rest),
    },
    {
      id: 'turn',
      progressStart: 0.08,
      progressEnd: 0.2,
      cameraView: 'general',
      textHeadline: 'Compacto, por todos sus lados',
      textBody: 'Una vuelta completa al equipo: carcasa, pantalla, sujeción y sonotrodo.',
      // Vuelta entera (no tres cuartos): termina en la pose de presentación,
      // y los beats siguientes parten de ahí sin un salto de giro.
      animationHook: (scene, t) => pose(scene, { ...rest, yaw: smoothstep(t) * Math.PI * 2 }),
      overlayComponent: 'facts',
      overlayData: ['Potencia', 'Frecuencia', 'Volumen de muestra'],
    },
    {
      id: 'screen',
      progressStart: 0.2,
      progressEnd: 0.32,
      cameraView: 'pantalla',
      textHeadline: 'Todo el control en la pantalla',
      textBody: '{Registro}.',
      animationHook: (scene) => pose(scene, running),
      overlayComponent: 'hotspot',
      overlayData: ['screen'],
    },
    {
      id: 'sonotrodo',
      progressStart: 0.32,
      progressEnd: 0.46,
      cameraView: 'sonotrodo',
      textHeadline: 'Donde sucede la cavitación',
      textBody: 'Sonotrodo intercambiable · {Sonotrodos}',
      animationHook: (scene, t) => pose(scene, { ...running, level: 0.25 + 0.75 * t }),
      overlayComponent: 'hotspot',
      overlayData: ['tip'],
      // Un lado fijo: el punto se mueve con la cámara y la cavitación, y la nota no salta.
      overlayPlacement: { tip: 'right' },
    },
    {
      id: 'amplitude',
      progressStart: 0.46,
      progressEnd: 0.56,
      cameraView: 'punta',
      textHeadline: 'Qué es la amplitud',
      textBody: 'Cuánto se desplaza la punta en cada vibración: {Amplitud}. Más amplitud, cavitación más intensa.',
      animationHook: (scene, t) => pose(scene, { ...running, level: 0.2 + 0.8 * t, readout: t }),
      overlayComponent: 'meter',
      overlayData: ['Amplitud'],
    },
    {
      id: 'volumes',
      progressStart: 0.56,
      progressEnd: 0.68,
      cameraView: 'muestra',
      textHeadline: 'De la muestra pequeña al lote',
      textBody: '{Volumen de muestra}, con la misma unidad.',
      animationHook: (scene) => pose(scene, { ...running, level: 0.2 }),
      overlayComponent: 'callout',
      overlayData: ['flask', 'Volumen de muestra'],
    },
    {
      id: 'swap',
      progressStart: 0.68,
      progressEnd: 0.8,
      cameraView: 'puntas',
      textHeadline: 'El sonotrodo determina el volumen',
      textBody: 'La misma unidad acepta puntas de distinto diámetro: {Sonotrodos}.',
      animationHook: (scene, t) =>
        pose(scene, {
          ...running,
          level: 0.2,
          sonotrode: t < 1 / 3 ? 'S24d7' : t < 2 / 3 ? 'S24d14' : 'S24d22',
        }),
      overlayComponent: 'sonotrodes',
      overlayData: ['S24d7', 'S24d14', 'S24d22'],
    },
    {
      id: 'apps',
      progressStart: 0.8,
      progressEnd: 0.9,
      cameraView: 'general',
      textHeadline: 'Aplicaciones',
      textBody: 'Un mismo procesador para el trabajo de rutina y el desarrollo de método.',
      // La cavitación se apaga; la pantalla sigue encendida y el equipo vuelve al reposo.
      animationHook: (scene) => pose(scene, { ...running, level: 0, yaw: null, sonotrode: 'S24d22' }),
      overlayComponent: 'pills',
    },
    {
      id: 'close',
      progressStart: 0.9,
      progressEnd: 1,
      cameraView: 'general',
      textHeadline: 'Pida su cotización',
      textBody: 'Le enviamos una propuesta formal para su laboratorio. La ficha técnica con su fuente sigue a continuación.',
      // De tres cuartos por el otro lado y apagado: no repite el encuadre de «apps».
      animationHook: (scene) => pose(scene, { ...rest, yaw: -0.9, sonotrode: 'S24d22' }),
      overlayComponent: 'cta',
    },
  ],
  specs: [
    { title: 'Potencia y frecuencia', labels: ['Potencia', 'Frecuencia', 'Amplitud'] },
    { title: 'Muestra y sonotrodos', labels: ['Volumen de muestra', 'Sonotrodos'] },
    { title: 'Control', labels: ['Registro'] },
  ],
  // Las aplicaciones salen de la ficha del modelo (`applications`).
  applications: [],
  // Referencias de sonotrodo del fabricante, sin cifras propias: el diámetro
  // es el número del nombre y el rango con fuente está en la ficha.
  accessories: [
    { id: 'S24d7', name: 'Sonotrodo S24d7' },
    { id: 'S24d14', name: 'Sonotrodo S24d14' },
    { id: 'S24d22', name: 'Sonotrodo S24d22' },
  ],
  relatedModels: ['hielscher-up200st', 'hielscher-up100h', 'hielscher-uip2000hdt'],
  faqs: [],
};
