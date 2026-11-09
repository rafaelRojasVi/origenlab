import type { ShowroomEntry } from './types';
import { spec, lines } from './sources';

const ID = 'ortoalresa-digicen-22-r';
const s = (label: string) => spec(ID, label);

export const entry: ShowroomEntry = {
  id: ID,
  brand: 'Ortoalresa',
  name: 'Digicen 22 R',
  kind: 'Centrífuga universal refrigerada',
  photo: { productId: ID },
  power: { on: 'Centrifugar', off: 'Detener' },
  views: [
    { id: 'general',   label: 'Vista general' },
    { id: 'pantalla',  label: 'Panel de control' },
    { id: 'rotor',     label: 'Rotor y cubetas' },
    { id: 'lateral',   label: 'Lateral' },
  ],
  hotspots: [
    {
      anchor: 'screen',
      view:   'pantalla',
      title:  'Pantalla TFT táctil',
      body:   lines(
        s('Pantalla'),
        s('Versión') && `Versión: ${s('Versión')!.toLowerCase()}`,
        s('Conectividad') && `Conectividad: ${s('Conectividad')!.toLowerCase()}`,
      ),
    },
    {
      anchor: 'rotor',
      view:   'rotor',
      title:  'Rotor de balanceo',
      body:   lines(
        s('Capacidad máxima') && `Capacidad: ${s('Capacidad máxima')}`,
        s('Velocidad máxima') && `Velocidad máx.: ${s('Velocidad máxima')}`,
        s('Rotor'),
      ),
    },
    {
      anchor: 'lid',
      view:   'general',
      title:  'Tapa y cámara',
      body:   lines(
        'Tapa abatible con bisagra trasera sobre la cámara del rotor.',
        s('Cámara') && `Cámara: ${s('Cámara')!.toLowerCase()}`,
      ),
    },
    {
      anchor: 'body',
      view:   'lateral',
      title:  'Refrigeración y motor',
      body:   lines(
        s('Temperatura'),
        s('Refrigerante'),
        s('Motor') && `Motor: ${s('Motor')!.toLowerCase()}`,
        s('Nivel de ruido') && `Ruido: ${s('Nivel de ruido')!.toLowerCase()}`,
      ),
    },
  ],
};
