#!/usr/bin/env node
/**
 * Normaliza los logotipos de marca para el sitio público.
 *
 * Fuentes: `public/email/brands/*-source.*`, descargadas del sitio oficial de
 * cada fabricante (procedencia en `public/email/brands/README.md`).
 * Salida: `public/brands/*.png` a 3x.
 *
 * Tratamiento: recorte del margen, alfa derivado de la luminancia y una sola
 * tinta (ink-800). Todos los logotipos quedan al mismo tono y densidad, que es
 * la condición para que el muro de marcas se lea como un conjunto y no como un
 * collage. Es el mismo criterio ya aplicado en la firma de correo corporativa
 * (`build-email-brand-strip.mjs`), a mayor resolución.
 *
 * Ejecutar: npm run build:brand-logos
 */
import { existsSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import sharp from 'sharp';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const srcDir = join(root, 'public', 'email', 'brands');
const outDir = join(root, 'public', 'brands');

/** Altura de exportación (3x de los 48 px máximos de presentación). */
const EXPORT_HEIGHT = 144;
/** Ganancia del alfa: lleva los tonos medios a trazo sólido. */
const ALPHA_GAIN = 1.6;
/** Tinta única de salida: ink-800. */
const INK = [0x2b, 0x2e, 0x30];

const LOGOS = [
  { source: 'ortoalresa-source.svg', out: 'ortoalresa-logo.png' },
  { source: 'serva-source.png', out: 'serva-logo.png' },
  { source: 'ika-source.png', out: 'ika-logo.png' },
  { source: 'hielscher-source.svg', out: 'hielscher-logo.png' },
  { source: 'ollital-source.jpeg', out: 'ollital-logo.png' },
  { source: 'crtop-source.jpg', out: 'crtop-logo.png' },
];

mkdirSync(outDir, { recursive: true });

for (const { source, out } of LOGOS) {
  const from = join(srcDir, source);
  if (!existsSync(from)) throw new Error(`Falta la fuente ${from}`);

  const isVector = source.endsWith('.svg');

  // 1. Rasterizar (los SVG a alta densidad), aplanar sobre blanco y añadir un
  //    borde: sin él, `trim` toma como fondo el primer píxel y recorta el trazo
  //    en logotipos que llegan al borde del lienzo (caso IKA).
  const flattened = await sharp(from, isVector ? { density: 900 } : undefined)
    .flatten({ background: '#ffffff' })
    .extend({ top: 8, bottom: 8, left: 8, right: 8, background: '#ffffff' })
    .greyscale()
    .toBuffer();

  const { data, info } = await sharp(flattened)
    .trim({ threshold: 14 })
    .resize({ height: EXPORT_HEIGHT, fit: 'inside', withoutEnlargement: false })
    .raw()
    .toBuffer({ resolveWithObject: true });

  // 2. Alfa desde la luminancia con ganancia; el papel desaparece.
  const rgba = Buffer.alloc(info.width * info.height * 4);
  for (let i = 0; i < info.width * info.height; i += 1) {
    const luminance = data[i * info.channels];
    rgba[i * 4] = INK[0];
    rgba[i * 4 + 1] = INK[1];
    rgba[i * 4 + 2] = INK[2];
    rgba[i * 4 + 3] = Math.max(0, Math.min(255, Math.round((255 - luminance) * ALPHA_GAIN)));
  }

  await sharp(rgba, { raw: { width: info.width, height: info.height, channels: 4 } })
    .png({ compressionLevel: 9 })
    .toFile(join(outDir, out));

  console.log(`${out.padEnd(22)} ${info.width}x${info.height}`);
}
