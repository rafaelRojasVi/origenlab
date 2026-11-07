/** Historia por scroll del Hielscher UP100H: tres beats (`_threeBeat.ts`); en el beat 2 el equipo se enciende y emite. */
import { threeBeatStory } from './_threeBeat';

export const story = threeBeatStory({
  modelId: 'hielscher-up100h',
  hero: { headline: 'Ultrasonido compacto', body: 'Procesador ultrasónico UP100H · {Potencia} · {Frecuencia}' },
  reveal: {
    view: 'general',
    anchors: ['housing', 'tip'],
    headline: 'Pequeño y de operación continua',
    body: 'Peso: {Peso}. Régimen de trabajo: {Régimen de trabajo}.',
  },
  specs: [
    { title: 'Potencia y frecuencia', labels: ['Potencia', 'Frecuencia', 'Amplitud'] },
    { title: 'Muestra y uso', labels: ['Volumen de muestra', 'Peso', 'Régimen de trabajo'] },
  ],
  relatedModels: ['hielscher-up200st', 'hielscher-up400st', 'hielscher-uip2000hdt'],
});
