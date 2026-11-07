/** Historia por scroll del Hielscher UP200St: tres beats (`_threeBeat.ts`); en el beat 2 el equipo se enciende y emite. */
import { threeBeatStory } from './_threeBeat';

export const story = threeBeatStory({
  modelId: 'hielscher-up200st',
  hero: { headline: 'Sonicación de laboratorio', body: 'Procesador ultrasónico UP200St · {Potencia} · {Frecuencia}' },
  reveal: {
    view: 'general',
    anchors: ['screen', 'tip'],
    placement: { screen: 'bottom:70', tip: 'bottom:110' },
    headline: 'Generador y transductor, por separado',
    body: 'Protección: {Protección}. Volumen de muestra: {Volumen de muestra}.',
  },
  specs: [
    { title: 'Potencia y frecuencia', labels: ['Potencia', 'Frecuencia', 'Amplitud'] },
    { title: 'Muestra y protección', labels: ['Volumen de muestra', 'Protección'] },
    { title: 'Control', labels: ['Registro'] },
  ],
  relatedModels: ['hielscher-up400st', 'hielscher-up100h', 'hielscher-uip2000hdt'],
});
