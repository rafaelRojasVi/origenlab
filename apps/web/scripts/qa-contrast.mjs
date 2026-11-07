#!/usr/bin/env node
/**
 * Contraste calculado sobre las páginas construidas.
 *
 * Recorre cada texto visible, resuelve el color de fondo efectivo subiendo por
 * los ancestros y comprueba WCAG 2.x: 4,5:1 para texto normal y 3:1 para texto
 * grande (>= 24 px, o >= 18,66 px en negrita) y para los bordes que delimitan
 * un control. Además mide el borde de los botones secundarios, de WhatsApp y de
 * icono (>= 3:1 contra el fondo del padre, WCAG 1.4.11) y el anillo de foco de
 * cada elemento al tabular (>= 3:1 y >= 2 px).
 *
 * Ejecutar: node scripts/qa-contrast.mjs
 */
import { createServer } from 'node:http';
import { createReadStream, existsSync, statSync } from 'node:fs';
import { dirname, extname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const dist = join(root, 'dist');

const ROUTES = [
  '/',
  '/productos/',
  '/productos/centrifugas/',
  '/productos/centrifugas/biocen-22-r/',
  '/productos/sonicacion/',
  '/productos/sonicacion/hielscher-up400st/',
  '/productos/centrifugas/digicen-22-r/',
  '/marcas/',
  '/marcas/ortoalresa/',
  '/marcas/serva-electrophoresis/',
  '/aplicaciones/',
  '/categorias/control-de-calidad/',
  '/categorias/laboratorio-clinico/',
  '/servicios/',
  '/como-comprar/',
  '/nosotros/',
  '/contacto/',
  '/privacidad/',
  '/cookies/',
  '/aviso-legal/',
  '/404.html',
];

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.webp': 'image/webp',
  '.avif': 'image/avif',
  '.ico': 'image/x-icon',
  '.woff2': 'font/woff2',
};

const server = createServer((req, res) => {
  let file = join(dist, decodeURIComponent(new URL(req.url, 'http://l').pathname));
  if (existsSync(file) && statSync(file).isDirectory()) file = join(file, 'index.html');
  if (!existsSync(file)) {
    res.writeHead(404);
    res.end();
    return;
  }
  res.writeHead(200, { 'content-type': MIME[extname(file)] ?? 'application/octet-stream' });
  createReadStream(file).pipe(res);
});
await new Promise((resolve) => server.listen(0, resolve));
const base = `http://localhost:${server.address().port}`;
const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });

/**
 * WCAG 1.4.11: el borde de un control que no lleva relleno debe separarse del
 * fondo con 3:1. El tablero de diseño usaba hairline-strong (1,58:1 sobre
 * papel); aquí se exige el 3:1 completo, así que el borde es ink-500 sobre
 * claro y onink-300 sobre tinta.
 */
const CONTROL_EDGE_MIN = 3;
const FOCUS_RING_MIN = 3;
const FOCUS_RING_WIDTH_MIN = 2;
const FOCUS_TABS = 15;
const FOCUS_CONTROLS = '.btn, .btn-tertiary, .link-arrow';

const failures = [];
let checked = 0;
let minimum = { ratio: 99, where: '' };
let edgesChecked = 0;
let minimumEdge = { ratio: 99, where: '' };
let ringsChecked = 0;
let minimumRing = { ratio: 99, where: '' };

for (const route of ROUTES) {
  const page = await context.newPage();
  await page.goto(base + route, { waitUntil: 'load' });

  const result = await page.evaluate(() => {
    const parse = (color) => {
      const m = color.match(/rgba?\(([^)]+)\)/);
      if (!m) return null;
      const parts = m[1].split(',').map((v) => parseFloat(v));
      return { r: parts[0], g: parts[1], b: parts[2], a: parts[3] ?? 1 };
    };
    const luminance = ({ r, g, b }) => {
      const f = (v) => {
        const s = v / 255;
        return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
      };
      return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
    };
    const over = (fg, bg) => ({
      r: fg.r * fg.a + bg.r * (1 - fg.a),
      g: fg.g * fg.a + bg.g * (1 - fg.a),
      b: fg.b * fg.a + bg.b * (1 - fg.a),
      a: 1,
    });
    const contrast = (a, b) => {
      const la = luminance(a);
      const lb = luminance(b);
      return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
    };

    const backgroundOf = (el) => {
      let base = { r: 255, g: 255, b: 255, a: 1 };
      const stack = [];
      for (let node = el; node; node = node.parentElement) {
        const bg = parse(getComputedStyle(node).backgroundColor);
        if (bg && bg.a > 0) stack.push(bg);
        if (bg && bg.a === 1) break;
      }
      for (const layer of stack.reverse()) base = over(layer, base);
      return base;
    };

    const out = [];
    const edges = [];
    for (const el of document.querySelectorAll('.btn-secondary, .btn-whatsapp, .btn-icon')) {
      const rect = el.getBoundingClientRect();
      const style = getComputedStyle(el);
      if (rect.width === 0 || rect.height === 0 || style.visibility === 'hidden') continue;
      const edge = parse(style.borderTopColor);
      if (!edge || parseFloat(style.borderTopWidth) === 0) continue;
      const bg = backgroundOf(el.parentElement);
      edges.push({
        ratio: Math.round(contrast(over(edge, bg), bg) * 100) / 100,
        selector: `${el.tagName.toLowerCase()}.${(el.className || '').toString().split(' ').slice(0, 2).join('.')}`,
      });
    }
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const seen = new Set();
    let node;
    while ((node = walker.nextNode())) {
      const text = node.textContent?.trim();
      if (!text) continue;
      const el = node.parentElement;
      if (!el || seen.has(el)) continue;
      seen.add(el);
      const style = getComputedStyle(el);
      if (style.visibility === 'hidden' || style.display === 'none') continue;
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) continue;
      if (el.closest('.sr-only') || el.classList.contains('skip-link')) continue;
      // WCAG 1.4.3 exime los componentes inactivos.
      if (el.closest(':disabled, [aria-disabled="true"]')) continue;

      const fg = parse(style.color);
      if (!fg) continue;
      const bg = backgroundOf(el);
      const ratio = contrast(over(fg, bg), bg);
      const size = parseFloat(style.fontSize);
      const weight = Number(style.fontWeight) || 400;
      const large = size >= 24 || (size >= 18.66 && weight >= 700);
      const required = large ? 3 : 4.5;
      out.push({
        ratio: Math.round(ratio * 100) / 100,
        required,
        size,
        pass: ratio >= required,
        sample: text.slice(0, 40),
        band: Boolean(el.closest('.band')),
        selector: `${el.tagName.toLowerCase()}.${(el.className || '').toString().split(' ')[0]}`,
      });
    }
    return { out, edges, hasBand: document.querySelector('.band') !== null };
  });

  for (const edge of result.edges) {
    edgesChecked += 1;
    if (edge.ratio < minimumEdge.ratio) minimumEdge = { ratio: edge.ratio, where: `${route} ${edge.selector}` };
    if (edge.ratio < CONTROL_EDGE_MIN) {
      failures.push(`${route} ${edge.selector} borde ${edge.ratio}:1 (mínimo ${CONTROL_EDGE_MIN}:1)`);
    }
  }

  // La banda de cierre (fondo tinta, botones sobre tinta) debe haberse medido: título, texto y los dos botones.
  if (result.hasBand) {
    const inBand = result.out.filter((entry) => entry.band).length;
    if (inBand < 4) failures.push(`${route}: la banda de cierre sólo midió ${inBand} textos (mínimo 4: título, texto, dos botones)`);
  }

  for (const entry of result.out) {
    checked += 1;
    if (entry.ratio < minimum.ratio) {
      minimum = { ratio: entry.ratio, where: `${route} ${entry.selector} "${entry.sample}"` };
    }
    if (!entry.pass) {
      failures.push(
        `${route} ${entry.selector} ${entry.ratio}:1 (necesita ${entry.required}:1, ${entry.size}px) "${entry.sample}"`,
      );
    }
  }

  // Anillo de foco: tabular y medir el contorno de cada elemento enfocado; después
  // enfocar con `.focus()` cada botón y enlace con flecha (barra fija, banda de
  // cierre, enlaces en página) para medir también los anillos sobre tinta.
  const controlCount = await page.evaluate((sel) => document.querySelectorAll(sel).length, FOCUS_CONTROLS);
  for (let i = 0; i < FOCUS_TABS + controlCount; i += 1) {
    const controlIndex = i - FOCUS_TABS;
    if (controlIndex < 0) await page.keyboard.press('Tab');
    const ring = await page.evaluate(([sel, idx]) => {
      if (idx >= 0) document.querySelectorAll(sel)[idx]?.focus();
      const el = document.activeElement;
      if (!el || el === document.body) return null;
      const style = getComputedStyle(el);
      if (style.outlineStyle === 'none') return null;
      const parse = (color) => {
        const m = color.match(/rgba?\(([^)]+)\)/);
        if (!m) return null;
        const p = m[1].split(',').map((v) => parseFloat(v));
        return { r: p[0], g: p[1], b: p[2], a: p[3] ?? 1 };
      };
      const lum = ({ r, g, b }) => {
        const f = (v) => ((v / 255) <= 0.03928 ? v / 255 / 12.92 : ((v / 255 + 0.055) / 1.055) ** 2.4);
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
      };
      let bg = { r: 255, g: 255, b: 255 };
      const stack = [];
      for (let node = el.parentElement; node; node = node.parentElement) {
        const c = parse(getComputedStyle(node).backgroundColor);
        if (c && c.a > 0) stack.push(c);
        if (c && c.a === 1) break;
      }
      for (const c of stack.reverse()) {
        bg = { r: c.r * c.a + bg.r * (1 - c.a), g: c.g * c.a + bg.g * (1 - c.a), b: c.b * c.a + bg.b * (1 - c.a) };
      }
      const fg = parse(style.outlineColor);
      if (!fg) return null;
      const a = lum(fg);
      const b = lum(bg);
      return {
        ratio: Math.round(((Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)) * 100) / 100,
        width: parseFloat(style.outlineWidth),
        selector: `${el.tagName.toLowerCase()}.${(el.className || '').toString().split(' ')[0]}`,
      };
    }, [FOCUS_CONTROLS, controlIndex]);
    if (!ring) continue;
    ringsChecked += 1;
    if (ring.ratio < minimumRing.ratio) minimumRing = { ratio: ring.ratio, where: `${route} ${ring.selector}` };
    if (ring.ratio < FOCUS_RING_MIN || ring.width < FOCUS_RING_WIDTH_MIN) {
      failures.push(
        `${route} ${ring.selector} anillo de foco ${ring.ratio}:1, ${ring.width}px (mínimo ${FOCUS_RING_MIN}:1 y ${FOCUS_RING_WIDTH_MIN}px)`,
      );
    }
  }
  await page.close();
}

await browser.close();
server.close();

console.log(`${checked} textos comprobados en ${ROUTES.length} rutas`);
console.log(`Mínimo: ${minimum.ratio}:1 en ${minimum.where}`);
console.log(`${edgesChecked} bordes de control comprobados; mínimo ${minimumEdge.ratio}:1 en ${minimumEdge.where || '-'}`);
console.log(`${ringsChecked} anillos de foco comprobados; mínimo ${minimumRing.ratio}:1 en ${minimumRing.where || '-'}`);
if (failures.length) {
  console.error(`\n${failures.length} por debajo del mínimo WCAG AA:`);
  for (const failure of failures.slice(0, 25)) console.error(`  x ${failure}`);
  process.exit(1);
}
console.log('qa:contrast OK');
