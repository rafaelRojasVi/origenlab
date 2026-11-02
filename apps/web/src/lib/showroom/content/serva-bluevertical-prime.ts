import type { ShowroomEntry } from './types';
import { criterion, lines } from './sources';

const ID = 'serva-bluevertical-prime';
const PS = 'serva-bluepower';
const c = (label: string) => criterion(ID, label);
const p = (label: string) => criterion(PS, label);

export const entry: ShowroomEntry = {
  id: ID,
  brand: 'SERVA',
  name: 'BlueVertical™ PRiME™',
  kind: 'Cámara de electroforesis vertical',
  photo: { modelId: ID },
  power: { on: 'Iniciar corrida', off: 'Detener' },
  views: [
    { id: 'general', label: 'Vista general' },
    { id: 'pocillos', label: 'Pocillos' },
    { id: 'bandas', label: 'Bandas en gel' },
    { id: 'fuente', label: 'Fuente BluePower' },
  ],
  hotspots: [
    {
      anchor: 'wells',
      view: 'pocillos',
      title: 'Peine y pocillos',
      body: lines(
        c('Formato de gel'),
        c('Tampón'),
      ),
    },
    {
      anchor: 'gel',
      view: 'bandas',
      title: 'Gel de separación',
      body: lines(
        c('Máximos'),
        c('Electrodo'),
        c('Ampliaciones'),
      ),
    },
    {
      anchor: 'electrode',
      view: 'pocillos',
      title: 'Electrodo de platino',
      body: lines(
        c('Electrodo'),
        c('Máximos'),
      ),
    },
    {
      anchor: 'display',
      view: 'fuente',
      title: 'Fuente BluePower 600 PRIME',
      body: lines(
        p('BluePower 600 PRIME'),
        p('Programación'),
        p('Seguridad'),
      ),
    },
  ],
};
