/** Historia por scroll del IKA T 25 digital ULTRA-TURRAX: tres beats (`_threeBeat.ts`). */
import { threeBeatStory } from './_threeBeat';

export const story = threeBeatStory({
  modelId: 'ika-t25-digital',
  hero: { headline: 'Dispersión de laboratorio', body: 'Dispersor ULTRA-TURRAX T 25 digital · {Revoluciones}' },
  reveal: {
    view: 'panel',
    anchors: ['display', 'knob'],
    headline: 'Las revoluciones, a la vista',
    body: 'Indicación: {Indicación}. Herramienta: {Herramienta dispersora}.',
  },
  specs: [
    { title: 'Rendimiento', labels: ['Volumen de trabajo (H₂O)', 'Revoluciones', 'Potencia', 'Viscosidad máxima'] },
    { title: 'Herramienta e indicación', labels: ['Herramienta dispersora', 'Indicación'] },
  ],
  relatedModels: ['ika-t18-digital', 'ika-t10-basic'],
});
