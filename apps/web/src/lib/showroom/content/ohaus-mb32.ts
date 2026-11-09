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
    { id: 'tapa', label: 'Tapa y cámara' },
    { id: 'calefactor', label: 'Calefactor' },
  ],
  hotspots: [
    {
      anchor: 'pantalla',
      view: 'pantalla',
      title: 'Pantalla LCD',
      body: lines(
        c('Legibilidad') && `Legibilidad: ${c('Legibilidad')}`,
        'LCD retroiluminada, sin función táctil: la cifra grande arriba (peso o humedad en %MC) y una segunda línea con indicaciones.',
      ),
    },
    {
      anchor: 'perilla',
      view: 'pantalla',
      title: 'Perilla y teclas',
      body: lines(
        'La perilla, bajo la pantalla, recorre el menú y elige el programa; las teclas de la franja inferior encienden, detienen y taran.',
        c('Programas') && `Programas de secado: ${c('Programas')!.toLowerCase()}`,
        c('Interfaces') && `Salidas: ${c('Interfaces')}`,
      ),
    },
    {
      anchor: 'tapa',
      view: 'tapa',
      title: 'Tapa con cubierta del calefactor',
      body: ['La tapa articulada se levanta hacia atrás para cargar la muestra; arriba lleva la cubierta negra del calefactor, con aletas.'],
    },
    {
      anchor: 'calefactor',
      view: 'calefactor',
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
