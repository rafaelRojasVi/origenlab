import type { ShowroomEntry } from './types';
import { criterion, lines } from './sources';

const ID = 'hielscher-up400st';
const c = (label: string) => criterion(ID, label);

export const entry: ShowroomEntry = {
  id: ID,
  brand: 'Hielscher',
  name: 'UP400St',
  kind: 'Procesador ultrasónico',
  photo: { modelId: ID },
  power: { on: 'Encender', off: 'Apagar' },
  views: [
    { id: 'general', label: 'Vista general' },
    { id: 'pantalla', label: 'Pantalla' },
    { id: 'sonotrodo', label: 'Sonotrodo' },
    { id: 'lateral', label: 'Lateral' },
  ],
  hotspots: [
    { anchor: 'screen', view: 'pantalla', title: 'Pantalla y registro', body: lines(c('Registro'), c('Amplitud') && `Amplitud: ${c('Amplitud')!.toLowerCase()}`) },
    { anchor: 'tip', view: 'sonotrodo', title: 'Sonotrodo intercambiable', body: lines(c('Sonotrodos'), c('Volumen de muestra')) },
    { anchor: 'transducer', view: 'lateral', title: 'Potencia y frecuencia', body: lines([c('Potencia'), c('Frecuencia')].filter(Boolean).join(' · ')) },
    { anchor: 'mount', view: 'general', title: 'Montaje en soporte', body: ['Se fija a la varilla del soporte con una perilla; el sonotrodo queda centrado sobre el vaso.'] },
  ],
};
