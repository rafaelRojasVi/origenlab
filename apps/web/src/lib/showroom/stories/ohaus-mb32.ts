/** Historia por scroll del OHAUS MB32: tres beats (`_threeBeat.ts`); en el beat 2 la tapa se sostiene abierta y el calefactor se enciende. */
import { threeBeatStory } from './_threeBeat';
import { smoothstep } from '../scrollStory';

/** Apertura de la tapa del modelo (`LID_OPEN`: 96°). */
const LID_OPEN = (96 * Math.PI) / 180;

interface WithLid {
  setLidAngle?(angle: number | null): void;
}

export const story = threeBeatStory({
  modelId: 'ohaus-mb32',
  hero: { headline: 'Humedad, sin complicaciones', body: 'Analizador de humedad MB32 · capacidad {Capacidad}' },
  reveal: {
    view: 'tapa',
    anchors: ['calefactor', 'plato'],
    headline: 'La tapa se abre, el calefactor trabaja',
    body: 'Calentamiento: {Calentamiento}. Programas: {Programas}.',
  },
  specs: [
    { title: 'Medición', labels: ['Tipo', 'Capacidad', 'Legibilidad', 'Calentamiento'] },
    { title: 'Programas y conexión', labels: ['Programas', 'Interfaces'] },
  ],
  relatedModels: ['ohaus-mb120', 'ohaus-pioneer-px'],
  // El modelo abre la tapa sólo con el nivel entre 0,08 y 0,5: aquí se sostiene
  // abierta mientras el calefactor está en marcha (`setLidAngle`) y, fuera de ese
  // beat, el nivel va a 0 para que la tapa se cierre sola sin pasar por la ventana.
  extra: (scene, stage, t) => {
    const lid = scene.model as unknown as WithLid;
    if (stage === 'reveal') {
      lid.setLidAngle?.(LID_OPEN * smoothstep(Math.min(1, t * 4)));
    } else {
      scene.setLevel(0);
      lid.setLidAngle?.(null);
    }
  },
});
