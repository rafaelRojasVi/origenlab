#!/usr/bin/env node
/**
 * Deriva los tamaños de imagen de producto que usa el sitio.
 *
 * Fuente: los originales del fabricante en `public/products/<marca>/`, con
 * fila de procedencia en `src/data/productImages.ts`. Se aceptan AVIF, JPEG,
 * PNG y WebP. Salida: los mismos recortes a 480 y 960 px de ancho, en AVIF y
 * WebP, junto al original. Al terminar imprime las dimensiones de cada
 * derivado, que son las que el registro declara y `validate:images` comprueba.
 *
 * Fondo: los originales que son recortes de estudio sobre blanco puro se
 * convierten a transparencia por relleno desde los bordes, no por umbral
 * global: así el gris claro de las carcasas y los reflejos internos, que no
 * tocan el borde, se conservan intactos. Sobre el papel del sitio (#FAFAF7) un
 * blanco puro se vería como una caja flotante detrás de cada equipo. Un
 * original cuyo borde **no** es blanco (fotografía ambientada, fondo de color)
 * se deja tal cual: recortarlo inventaría un contorno. Se decide midiendo el
 * borde del lienzo, no a mano. No se retoca, recorta ni recompone nada del
 * equipo fotografiado.
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
const MASTER_EXT = /\.(avif|jpe?g|png|webp)$/i;

/** Luminancia mínima para considerar un píxel parte del fondo de estudio. */
const BACKGROUND_MIN = 246;
/** Margen de suavizado: entre este valor y BACKGROUND_MIN el alfa interpola. */
const BACKGROUND_SOFT = 228;
/** Proporción del borde que tiene que ser blanco para recortar el fondo. */
const BORDER_WHITE_SHARE = 0.9;

function luminanceOf(data, channels, total) {
  const luminance = new Uint8Array(total);
  for (let i = 0; i < total; i += 1) {
    const o = i * channels;
    luminance[i] = (data[o] * 299 + data[o + 1] * 587 + data[o + 2] * 114) / 1000;
  }
  return luminance;
}

/** ¿Es un recorte de estudio sobre blanco? Se mide el perímetro del lienzo. */
function hasStudioBorder(luminance, width, height) {
  let white = 0;
  let count = 0;
  const check = (x, y) => {
    count += 1;
    if (luminance[y * width + x] >= BACKGROUND_MIN) white += 1;
  };
  for (let x = 0; x < width; x += 1) {
    check(x, 0);
    check(x, height - 1);
  }
  for (let y = 1; y < height - 1; y += 1) {
    check(0, y);
    check(width - 1, y);
  }
  return white / count >= BORDER_WHITE_SHARE;
}

/**
 * Relleno por inundación desde los cuatro bordes. Devuelve un buffer RGBA con
 * el fondo conectado al borde convertido en transparente y un alfa progresivo
 * en la orla, para que no queden dientes de sierra en los contornos.
 */
function cutOutBackground(data, luminance, width, height, channels) {
  const total = width * height;
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

const derivedSuffix = new RegExp(`-(${PRODUCT_IMAGE_WIDTHS.join('|')})\\.(avif|webp)$`);
const report = [];

for (const brandDir of brandDirs) {
  const dir = join(baseDir, brandDir);
  const originals = readdirSync(dir).filter(
    (file) => MASTER_EXT.test(file) && !derivedSuffix.test(file),
  );

  for (const file of originals) {
    const slug = file.replace(MASTER_EXT, '');
    const { data, info } = await sharp(join(dir, file))
      .removeAlpha()
      .raw()
      .toBuffer({ resolveWithObject: true });

    const total = info.width * info.height;
    const luminance = luminanceOf(data, info.channels, total);
    const studio = hasStudioBorder(luminance, info.width, info.height);

    let prepared;
    if (studio) {
      const rgba = cutOutBackground(data, luminance, info.width, info.height, info.channels);
      const cut = sharp(rgba, { raw: { width: info.width, height: info.height, channels: 4 } });
      // Se recorta el aire sobrante para que todos los equipos ocupen la misma
      // superficie óptica dentro de su caja.
      prepared = await cut.trim({ threshold: 1 }).png().toBuffer();
    } else {
      prepared = await sharp(join(dir, file)).png().toBuffer();
    }

    const dims = {};
    for (const width of PRODUCT_IMAGE_WIDTHS) {
      const pipeline = sharp(prepared).resize({ width, withoutEnlargement: true });
      const avif = await pipeline.clone().avif({ quality: 62 }).toFile(join(dir, `${slug}-${width}.avif`));
      await pipeline.clone().webp({ quality: 82 }).toFile(join(dir, `${slug}-${width}.webp`));
      dims[width] = { width: avif.width, height: avif.height };
    }
    report.push({ brandDir, slug, studio, master: `${info.width}x${info.height}`, dims });
    console.log(
      `${brandDir}/${slug}  ${studio ? 'fondo recortado' : 'fondo conservado'}  original ${info.width}x${info.height}  ` +
        PRODUCT_IMAGE_WIDTHS.map((w) => `${w}: ${dims[w].width}x${dims[w].height}`).join('  '),
    );
  }
}

console.log('\nPara productImages.ts:');
for (const row of report) {
  const entries = Object.entries(row.dims)
    .map(([w, d]) => `${w}: { width: ${d.width}, height: ${d.height} }`)
    .join(', ');
  console.log(`  ${row.brandDir}/${row.slug}: derivatives: { ${entries} }`);
}
