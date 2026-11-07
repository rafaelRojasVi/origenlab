/** Historia por scroll de la Ortoalresa Digicen 22 R: tres beats (`_threeBeat.ts`). */
import { threeBeatStory } from './_threeBeat';

export const story = threeBeatStory({
  modelId: 'ortoalresa-digicen-22-r',
  hero: { headline: 'Centrifugación refrigerada', body: 'Centrífuga universal Digicen 22 R · {Velocidad máxima}' },
  reveal: {
    view: 'general',
    anchors: ['screen', 'lid'],
    headline: 'Control táctil, cámara refrigerada',
    body: 'Pantalla: {Pantalla}. Temperatura: {Temperatura}.',
  },
  specs: [
    { title: 'Capacidad y velocidad', labels: ['Capacidad máxima', 'Velocidad máxima', 'Rotor', 'Motor', 'Nivel de ruido'] },
    { title: 'Refrigeración', labels: ['Versión', 'Temperatura', 'Temperatura a máximas RPM', 'Pre-enfriamiento', 'Sensor', 'Refrigerante'] },
    { title: 'Control y cámara', labels: ['Pantalla', 'Conectividad', 'Accesorios', 'Cámara'] },
    { title: 'Instalación', labels: ['Dimensiones (CE 259 / CE 260)', 'Peso neto', 'Alimentación CE 259', 'Alimentación CE 260'] },
  ],
  relatedModels: ['ortoalresa-digicen-22', 'ortoalresa-biocen-22-r'],
});
