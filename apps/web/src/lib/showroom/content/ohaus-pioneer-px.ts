import type { ShowroomEntry } from './types';
import { criterion, lines } from './sources';

const ID = 'ohaus-pioneer-px';
const c = (label: string) => criterion(ID, label);

export const entry: ShowroomEntry = {
  id: ID,
  brand: 'OHAUS',
  name: 'Pioneer PX',
  kind: 'Balanza analítica y de precisión',
  photo: { modelId: ID },
  power: { on: 'Pesar', off: 'Retirar' },
  views: [
    { id: 'general',   label: 'Vista general' },
    { id: 'pantalla',  label: 'Pantalla' },
    { id: 'plato',     label: 'Plato' },
    { id: 'protector', label: 'Protector' },
  ],
  hotspots: [
    {
      anchor: 'display',
      view: 'pantalla',
      title: 'Pantalla y datos GLP/GMP',
      body: lines(c('Legibilidad') && `Legibilidad: ${c('Legibilidad')}`, c('Interfaces')),
    },
    {
      anchor: 'pan',
      view: 'plato',
      title: 'Plato de acero inoxidable',
      body: lines(c('Plato'), c('Calibración')),
    },
    {
      anchor: 'shield',
      view: 'protector',
      title: 'Protector de vidrio',
      body: lines(c('Tipo'), 'Protector desmontable de vidrio con tres puertas deslizantes y barra de descarga estática incorporada.'),
    },
    {
      anchor: 'foot',
      view: 'general',
      title: 'Modelos disponibles',
      body: lines(c('Capacidad')),
    },
  ],
};
