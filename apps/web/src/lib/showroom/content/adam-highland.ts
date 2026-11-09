import type { ShowroomEntry } from './types';
import { criterion, lines } from './sources';

const ID = 'adam-highland';
const c = (label: string) => criterion(ID, label);

export const entry: ShowroomEntry = {
  id: ID,
  brand: 'Adam Equipment',
  name: 'Highland',
  kind: 'Balanza portátil de precisión',
  photo: { modelId: ID },
  power: { on: 'Pesar', off: 'Retirar' },
  views: [
    { id: 'general', label: 'Vista general' },
    { id: 'pantalla', label: 'Pantalla y teclado' },
    { id: 'plato', label: 'Protector y plato' },
    { id: 'asa', label: 'Asa de transporte' },
  ],
  hotspots: [
    {
      anchor: 'screen',
      view: 'pantalla',
      title: 'Pantalla retroiluminada',
      body: lines(c('Pantalla'), c('Legibilidad') && `Legibilidad: ${c('Legibilidad')}`),
    },
    {
      anchor: 'keys',
      view: 'pantalla',
      title: 'Teclado de membrana',
      body: lines(c('Interfaces') && `Salida de datos: ${c('Interfaces')}`),
    },
    {
      anchor: 'shield',
      view: 'plato',
      title: 'Protector de corrientes con tapa perforada',
      body: lines(c('Legibilidad') && `Legibilidad: ${c('Legibilidad')}`),
    },
    {
      anchor: 'pan',
      view: 'plato',
      title: 'Plato',
      body: lines(c('Capacidad') && `Capacidad: ${c('Capacidad')}`, c('Protección')),
    },
    {
      anchor: 'handle',
      view: 'asa',
      title: 'Asa de transporte integrada',
      body: lines(c('Calibración') && `Calibración: ${c('Calibración')}`),
    },
  ],
};
