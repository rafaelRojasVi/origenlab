/** Historia por scroll de la balanza Adam Highland: tres beats (`_threeBeat.ts`). */
import { threeBeatStory } from './_threeBeat';

export const story = threeBeatStory({
  modelId: 'adam-highland',
  hero: { headline: 'Pesaje de precisión', body: 'Balanza portátil Highland · {Capacidad}' },
  reveal: {
    view: 'plato',
    scale: 1.8,
    anchors: ['pan', 'shield'],
    headline: 'Plato protegido, calibración interna',
    body: 'Calibración: {Calibración}. Protección: {Protección}.',
  },
  specs: [
    { title: 'Capacidad y lectura', labels: ['Capacidad', 'Legibilidad', 'Pantalla'] },
    { title: 'Calibración y protección', labels: ['Calibración', 'Protección'] },
    { title: 'Conexión', labels: ['Interfaces'] },
  ],
  relatedModels: ['ohaus-pioneer-px', 'adam-pmb', 'adam-solis'],
});
