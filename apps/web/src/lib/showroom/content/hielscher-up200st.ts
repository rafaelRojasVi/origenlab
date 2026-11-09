import type { ShowroomEntry } from './types';
import { criterion, lines } from './sources';

const ID = 'hielscher-up200st';
const c = (label: string) => criterion(ID, label);

export const entry: ShowroomEntry = {
  id: ID,
  brand: 'Hielscher',
  name: 'UP200St',
  kind: 'Procesador ultrasónico',
  photo: { modelId: ID },
  power: { on: 'Encender', off: 'Apagar' },
  views: [
    { id: 'general', label: 'Vista general' },
    { id: 'pantalla', label: 'Pantalla' },
    { id: 'transductor', label: 'Transductor' },
    { id: 'sonotrodo', label: 'Sonotrodo' },
  ],
  hotspots: [
    { anchor: 'screen', view: 'pantalla', title: 'Pantalla y registro', body: lines(c('Registro'), c('Amplitud') && `Amplitud: ${c('Amplitud')!.toLowerCase()}`) },
    { anchor: 'transducer', view: 'transductor', title: 'Transductor de acero inoxidable', body: lines(c('Protección'), [c('Potencia'), c('Frecuencia')].filter(Boolean).join(' · ')) },
    { anchor: 'tip', view: 'sonotrodo', title: 'Sonotrodo en la muestra', body: lines(c('Volumen de muestra') && `Volumen de muestra: ${c('Volumen de muestra')}`) },
    { anchor: 'stand', view: 'general', title: 'Montaje en soporte', body: ['Una mordaza sujeta el transductor en vertical a la varilla del soporte; el cable lo une al generador, que queda aparte sobre el mesón.'] },
  ],
};
