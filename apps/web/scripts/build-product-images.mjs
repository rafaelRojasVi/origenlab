#!/usr/bin/env node
/**
 * Deriva los tamaños de imagen de producto que usa el sitio.
 *
 * Fuente: los AVIF originales del fabricante en `public/products/<marca>/`
 * (procedencia en `docs/product-assets.md`). Salida: los mismos recortes a 480 y
 * 960 px de ancho, en AVIF y WebP, con el fondo blanco convertido en
 * transparencia.
 *
 * Por qué la transparencia: los originales son recortes de estudio sobre blanco
 * puro. Sobre el fondo papel del sitio (#FAFAF7) ese blanco se ve como una caja
 * flotante detrás de cada equipo, que es justo el efecto de ficha que el
 * rediseño elimina. El fondo se quita por relleno desde los bordes, no por
 * umbral global: así el gris claro de las carcasas y los reflejos internos, que
 * no tocan el borde, se conservan intactos. No se retoca, recorta ni recompone
 * nada del equipo fotografiado.
 *
 * Ejecutar: npm run build:product-images
 */
import { readdirSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import sharp from 'sharp';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const baseDir = join(root, 'public', 'products');

export const PRODUCT_IMAGE_WIDTHS = [480, 960];

/** Luminancia mínima para considerar un píxel parte del fondo de estudio. */
const BACKGROUND_MIN = 246;
/** Margen de suavizado: entre este valor y BACKGROUND_MIN el alfa interpola. */
const BACKGROUND_SOFT = 228;

/**
 * Relleno por inundación desde los cuatro bordes. Devuelve un buffer RGBA con
 * el fondo conectado al borde convertido en transparente y un alfa progresivo
 * en la orla, para que no queden dientes de sierra en los contornos.
 */
function cutOutBackground(data, width, height, channels) {
  const total = width * height;
  const luminance = new Uint8Array(total);
  for (let i = 0; i < total; i += 1) {
    const o = i * channels;
    luminance[i] = (data[o] * 299 + data[o + 1] * 587 + data[o + 2] * 114) / 1000;
  }

  const isBackground = new Uint8Array(total);
  const stack = [];
  const push = (x, y) => {
    if (x < 0 || y < 0 || x >= width || y >= height) return;
    const i = y * width + x;
    if (isBackground[i] || luminance[i] < BACKGROUND_MIN) return;
    isBackground[i] = 1;
    stack.push(i);
  };

  for (let x = 0; x < width; x += 1) {
    push(x, 0);
    push(x, height - 1);
  }
  for (let y = 0; y < height; y += 1) {
    push(0, y);
    push(width - 1, y);
  }

  while (stack.length) {
    const i = stack.pop();
    const x = i % width;
    const y = (i - x) / width;
    push(x - 1, y);
    push(x + 1, y);
    push(x, y - 1);
    push(x, y + 1);
  }

  const rgba = Buffer.alloc(total * 4);
  for (let i = 0; i < total; i += 1) {
    const o = i * channels;
    rgba[i * 4] = data[o];
    rgba[i * 4 + 1] = data[o + 1];
    rgba[i * 4 + 2] = data[o + 2];
    if (isBackground[i]) {
      rgba[i * 4 + 3] = 0;
      continue;
    }
    // Orla: los píxeles claros que tocan el fondo se atenúan en proporción a su
    // luminancia, de modo que el borde del equipo no quede recortado a hachazos.
    const x = i % width;
    const y = (i - x) / width;
    const touchesBackground =
      (x > 0 && isBackground[i - 1]) ||
      (x < width - 1 && isBackground[i + 1]) ||
      (y > 0 && isBackground[i - width]) ||
      (y < height - 1 && isBackground[i + width]);
    if (touchesBackground && luminance[i] > BACKGROUND_SOFT) {
      const t = (luminance[i] - BACKGROUND_SOFT) / (BACKGROUND_MIN - BACKGROUND_SOFT);
      rgba[i * 4 + 3] = Math.round(255 * (1 - Math.min(1, t)));
    } else {
      rgba[i * 4 + 3] = 255;
    }
  }
  return rgba;
}

const brandDirs = readdirSync(baseDir).filter((entry) =>
  statSync(join(baseDir, entry)).isDirectory(),
);

for (const brandDir of brandDirs) {
  const dir = join(baseDir, brandDir);
  const derivedSuffix = new RegExp(`-(${PRODUCT_IMAGE_WIDTHS.join('|')})\\.(avif|webp)$`);
  const originals = readdirSync(dir).filter(
    (file) => file.endsWith('.avif') && !derivedSuffix.test(file),
  );

  for (const file of originals) {
    const slug = file.replace(/\.avif$/, '');
    const { data, info } = await sharp(join(dir, file))
      .removeAlpha()
      .raw()
      .toBuffer({ resolveWithObject: true });

    const rgba = cutOutBackground(data, info.width, info.height, info.channels);
    const cut = sharp(rgba, { raw: { width: info.width, height: info.height, channels: 4 } });
    // Se recorta el aire sobrante para que todos los equipos ocupen la misma
    // superficie óptica dentro de su caja cuadrada.
    const trimmed = await cut.trim({ threshold: 1 }).png().toBuffer();

    for (const width of PRODUCT_IMAGE_WIDTHS) {
      const pipeline = sharp(trimmed).resize({ width, withoutEnlargement: true });
      await pipeline.clone().avif({ quality: 62 }).toFile(join(dir, `${slug}-${width}.avif`));
      await pipeline.clone().webp({ quality: 82 }).toFile(join(dir, `${slug}-${width}.webp`));
    }
    console.log(`${brandDir}/${slug}  fondo recortado, ${PRODUCT_IMAGE_WIDTHS.join(', ')} px`);
  }
}
