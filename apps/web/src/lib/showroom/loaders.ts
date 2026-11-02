/**
 * Carga diferida de cada modelo. Un `import()` literal por equipo, para que
 * cada uno quede en su propio fragmento y la portada no pida los demás.
 */
import type { ModelModule } from './types';

export const modelLoaders: Record<string, () => Promise<ModelModule>> = {
  'hielscher-up400st': () => import('./models/hielscher-up400st'),
  'ika-t25-digital': () => import('./models/ika-t25-digital'),
  'ortoalresa-digicen-22-r': () => import('./models/ortoalresa-digicen-22-r'),
  'ohaus-pioneer-px': () => import('./models/ohaus-pioneer-px'),
  'adam-highland': () => import('./models/adam-highland'),
  'loeser-i-osmometer': () => import('./models/loeser-i-osmometer'),
  'serva-bluevertical-prime': () => import('./models/serva-bluevertical-prime'),
};
