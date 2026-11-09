/** Historia por scroll de la OHAUS Pioneer PX: tres beats (`_threeBeat.ts`). */
import { threeBeatStory } from './_threeBeat';

export const story = threeBeatStory({
  modelId: 'ohaus-pioneer-px',
  hero: { headline: 'Pesaje analítico', body: 'Balanza Pioneer PX · legibilidad {Legibilidad}' },
  reveal: {
    view: 'protector',
    scale: 1.4,
    anchors: ['display', 'pan'],
    placement: { pan: 'left:90' },
    headline: 'Del plato a la pantalla',
    body: 'Plato: {Plato}. Calibración: {Calibración}.',
  },
  specs: [
    { title: 'Capacidad y lectura', labels: ['Tipo', 'Capacidad', 'Legibilidad'] },
    { title: 'Plato y calibración', labels: ['Plato', 'Calibración'] },
    { title: 'Conexión', labels: ['Interfaces'] },
  ],
  relatedModels: ['adam-highland', 'ohaus-ranger-count-3000'],
});
