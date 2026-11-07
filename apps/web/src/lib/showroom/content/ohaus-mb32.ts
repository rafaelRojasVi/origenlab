import type { ShowroomEntry } from './types';
import { criterion, lines } from './sources';

const ID = 'ohaus-mb32';
const c = (label: string) => criterion(ID, label);

export const entry: ShowroomEntry = {
  id: ID,
  brand: 'OHAUS',
  name: 'MB32',
  kind: 'Analizador de humedad',
  photo: { modelId: ID },
  power: { on: 'Secar', off: 'Detener' },
  views: [
    { id: 'general', label: 'Vista general' },
    { id: 'pantalla', label: 'Pantalla y perilla' },
    { id: 'tapa', label: 'Tapa y calefactor' },
  ],
  hotspots: [
    {
      anchor: 'pantalla',
      view: 'pantalla',
      title: 'Pantalla LCD',
      body: lines(
        c('Legibilidad') && `Legibilidad: ${c('Legibilidad')}`,
        'Pantalla LCD de segmentos, sin función táctil: la humedad en porcentaje arriba y el peso en una segunda línea.',
      ),
    },
    {
      anchor: 'perilla',
      view: 'pantalla',
      title: 'Perilla y teclas',
      body: lines(
        'Una perilla y unas pocas teclas recorren el menú y lanzan el método de secado.',
        c('Programas') && `Programas de secado: ${c('Programas')!.toLowerCase()}`,
        c('Interfaces') && `Salidas: ${c('Interfaces')}`,
      ),
    },
    {
      anchor: 'tapa',
      view: 'tapa',
      title: 'Tapa con bisagra trasera',
      body: ['La tapa articulada se levanta hacia atrás para cargar la muestra y, cerrada, encierra el calor sobre el plato.'],
    },
    {
      anchor: 'calefactor',
      view: 'tapa',
      title: 'Calefactor de fibra de carbono',
      body: lines(c('Calentamiento'), 'Varillas calefactoras en la cara interior de la tapa: calientan la muestra desde arriba.'),
    },
    {
      anchor: 'plato',
      view: 'tapa',
      title: 'Plato de muestra',
      body: lines(c('Capacidad') && `Capacidad: ${c('Capacidad')}`, 'El plato queda bajo el calefactor, en la cámara de secado.'),
    },
  ],
};
