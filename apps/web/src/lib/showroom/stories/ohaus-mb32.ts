/** Historia por scroll del OHAUS MB32: tres beats (`_threeBeat.ts`); en el beat 2 el equipo se enciende, la tapa se abre sola y el calefactor trabaja. */
import { threeBeatStory } from './_threeBeat';

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
});
