#!/usr/bin/env node
/**
 * Genera los activos sociales y los iconos de aplicación desde SVG.
 *
 * WhatsApp, LinkedIn y Facebook no renderizan SVG en las previsualizaciones, e
 * iOS ignora un `apple-touch-icon` en SVG; por eso ambos se exportan a PNG.
 * Las tipografías se incrustan como trazos vía SVG para no depender de las
 * fuentes del sistema durante el rasterizado.
 *
 * Ejecutar: npm run build:social
 */
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import sharp from 'sharp';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const ogDir = join(root, 'public', 'og');
mkdirSync(ogDir, { recursive: true });

const PAPER = '#FAFAF7';
const INK = '#141617';
const INK_600 = '#54595D';
const TEAL = '#0F766E';

const fontDir = join(root, 'public', 'fonts');
const sans = `file://${join(fontDir, 'plus-jakarta-sans-latin-wght-normal.woff2')}`;
const mono = `file://${join(fontDir, 'ibm-plex-mono-latin-400-normal.woff2')}`;

/** Marca atómica: mismas elipses que el logotipo estático del sitio. */
function atom(cx, cy, scale, stroke = TEAL) {
  const rings = [0, 60, -60]
    .map(
      (angle) =>
        `<ellipse cx="0" cy="0" rx="${3.31 * scale}" ry="${1.12 * scale}" transform="rotate(${angle})" fill="none" stroke="${stroke}" stroke-width="${0.09 * scale}" opacity="0.35"/>`,
    )
    .join('');
  const bodies = [
    [2.6, 0.55],
    [-1.9, -1.5],
    [-0.7, 1.7],
  ]
    .map(([x, y]) => `<circle cx="${x * scale}" cy="${y * scale}" r="${0.16 * scale}" fill="${stroke}"/>`)
    .join('');
  return `<g transform="translate(${cx} ${cy})">${rings}<circle cx="0" cy="0" r="${0.3 * scale}" fill="none" stroke="${stroke}" stroke-width="${0.055 * scale}" opacity="0.4"/><circle cx="0" cy="0" r="${0.22 * scale}" fill="${stroke}"/>${bodies}</g>`;
}

const ogSvg = `<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">
  <defs>
    <style>
      @font-face { font-family: 'PJS'; src: url('${sans}') format('woff2-variations'); font-weight: 200 800; }
      @font-face { font-family: 'IPM'; src: url('${mono}') format('woff2'); font-weight: 400; }
    </style>
  </defs>
  <rect width="1200" height="630" fill="${PAPER}"/>
  <rect x="0" y="0" width="1200" height="4" fill="${TEAL}"/>
  ${atom(96, 92, 13)}
  <text x="140" y="103" font-family="PJS" font-size="34" font-weight="700" fill="${INK}" letter-spacing="-0.8">OrigenLab</text>
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

/** apple-touch-icon: la marca sobre tinta, con margen de seguridad iOS. */
const touchSvg = `<svg xmlns="http://www.w3.org/2000/svg" width="180" height="180" viewBox="0 0 180 180">
  <rect width="180" height="180" fill="${INK}"/>
  ${atom(90, 90, 22, '#2DD4BF')}
</svg>`;
await sharp(Buffer.from(touchSvg), { density: 300 })
  .resize(180, 180)
  .png({ compressionLevel: 9 })
  .toFile(join(root, 'public', 'apple-touch-icon.png'));
console.log('apple-touch-icon.png  180x180');
