import type { ShowroomEntry } from './types';
import { criterion, lines } from './sources';

const ID = 'loeser-i-osmometer';
const c = (label: string) => criterion(ID, label);

export const entry: ShowroomEntry = {
  id: ID,
  brand: 'Löser',
  name: 'i-Osmometer',
  kind: 'Osmómetro crioscópico',
  photo: { modelId: ID },
  power: { on: 'Medir', off: 'Detener' },
  views: [
    { id: 'general',  label: 'Vista general' },
    { id: 'pantalla', label: 'Pantalla'       },
    { id: 'cabeza',   label: 'Cabeza de medición' },
    { id: 'lateral',  label: 'Lateral'        },
    { id: 'trasera',  label: 'Trasera'        },
  ],
  hotspots: [
    {
      anchor: 'screen',
      view: 'pantalla',
      title: 'Pantalla táctil en color',
      body: lines(
        c('Rango'),
        c('Memoria'),
      ),
    },
    {
      anchor: 'head',
      view: 'cabeza',
      title: 'Cabeza de medición Peltier',
      body: lines(
        c('Volumen de muestra'),
        c('Tiempo de medición'),
      ),
    },
    {
      anchor: 'printer',
      view: 'general',
      title: 'Impresora y lector integrados',
      body: lines(c('Documentación'), c('Interfaces')),
    },
  ],
};
