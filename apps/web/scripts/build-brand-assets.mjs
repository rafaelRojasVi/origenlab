#!/usr/bin/env node
/**
 * Construye todo el sistema de marca desde una sola geometría.
 *
 * Sustituye a `build-social-assets.mjs`, que volvía a dibujar el átomo a mano
 * dentro del SVG social: había dos marcas distintas en el repositorio, la del
 * encabezado y la de la previsualización social, y sólo se parecían de lejos.
 * Aquí la fuente es `scripts/lib/mark-geometry.mjs` y todo lo demás se deriva.
 *
 * Salidas:
 *   public/logo/*.svg              entrega vectorial (marca, lockups, una tinta)
 *   public/favicon.svg|.ico        pestaña del navegador
 *   public/apple-touch-icon.png    iOS (ignora SVG)
 *   public/og/origenlab-og.svg|png previsualización social (las redes no leen SVG)
 *   src/lib/logo/generatedMark.ts  constantes que consumen los componentes Astro
 *
 * Ejecutar: npm run build:brand
 */
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import sharp from 'sharp';
import { CONCEPTS, MARK_PALETTE, lockupSvg, markBody, markSvg } from './lib/mark-render.mjs';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const logoDir = join(root, 'public', 'logo');
const ogDir = join(root, 'public', 'og');
mkdirSync(logoDir, { recursive: true });
mkdirSync(ogDir, { recursive: true });

/** Concepto elegido. Ver docs/logo-system.md para el porqué. */
const SELECTED = 'orbita';

const PAPER = MARK_PALETTE.paper;
const INK = MARK_PALETTE.ink;
const INK_600 = '#54595D';
const TEAL = MARK_PALETTE.teal700;

const fontDir = join(root, 'public', 'fonts');
const sans = `file://${join(fontDir, 'plus-jakarta-sans-latin-wght-normal.woff2')}`;
const mono = `file://${join(fontDir, 'ibm-plex-mono-latin-400-normal.woff2')}`;

/* -- 1. Entrega vectorial ------------------------------------------------- */

const files = {
  'origenlab-mark-light.svg': markSvg(SELECTED, 'light'),
  'origenlab-mark-dark.svg': markSvg(SELECTED, 'dark'),
  'origenlab-mark-mono.svg': markSvg(SELECTED, 'mono'),
  'origenlab-mark-favicon.svg': markSvg(SELECTED, 'light', { simplified: true }),
  'origenlab-lockup-light.svg': lockupSvg(SELECTED, 'light'),
  'origenlab-lockup-dark.svg': lockupSvg(SELECTED, 'dark'),
  'origenlab-lockup-stacked-light.svg': lockupSvg(SELECTED, 'light', { stacked: true }),
  'origenlab-lockup-stacked-dark.svg': lockupSvg(SELECTED, 'dark', { stacked: true }),
};
for (const [name, svg] of Object.entries(files)) {
  writeFileSync(join(logoDir, name), svg);
  console.log(`logo/${name}`);
}

/* -- 2. Favicon ------------------------------------------------------------ */

/**
 * El favicon usa la variante simplificada y sobre tinta: en una pestaña la
 * marca compite con el favicon del vecino, y un fondo propio la separa. El
 * radio evita la esquina viva a 16 px, donde un cuadrado exacto se ve sucio.
 */
const faviconSvg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" width="48" height="48" role="img" aria-label="OrigenLab"><title>OrigenLab</title><rect width="48" height="48" rx="9" fill="${INK}"/><g transform="translate(24 24) scale(0.82) translate(-24 -24)">${markBody(
  SELECTED,
  'dark',
  { simplified: true },
)}</g></svg>`;
writeFileSync(join(root, 'public', 'favicon.svg'), faviconSvg);
console.log('favicon.svg');

const faviconPng = await sharp(Buffer.from(faviconSvg), { density: 600 })
  .resize(32, 32)
  .png({ compressionLevel: 9 })
  .toBuffer();

/**
 * ICO mínimo con un PNG de 32x32 dentro. El formato admite PNG incrustado
 * desde Vista, y es lo que entienden todos los navegadores que siguen pidiendo
 * `favicon.ico` por convención.
 */
function icoFromPng(png, size) {
  const header = Buffer.alloc(6);
  header.writeUInt16LE(0, 0); // reservado
  header.writeUInt16LE(1, 2); // tipo: icono
  header.writeUInt16LE(1, 4); // número de imágenes
  const entry = Buffer.alloc(16);
  entry.writeUInt8(size === 256 ? 0 : size, 0);
  entry.writeUInt8(size === 256 ? 0 : size, 1);
  entry.writeUInt8(0, 2); // paleta
  entry.writeUInt8(0, 3); // reservado
  entry.writeUInt16LE(1, 4); // planos
  entry.writeUInt16LE(32, 6); // bits por píxel
  entry.writeUInt32LE(png.length, 8);
  entry.writeUInt32LE(header.length + entry.length, 12);
  return Buffer.concat([header, entry, png]);
}
writeFileSync(join(root, 'public', 'favicon.ico'), icoFromPng(faviconPng, 32));
console.log('favicon.ico  32x32');

/* -- 3. apple-touch-icon --------------------------------------------------- */

const touchSvg = `<svg xmlns="http://www.w3.org/2000/svg" width="180" height="180" viewBox="0 0 180 180"><rect width="180" height="180" fill="${INK}"/><g transform="translate(90 90) scale(2.6) translate(-24 -24)">${markBody(
  SELECTED,
  'dark',
)}</g></svg>`;
await sharp(Buffer.from(touchSvg), { density: 300 })
  .resize(180, 180)
  .png({ compressionLevel: 9 })
  .toFile(join(root, 'public', 'apple-touch-icon.png'));
console.log('apple-touch-icon.png  180x180');

/* -- 4. Previsualización social ------------------------------------------- */

const ogSvg = `<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">
  <defs>
    <style>
      @font-face { font-family: 'PJS'; src: url('${sans}') format('woff2-variations'); font-weight: 200 800; }
      @font-face { font-family: 'IPM'; src: url('${mono}') format('woff2'); font-weight: 400; }
    </style>
  </defs>
  <rect width="1200" height="630" fill="${PAPER}"/>
  <rect x="0" y="0" width="1200" height="4" fill="${TEAL}"/>
  <g transform="translate(96 68) scale(1.15)">${markBody(SELECTED, 'light')}</g>
  <text x="164" y="105" font-family="PJS" font-size="34" font-weight="700" fill="${INK}" letter-spacing="-0.8">OrigenLab</text>
  <line x1="96" y1="176" x2="1104" y2="176" stroke="#E3E3DE" stroke-width="1"/>
  <text x="96" y="312" font-family="PJS" font-size="64" font-weight="600" fill="${INK}" letter-spacing="-1.4">Equipos de laboratorio,</text>
  <text x="96" y="386" font-family="PJS" font-size="64" font-weight="600" fill="${INK}" letter-spacing="-1.4">cotizados con criterio técnico.</text>
  <text x="96" y="466" font-family="PJS" font-size="30" font-weight="400" fill="${INK_600}">Venta de equipos para laboratorios de servicio e investigación.</text>
  <line x1="96" y1="530" x2="1104" y2="530" stroke="#E3E3DE" stroke-width="1"/>
  <text x="96" y="574" font-family="IPM" font-size="24" fill="${INK_600}" letter-spacing="0.8">VALDIVIA, CHILE</text>
  <text x="1104" y="574" text-anchor="end" font-family="IPM" font-size="24" fill="${INK_600}" letter-spacing="0.8">ORIGENLAB.CL</text>
</svg>`;
writeFileSync(join(ogDir, 'origenlab-og.svg'), ogSvg);
await sharp(Buffer.from(ogSvg), { density: 144 })
  .resize(1200, 630)
  .png({ compressionLevel: 9 })
  .toFile(join(ogDir, 'origenlab-og.png'));
console.log('og/origenlab-og.png  1200x630');

/* -- 5. Constantes para los componentes Astro ------------------------------ */

const c = CONCEPTS[SELECTED];
const ts = `/**
 * GENERADO por scripts/build-brand-assets.mjs. No editar a mano.
 *
 * La geometría vive en scripts/lib/mark-geometry.mjs. Se emite a TypeScript
 * para que los componentes Astro dibujen exactamente la misma marca que los
 * archivos de public/logo/, sin repetir el trazado y sin poder divergir.
 *
 * Regenerar: npm run build:brand
 */

/** Lado del lienzo. El viewBox es \`0 0 48 48\`. */
export const MARK_CANVAS = 48;

/** Trayectoria: lemniscata de la coreografía en ocho de los tres cuerpos. */
export const MARK_PATH = ${JSON.stringify(c.path)};

/** Los tres cuerpos, en el orden en que los recorre la trayectoria. */
export const MARK_NODES: readonly (readonly [number, number])[] = ${JSON.stringify(c.nodes)};

/** Peso relativo de cada nodo: crece de izquierda a derecha (muestra a resultado). */
export const MARK_NODE_SCALE: readonly number[] = ${JSON.stringify(c.nodeScale)};

export const MARK_STROKE_WIDTH = ${c.strokeWidth};
export const MARK_NODE_RADIUS = ${c.nodeRadius};

/** Refuerzo para 24 px e inferiores: sin él la traza cae por debajo del píxel. */
export const MARK_SMALL_STROKE_MULTIPLIER = 1.25;
export const MARK_SMALL_NODE_MULTIPLIER = 1.12;
`;
mkdirSync(join(root, 'src', 'lib', 'logo'), { recursive: true });
writeFileSync(join(root, 'src', 'lib', 'logo', 'generatedMark.ts'), ts);
console.log('src/lib/logo/generatedMark.ts');
