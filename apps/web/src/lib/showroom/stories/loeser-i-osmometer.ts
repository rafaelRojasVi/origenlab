/** Historia por scroll del Loeser i Osmometer: tres beats (`_threeBeat.ts`). */
import { threeBeatStory } from './_threeBeat';

export const story = threeBeatStory({
  modelId: 'loeser-i-osmometer',
  hero: { headline: 'Osmometría crioscópica', body: 'Osmómetro i Osmometer · muestra de {Volumen de muestra}' },
  reveal: {
    view: 'pantalla',
    scale: 3,
    anchors: ['screen', 'printer'],
    headline: 'El resultado, impreso junto a la muestra',
    body: 'Tiempo de medición: {Tiempo de medición}. Memoria: {Memoria}.',
  },
  specs: [
    { title: 'Medición', labels: ['Volumen de muestra', 'Tiempo de medición', 'Rango'] },
    { title: 'Registro y conexión', labels: ['Memoria', 'Documentación', 'Interfaces'] },
  ],
  relatedModels: ['loeser-i-osmometer-basic', 'loeser-osmometer-basic'],
});
