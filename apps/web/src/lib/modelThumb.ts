import { existsSync } from 'node:fs';
import { join } from 'node:path';
import type { Modelo } from '../content.config';
import { imageForEntry } from './modelos';
import { imageSourcesFor } from './productImage';
import { turntableFor } from './turntable';

/**
 * Miniatura de un modelo (sólo en el build: lee `public/`): la de 360 px del
 * estudio 3D (`<slug>-thumb.webp`), su póster si aún no hay miniatura, o la
 * fotografía del fabricante. La usan el menú grande y las industrias.
 */
export interface ModelThumb {
  kind: '3d' | 'photo';
  src: string;
  srcset: string | undefined;
  width: number;
  height: number;
  /** Texto alternativo: el nombre del modelo, o el `alt` del registro para una foto. */
  alt: string;
}

export function modelThumb(entry: Modelo): ModelThumb | null {
  const files = turntableFor(entry.slug);
  const small = `/products/${entry.slug}-thumb.webp`;
  if (files && existsSync(join(process.cwd(), 'public', small))) return { kind: '3d', src: small, srcset: undefined, width: 360, height: 270, alt: entry.name };
  if (files) return { kind: '3d', src: files.poster, srcset: undefined, width: 1200, height: 900, alt: entry.name };
  const record = imageForEntry(entry);
  const sources = imageSourcesFor(record);
  if (!sources) return null;
  return {
    kind: 'photo',
    src: sources.src,
    srcset: sources.webpSrcset.split(', ')[0],
    width: sources.width,
    height: sources.height,
    alt: record?.alt || entry.name,
  };
}
