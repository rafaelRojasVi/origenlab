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
    { id: 'general',  label: 'Vista general' },
    { id: 'pantalla', label: 'Pantalla LCD' },
    { id: 'plato',    label: 'Plato y protector' },
    { id: 'lateral',  label: 'Vista lateral' },
  ],
  hotspots: [
    {
      anchor: 'screen',
      view: 'pantalla',
      title: 'Pantalla LCD retroiluminada',
      body: lines(
        c('Pantalla'),
        c('Legibilidad') && `Legibilidad: ${c('Legibilidad')}`,
      ),
    },
    {
      anchor: 'pan',
      view: 'plato',
      title: 'Plato y protector de corrientes',
      body: lines(
        c('Capacidad') && `Capacidad: ${c('Capacidad')}`,
        c('Calibración'),
      ),
    },
    {
      anchor: 'shield',
      view: 'plato',
      title: 'Protector azul desmontable',
      body: lines(
        c('Protección'),
        'Protector translúcido que estabiliza la lectura en campo.',
      ),
    },
    {
      anchor: 'top',
      view: 'lateral',
      title: 'Conectividad e interfaces',
      body: lines(c('Interfaces')),
    },
  ],
};
