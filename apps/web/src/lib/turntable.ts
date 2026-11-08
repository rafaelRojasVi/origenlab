import { existsSync } from 'node:fs';
import { join } from 'node:path';

/**
 * Archivos horneados por `npm run posters` (`scripts/generate-posters.mjs`) para
 * cada modelo con escena 3D: el póster (`<slug>-poster.jpg`, 1200x900) y la tira
 * de giro (`<slug>-turntable.webp`, 24 cuadros 4:3 en una fila). Sólo en el
 * build (lee `public/`): las plantillas lo usan para elegir entre
 * `ui/Turntable.astro` (hay póster) y `ui/LightBox.astro` (sólo fotografía).
 */
export const TURNTABLE_FRAMES = 24;

export interface TurntableFiles {
  poster: string;
  /** `null` si el modelo tiene póster pero aún no tira: la tarjeta queda quieta. */
  strip: string | null;
}

const has = (file: string) => existsSync(join(process.cwd(), 'public', file));

export function turntableFor(slug: string): TurntableFiles | null {
  const poster = `products/${slug}-poster.jpg`;
  if (!has(poster)) return null;
  const strip = `products/${slug}-turntable.webp`;
  return { poster: `/${poster}`, strip: has(strip) ? `/${strip}` : null };
}
