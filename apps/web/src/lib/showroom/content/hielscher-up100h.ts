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
    { id: 'frontal', label: 'Panel' },
    { id: 'sonotrodo', label: 'Sonotrodo' },
    { id: 'agarre', label: 'En la mano' },
  ],
  hotspots: [
    {
      anchor: 'housing',
      view: 'general',
      title: 'Generador y transductor en una pieza',
      body: lines(c('Potencia') && c('Frecuencia') && `${c('Potencia')} · ${c('Frecuencia')}`, c('Régimen de trabajo'), c('Peso') && `Peso: ${c('Peso')}`),
    },
    {
      anchor: 'cycle',
      view: 'frontal',
      title: 'Perilla «Cycle»',
      body: ['Ciclo de pulso: de trabajo continuo a pulsos, para no calentar la muestra.'],
    },
    {
      anchor: 'amplitude',
      view: 'frontal',
      title: 'Perilla «Amplitude %»',
      body: lines(c('Amplitud') && `Amplitud: ${c('Amplitud')!.toLowerCase()}`, 'Cuánto se desplaza la punta en cada vibración.'),
    },
    {
      anchor: 'tip',
      view: 'sonotrodo',
      title: 'Sonotrodo intercambiable',
      body: lines(c('Volumen de muestra') && `Volumen de muestra: ${c('Volumen de muestra')!.toLowerCase()}`, 'Punta de titanio, atornillada al cuerpo; se cambia según el volumen.'),
    },
    {
      anchor: 'grip',
      view: 'agarre',
      title: 'En la mano o en el soporte',
      body: lines('La carcasa estriada es el mango: se sostiene a mano o se fija con una pinza a un soporte de laboratorio.', c('Peso') && `Peso: ${c('Peso')}`),
    },
    {
      anchor: 'cable',
      view: 'general',
      title: 'Una sola conexión',
      body: ['Un único cable de alimentación: no hay transductor aparte que conectar.'],
    },
  ],
};
