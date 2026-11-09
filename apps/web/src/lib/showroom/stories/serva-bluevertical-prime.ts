/** Historia por scroll de la SERVA BlueVertical PRiME: tres beats (`_threeBeat.ts`). */
import { threeBeatStory } from './_threeBeat';

export const story = threeBeatStory({
  modelId: 'serva-bluevertical-prime',
  hero: { headline: 'Electroforesis vertical', body: 'Cámara BlueVertical PRiME · formato de gel: {Formato de gel}' },
  reveal: {
    view: 'pocillos',
    anchors: ['wells', 'electrode'],
    headline: 'Pocillos, tampón y electrodo',
    body: 'Tampón: {Tampón}. Electrodo: {Electrodo}.',
  },
  specs: [
    { title: 'Gel y tampón', labels: ['Formato de gel', 'Tampón'] },
    { title: 'Electricidad', labels: ['Máximos', 'Electrodo'] },
    { title: 'Equipo', labels: ['Dimensiones', 'Ampliaciones'] },
  ],
  relatedModels: ['serva-bluepower', 'serva-bluemarine-100'],
});
