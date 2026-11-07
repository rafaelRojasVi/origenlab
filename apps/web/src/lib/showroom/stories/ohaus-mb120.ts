/** Historia por scroll del OHAUS MB120: tres beats (`_threeBeat.ts`); en el beat 2 la tapa se abre y la lámpara trabaja. */
import { threeBeatStory } from './_threeBeat';

export const story = threeBeatStory({
  modelId: 'ohaus-mb120',
  hero: { headline: 'Humedad y sólidos, medidos', body: 'Analizador de humedad MB120 · capacidad {Capacidad}' },
  reveal: {
    view: 'tapa',
    anchors: ['lampara', 'plato'],
    headline: 'La tapa se abre, la lámpara trabaja',
    body: 'Calentamiento: {Calentamiento}. Legibilidad: {Legibilidad}.',
  },
  specs: [
    { title: 'Medición', labels: ['Tipo', 'Capacidad', 'Legibilidad', 'Calentamiento'] },
    { title: 'Datos y conexión', labels: ['Memoria', 'Interfaces'] },
  ],
  relatedModels: ['ohaus-mb32', 'ohaus-pioneer-px'],
  // La tapa sigue `root.userData.lidOpen` (0 cerrada … 1 abierta) con suavizado propio.
  extra: (scene, stage) => {
    scene.model.root.userData.lidOpen = stage === 'reveal' ? 1 : 0;
  },
});
