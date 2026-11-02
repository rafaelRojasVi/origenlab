import type { ShowroomEntry } from './types';
import { criterion, lines } from './sources';

const ID = 'ika-t25-digital';
const c = (label: string) => criterion(ID, label);

export const entry: ShowroomEntry = {
  id: ID,
  brand: 'IKA',
  name: 'T 25 digital ULTRA-TURRAX',
  kind: 'Dispersor ULTRA-TURRAX',
  photo: { modelId: ID },
  power: { on: 'Encender', off: 'Apagar' },
  views: [
    { id: 'general',     label: 'Vista general' },
    { id: 'panel',       label: 'Panel de control' },
    { id: 'herramienta', label: 'Herramienta dispersora' },
    { id: 'lateral',     label: 'Lateral' },
  ],
  hotspots: [
    {
      anchor: 'display',
      view: 'panel',
      title: 'Indicación digital de velocidad',
      body: lines(
        c('Indicación'),
        c('Revoluciones'),
      ),
    },
    {
      anchor: 'knob',
      view: 'panel',
      title: 'Control de velocidad',
      body: lines(
        c('Revoluciones'),
        c('Volumen de trabajo (H₂O)'),
      ),
    },
    {
      anchor: 'herramienta',
      view: 'herramienta',
      title: 'Herramienta dispersora S 25',
      body: lines(
        c('Herramienta dispersora'),
        c('Viscosidad máxima'),
      ),
    },
    {
      anchor: 'mount',
      view: 'general',
      title: 'Potencia y montaje',
      body: lines(
        c('Potencia'),
        'Se fija a la varilla del soporte con un collarín; el cabezal queda centrado sobre el vaso.',
      ),
    },
  ],
};
