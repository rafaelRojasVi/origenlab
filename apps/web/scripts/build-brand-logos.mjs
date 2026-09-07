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

/**
 * Las seis marcas aprobadas del sitio público. La lista es cerrada y
 * `validate:brands` comprueba que coincida con `src/data/brands.ts`.
 *
 * `mode` describe cómo llega el original, no cómo queda:
 *
 *   ink       trazo oscuro sobre blanco. El alfa sale de la luminancia.
 *   alpha     trazo claro sobre transparente (PNG). El alfa ya viene dado y se
 *             usa tal cual; derivarlo de la luminancia daría un logotipo vacío.
 *   reversed  trazo claro calado sobre un fondo de color macizo. Se aísla lo
 *             casi blanco con un umbral: el fondo de color tiene luminancia
 *             media y sin umbral se quedaría como una mancha sólida.
 */
const LOGOS = [
  { source: 'ortoalresa-source.svg', out: 'ortoalresa-logo.png', mode: 'ink' },
  { source: 'serva-source.png', out: 'serva-logo.png', mode: 'ink' },
  { source: 'ika-source.png', out: 'ika-logo.png', mode: 'ink' },
  { source: 'hielscher-source.svg', out: 'hielscher-logo.png', mode: 'ink' },
  { source: 'adam-equipment-source.png', out: 'adam-equipment-logo.png', mode: 'alpha' },
  // El original de Löser es un azulejo de color macizo de 70x70 con un borde
  // claro de un par de píxeles. Sin recortarlo, el umbral toma ese borde por
  // trazo y el logotipo sale dentro de un marco.
  { source: 'loeser-source.jpg', out: 'loeser-logo.png', mode: 'reversed', inset: 4 },
];

/** Umbral y ganancia para aislar el trazo calado de su fondo de color. */
const REVERSED_FLOOR = 150;
const REVERSED_GAIN = 4;

mkdirSync(outDir, { recursive: true });

const clamp = (n) => Math.max(0, Math.min(255, Math.round(n)));

for (const { source, out, mode, inset = 0 } of LOGOS) {
  const from = join(srcDir, source);
  if (!existsSync(from)) throw new Error(`Falta la fuente ${from}`);

  const isVector = source.endsWith('.svg');

  /** Recorte previo del borde del original, cuando lo tiene. */
  let input = from;
  if (inset > 0) {
    const meta = await sharp(from).metadata();
    input = await sharp(from)
      .extract({
        left: inset,
        top: inset,
        width: meta.width - inset * 2,
        height: meta.height - inset * 2,
      })
      .toBuffer();
  }

  /**
   * `alpha` conserva el canal alfa del original y sólo lo recorta: el logotipo
   * ya viene calado y aplanarlo sobre blanco lo borraría por completo.
   */
  if (mode === 'alpha') {
    const { data, info } = await sharp(input)
      .ensureAlpha()
      .trim({ threshold: 1 })
      .resize({ height: EXPORT_HEIGHT, fit: 'inside', withoutEnlargement: false })
      .raw()
      .toBuffer({ resolveWithObject: true });

    const rgba = Buffer.alloc(info.width * info.height * 4);
    for (let i = 0; i < info.width * info.height; i += 1) {
      rgba[i * 4] = INK[0];
      rgba[i * 4 + 1] = INK[1];
      rgba[i * 4 + 2] = INK[2];
      rgba[i * 4 + 3] = data[i * info.channels + 3];
    }

    await sharp(rgba, { raw: { width: info.width, height: info.height, channels: 4 } })
      .png({ compressionLevel: 9 })
      .toFile(join(outDir, out));
    console.log(`${out.padEnd(26)} ${info.width}x${info.height}  (alpha)`);
    continue;
  }

  // 1. Rasterizar (los SVG a alta densidad), aplanar sobre el fondo que
  //    corresponda y añadir un borde: sin él, `trim` toma como fondo el primer
  //    píxel y recorta el trazo en logotipos que llegan al borde (caso IKA).
  //    En `reversed` el fondo de relleno es negro, no blanco: rellenar con
  //    blanco crearía un marco que el umbral tomaría por trazo.
  const pad = mode === 'reversed' ? '#000000' : '#ffffff';
  const flattened = await sharp(input, isVector ? { density: 900 } : undefined)
    .flatten({ background: pad })
    .extend({ top: 8, bottom: 8, left: 8, right: 8, background: pad })
    .greyscale()
    .toBuffer();

  const { data, info } = await sharp(flattened)
    .trim({ threshold: 14 })
    .resize({ height: EXPORT_HEIGHT, fit: 'inside', withoutEnlargement: false })
    .raw()
    .toBuffer({ resolveWithObject: true });

  // 2. Alfa desde la luminancia; el fondo desaparece y queda sólo el trazo.
  const rgba = Buffer.alloc(info.width * info.height * 4);
  for (let i = 0; i < info.width * info.height; i += 1) {
    const luminance = data[i * info.channels];
    rgba[i * 4] = INK[0];
    rgba[i * 4 + 1] = INK[1];
    rgba[i * 4 + 2] = INK[2];
    rgba[i * 4 + 3] =
      mode === 'reversed'
        ? clamp((luminance - REVERSED_FLOOR) * REVERSED_GAIN)
        : clamp((255 - luminance) * ALPHA_GAIN);
  }

  // Sólo `reversed` se vuelve a recortar. Recortar los de tinta movería el
  // encuadre que ya tenían y, en el caso de IKA, se come la «I», que es un
  // trazo fino pegado al borde.
  const composed = sharp(rgba, { raw: { width: info.width, height: info.height, channels: 4 } });
  await (mode === 'reversed' ? composed.trim({ threshold: 1 }) : composed)
    .png({ compressionLevel: 9 })
    .toFile(join(outDir, out));

  const final = await sharp(join(outDir, out)).metadata();
  console.log(`${out.padEnd(26)} ${final.width}x${final.height}  (${mode})`);
}
