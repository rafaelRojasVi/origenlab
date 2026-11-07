import type { ShowroomEntry } from './types';
import { criterion, lines } from './sources';

const ID = 'hielscher-up100h';
const c = (label: string) => criterion(ID, label);

export const entry: ShowroomEntry = {
  id: ID,
  brand: 'Hielscher',
  name: 'UP100H',
  kind: 'Procesador ultrasónico',
  photo: { modelId: ID },
  power: { on: 'Encender', off: 'Apagar' },
  views: [
    { id: 'general', label: 'Vista general' },
    { id: 'frontal', label: 'Frontal' },
    { id: 'sonotrodo', label: 'Sonotrodo' },
  ],
  hotspots: [
    {
      anchor: 'housing',
      view: 'general',
      title: 'Generador y transductor en una pieza',
      body: lines(c('Potencia') && c('Frecuencia') && `${c('Potencia')} · ${c('Frecuencia')}`, c('Régimen de trabajo'), c('Peso') && `Peso: ${c('Peso')}`),
    },
    {
      anchor: 'knob',
      view: 'frontal',
      title: 'Control de amplitud',
      body: lines(c('Amplitud') && `Amplitud: ${c('Amplitud')!.toLowerCase()}`),
    },
    {
      anchor: 'tip',
      view: 'sonotrodo',
      title: 'Sonotrodo intercambiable',
      body: lines(c('Volumen de muestra') && `Volumen de muestra: ${c('Volumen de muestra')!.toLowerCase()}`, 'Punta de titanio, atornillada al cuerpo; se cambia según el volumen.'),
    },
    {
      anchor: 'cable',
      view: 'general',
      title: 'Una sola conexión',
      body: ['Un único cable de alimentación: no hay transductor aparte que conectar.'],
    },
  ],
};
