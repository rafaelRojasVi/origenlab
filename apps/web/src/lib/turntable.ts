import { existsSync, readFileSync } from 'node:fs';
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
  /** Póster de 640x480 en WebP para tarjetas (`null` hasta que `npm run posters` lo genere). */
  posterSmall: string | null;
  /** `null` si el modelo tiene póster pero aún no tira: la tarjeta queda quieta. */
  strip: string | null;
}

const has = (file: string) => existsSync(join(process.cwd(), 'public', file));

export function turntableFor(slug: string): TurntableFiles | null {
  const poster = `products/${slug}-poster.jpg`;
  if (!has(poster)) return null;
  const strip = `products/${slug}-turntable.webp`;
  const small = `products/${slug}-poster-640.webp`;
  return { poster: `/${poster}`, posterSmall: has(small) ? `/${small}` : null, strip: has(strip) ? `/${strip}` : null };
}

/**
 * Caja que ocupa el equipo dentro del cuadro 4:3 del póster, unida sobre los 24
 * cuadros de la tira, en fracciones del cuadro (`src/data/posterFraming.json`,
 * lo escribe `npm run posters`).
 */
interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}
const framingFile = join(process.cwd(), 'src', 'data', 'posterFraming.json');
const framing: Record<string, Box> = existsSync(framingFile) ? JSON.parse(readFileSync(framingFile, 'utf8')) : {};

/**
 * Posición del cuadro 4:3 dentro de un escenario de proporción `ratio` (ancho /
 * alto) para que el equipo quede centrado, lo más grande posible sin cortarse:
 * `fill` del ancho y `fillH` del alto como máximo, con el centro del equipo a
 * `centerY` del alto. Devuelve `left`/`top`/`width` en % del escenario (sin
 * transformaciones: así el cuadro puede fundirse con el fondo por multiply).
 * Sin encuadre conocido, el cuadro llena el ancho, centrado.
 */
export function stageFrame(slug: string, ratio: number, fill = 0.8, fillH = 0.68, centerY = 0.54): string {
  const box = framing[slug] ?? { x: 0.1, y: 0.1, w: 0.8, h: 0.8 };
  const H = 1 / ratio; // alto del escenario en anchos
  const k = Math.min(fill / box.w, (fillH * H) / (0.75 * box.h), 2.2); // ancho del cuadro en anchos
  const left = 0.5 - k * (box.x + box.w / 2);
  const top = centerY * H - 0.75 * k * (box.y + box.h / 2);
  const pct = (v: number) => `${Math.round(v * 10000) / 100}%`;
  return `left:${pct(left)};top:${pct(top / H)};width:${pct(k)}`;
}
