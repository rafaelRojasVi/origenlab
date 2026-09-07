#!/usr/bin/env node
/**
 * Exploraciones de marca: escribe los tres conceptos y la hoja de comparación.
 *
 * Salida en `design/logo-explorations/`, fuera de `public/`, porque son material
 * de trabajo y no activos del sitio publicado. El concepto elegido se exporta
 * aparte con `npm run build:logo` a `public/logo/`.
 *
 * Ejecutar: npm run design:logo-explorations
 */
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { CONCEPTS, MARK_PALETTE, contrastRatio, lockupSvg, markSvg } from './lib/mark-render.mjs';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const outDir = join(root, 'design', 'logo-explorations');
mkdirSync(outDir, { recursive: true });

const ORDER = ['orbita', 'nucleo', 'muestra'];
const SIZES = [16, 24, 32, 48, 64];

/* -- 1. Archivos por concepto -------------------------------------------- */

for (const id of ORDER) {
  writeFileSync(join(outDir, `concept-${id}-light.svg`), markSvg(id, 'light'));
  writeFileSync(join(outDir, `concept-${id}-dark.svg`), markSvg(id, 'dark'));
  writeFileSync(join(outDir, `concept-${id}-mono.svg`), markSvg(id, 'mono'));
  writeFileSync(join(outDir, `concept-${id}-lockup.svg`), lockupSvg(id, 'light'));
}

/* -- 2. Contraste no textual (WCAG 1.4.11: 3:1) --------------------------- */

const contrast = [
  ['traza tinta / papel', MARK_PALETTE.ink, MARK_PALETTE.paper],
  ['nodo teal-700 / papel', MARK_PALETTE.teal700, MARK_PALETTE.paper],
  ['traza papel / tinta', MARK_PALETTE.paper, MARK_PALETTE.ink],
  ['nodo teal-500 / tinta', MARK_PALETTE.teal500, MARK_PALETTE.ink],
  ['nodo teal-500 / teal-800', MARK_PALETTE.teal500, '#115e59'],
].map(([label, a, b]) => ({ label, ratio: contrastRatio(a, b) }));

/* -- 3. Hoja de comparación ----------------------------------------------- */

const BACKDROPS = [
  ['Papel', MARK_PALETTE.paper, 'light'],
  ['Tinta', MARK_PALETTE.ink, 'dark'],
  ['Marca (teal-800)', '#115e59', 'dark'],
];

function sizeRow(id, scheme) {
  return SIZES.map(
    (s) =>
      `<figure class="size"><div class="size__box">${markSvg(id, scheme, {
        size: s,
        title: '',
        simplified: s <= 24,
      })}</div><figcaption>${s}px</figcaption></figure>`,
  ).join('');
}

function headerMock(id, tone) {
  const dark = tone === 'dark';
  return `<div class="mock mock--${tone}">
    <div class="mock__bar">
      <span class="mock__logo">${markSvg(id, dark ? 'dark' : 'light', {
        size: 30,
        title: '',
      })}<b>OrigenLab</b></span>
      <nav class="mock__nav"><span>Productos</span><span>Aplicaciones</span><span>Marcas</span><span>Servicios</span><span>Nosotros</span></nav>
      <span class="mock__cta">Solicitar cotización</span>
    </div>
  </div>`;
}

function mobileMock(id, tone) {
  const dark = tone === 'dark';
  return `<div class="mock mock--${tone} mock--mobile">
    <div class="mock__bar">
      <span class="mock__logo">${markSvg(id, dark ? 'dark' : 'light', {
        size: 26,
        title: '',
      })}<b>OrigenLab</b></span>
      <span class="mock__burger"></span>
    </div>
  </div>`;
}

const cards = ORDER.map((id) => {
  const c = CONCEPTS[id];
  return `<section class="concept">
    <h2><span class="concept__id">${id}</span> ${c.name}</h2>
    <div class="backdrops">
      ${BACKDROPS.map(
        ([label, bg, scheme]) => `<div class="backdrop" style="background:${bg}">
          <div class="backdrop__mark">${markSvg(id, scheme, { size: 96, title: '' })}</div>
          <span class="backdrop__label" style="color:${
            scheme === 'dark' ? '#b9bcbd' : '#676d71'
          }">${label}</span>
        </div>`,
      ).join('')}
    </div>
    <h3>Tamaños reales sobre papel</h3>
    <div class="sizes">${sizeRow(id, 'light')}</div>
    <h3>Tamaños reales sobre tinta</h3>
    <div class="sizes sizes--dark">${sizeRow(id, 'dark')}</div>
    <h3>Cabecera de escritorio</h3>
    ${headerMock(id, 'light')}
    ${headerMock(id, 'dark')}
    <h3>Cabecera móvil</h3>
    ${mobileMock(id, 'light')}
    ${mobileMock(id, 'dark')}
    <h3>Lockup horizontal</h3>
    <div class="lockup">${lockupSvg(id, 'light')}</div>
    <div class="lockup lockup--dark">${lockupSvg(id, 'dark')}</div>
  </section>`;
}).join('');

const sheet = `<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OrigenLab — comparación de conceptos de marca</title>
<style>
  /* Tipografía real del sitio: la hoja se juzga con la letra con la que se
     publica, no con la de reserva del sistema. Ruta relativa a public/. */
  @font-face {
    font-family: 'Plus Jakarta Sans';
    font-weight: 200 800;
    font-display: block;
    src: url('../../public/fonts/plus-jakarta-sans-latin-wght-normal.woff2') format('woff2-variations');
  }
  @font-face {
    font-family: 'IBM Plex Mono';
    font-weight: 400;
    font-display: block;
    src: url('../../public/fonts/ibm-plex-mono-latin-400-normal.woff2') format('woff2');
  }
  :root { --paper:#fafaf7; --ink:#141617; --hair:#e3e3de; --muted:#676d71; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--paper); color:var(--ink);
         font-family:'Plus Jakarta Sans',ui-sans-serif,system-ui,sans-serif; }
  .wrap { max-width:1180px; margin:0 auto; padding:56px 32px 96px; }
  h1 { font-size:34px; letter-spacing:-.02em; margin:0 0 8px; }
  .lede { color:var(--muted); max-width:70ch; line-height:1.6; margin:0 0 8px; }
  h2 { font-size:24px; letter-spacing:-.015em; margin:0 0 24px;
       padding-bottom:12px; border-bottom:2px solid var(--ink); }
  .concept__id { font-family:ui-monospace,Menlo,monospace; font-size:13px;
       text-transform:uppercase; letter-spacing:.08em; color:var(--muted); }
  h3 { font-family:ui-monospace,Menlo,monospace; font-size:12px; font-weight:500;
       text-transform:uppercase; letter-spacing:.06em; color:var(--muted);
       margin:36px 0 14px; }
  .concept { margin-top:72px; }
  .backdrops { display:grid; grid-template-columns:repeat(3,1fr); gap:16px; }
  .backdrop { aspect-ratio:4/3; display:flex; flex-direction:column;
       align-items:center; justify-content:center; gap:14px; border:1px solid var(--hair); }
  .backdrop__label { font-family:ui-monospace,Menlo,monospace; font-size:11px;
       text-transform:uppercase; letter-spacing:.06em; }
  .sizes { display:flex; align-items:flex-end; gap:28px; flex-wrap:wrap;
       padding:24px; border:1px solid var(--hair); }
  .sizes--dark { background:var(--ink); border-color:#33383a; }
  .sizes--dark figcaption { color:#8d9295; }
  .size { margin:0; display:flex; flex-direction:column; align-items:center; gap:10px; }
  .size__box { display:flex; align-items:flex-end; height:64px; }
  figcaption { font-family:ui-monospace,Menlo,monospace; font-size:11px; color:var(--muted); }
  .mock { border:1px solid var(--hair); margin-bottom:12px; }
  .mock--dark { background:var(--ink); border-color:#33383a; }
  .mock__bar { display:flex; align-items:center; gap:32px; padding:14px 24px; }
  .mock__logo { display:inline-flex; align-items:center; gap:7px; font-size:19px;
       font-weight:700; letter-spacing:-.025em; }
  .mock--light .mock__logo { color:var(--ink); }
  .mock--dark .mock__logo { color:var(--paper); }
  .mock__nav { display:flex; gap:22px; font-size:14px; font-weight:500; margin-left:auto; }
  .mock--light .mock__nav { color:#2b2e30; }
  .mock--dark .mock__nav { color:#b9bcbd; }
  .mock__cta { font-size:14px; font-weight:600; padding:9px 16px; border-radius:2px;
       background:#0f766e; color:#fff; white-space:nowrap; }
  .mock--mobile .mock__bar { gap:0; }
  .mock__burger { margin-left:auto; width:22px; height:2px; background:currentColor;
       box-shadow:0 -7px 0 currentColor, 0 7px 0 currentColor; }
  .mock--light .mock__burger { color:#2b2e30; }
  .mock--dark .mock__burger { color:#ececeb; }
  .lockup { padding:24px; border:1px solid var(--hair); }
  .lockup svg { width:260px; height:auto; }
  .lockup--dark { background:var(--ink); border-color:#33383a; border-top:0; }
  table { border-collapse:collapse; margin-top:16px; font-size:14px; }
  th, td { text-align:left; padding:8px 20px 8px 0; border-bottom:1px solid var(--hair); }
  th { font-family:ui-monospace,Menlo,monospace; font-size:11px; text-transform:uppercase;
       letter-spacing:.06em; color:var(--muted); font-weight:500; }
  .pass { color:#0f766e; font-weight:600; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Marca OrigenLab — tres exploraciones</h1>
  <p class="lede">Cada concepto se presenta sobre papel, tinta y fondo de marca; a 16, 24, 32,
  48 y 64 px reales; y montado en la cabecera de escritorio y móvil. Los tamaños de 16 y 24 px
  usan la variante simplificada, con la traza engrosada.</p>
  <p class="lede">Restricciones que los tres cumplen por construcción: ningún trazo por debajo de
  3 unidades sobre 48, ningún nodo por debajo de radio 4, ninguna opacidad menor que 1.</p>

  <h3>Contraste no textual medido (WCAG 1.4.11 exige 3:1)</h3>
  <table>
    <tr><th>Par</th><th>Ratio</th><th>Estado</th></tr>
    ${contrast
      .map(
        (c) =>
          `<tr><td>${c.label}</td><td>${c.ratio.toFixed(2)}:1</td><td class="${
            c.ratio >= 3 ? 'pass' : ''
          }">${c.ratio >= 3 ? 'cumple' : 'NO CUMPLE'}</td></tr>`,
      )
      .join('')}
  </table>

  ${cards}
</div>
</body>
</html>`;

writeFileSync(join(outDir, 'comparison-sheet.html'), sheet);

console.log(`Exploraciones en design/logo-explorations/`);
for (const id of ORDER) console.log(`  concept-${id}-{light,dark,mono,lockup}.svg`);
console.log('  comparison-sheet.html');
console.log('\nContraste no textual:');
for (const c of contrast) {
  console.log(`  ${c.label.padEnd(26)} ${c.ratio.toFixed(2)}:1 ${c.ratio >= 3 ? 'ok' : 'FALLA'}`);
}
