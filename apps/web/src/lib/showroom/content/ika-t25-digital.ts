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
    { id: 'acople',      label: 'Collarín y soporte' },
    { id: 'herramienta', label: 'Rotor y estátor' },
    { id: 'muestra',     label: 'En la muestra' },
  ],
  // El texto de cada nota sale sólo de la ficha con fuente (`brandModels.ts`).
  hotspots: [
    {
      anchor: 'display',
      view: 'panel',
      title: 'Indicación digital de velocidad',
      body: lines(c('Indicación'), c('Revoluciones')),
    },
    {
      anchor: 'knob',
      view: 'panel',
      title: 'Control de velocidad',
      body: lines(c('Revoluciones')),
    },
    {
      anchor: 'collar',
      view: 'acople',
      title: 'Collarín de acople',
      body: lines(c('Herramienta dispersora')),
    },
    {
      anchor: 'mount',
      view: 'acople',
      title: 'Motor',
      body: lines(c('Potencia')),
    },
    {
      anchor: 'herramienta',
      view: 'herramienta',
      title: 'Rotor y estátor',
      body: lines(c('Herramienta dispersora'), c('Viscosidad máxima')),
    },
  ],
};
