import type { ShowroomEntry } from './types';
import { criterion, lines } from './sources';

const ID = 'ohaus-mb120';
const c = (label: string) => criterion(ID, label);

export const entry: ShowroomEntry = {
  id: ID,
  brand: 'OHAUS',
  name: 'MB120',
  kind: 'Analizador de humedad',
  photo: { modelId: ID },
  power: { on: 'Iniciar secado', off: 'Detener' },
  views: [
    { id: 'general', label: 'Vista general' },
    { id: 'pantalla', label: 'Pantalla' },
    { id: 'tapa', label: 'Tapa abierta' },
  ],
  hotspots: [
    {
      anchor: 'pantalla',
      view: 'pantalla',
      title: 'Pantalla táctil',
      body: lines(c('Memoria'), c('Interfaces')),
    },
    {
      anchor: 'tapa',
      view: 'tapa',
      title: 'Tapa articulada',
      body: lines(c('Tipo')),
    },
    {
      anchor: 'lampara',
      view: 'tapa',
      title: 'Lámpara halógena',
      body: lines(c('Calentamiento')),
    },
    {
      anchor: 'plato',
      view: 'tapa',
      title: 'Plato de la muestra',
      body: lines(c('Capacidad') && `Capacidad: ${c('Capacidad')}`, c('Legibilidad') && `Legibilidad: ${c('Legibilidad')}`),
    },
  ],
};
