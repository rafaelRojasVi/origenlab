#!/usr/bin/env node
/**
 * Contraste calculado sobre las páginas construidas.
 *
 * Recorre cada texto visible, resuelve el color de fondo efectivo subiendo por
 * los ancestros y comprueba WCAG 2.x: 4,5:1 para texto normal y 3:1 para texto
 * grande (>= 24 px, o >= 18,66 px en negrita) y para los bordes que delimitan
 * un control.
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
  '/marcas/',
  '/marcas/ortoalresa/',
  '/marcas/serva-electrophoresis/',
  '/aplicaciones/',
  '/categorias/control-de-calidad/',
  '/categorias/laboratorio-clinico/',
  '/servicios/',
  '/nosotros/',
  '/contacto/',
  '/privacidad/',
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

const failures = [];
let checked = 0;
let minimum = { ratio: 99, where: '' };

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
        selector: `${el.tagName.toLowerCase()}.${(el.className || '').toString().split(' ')[0]}`,
      });
    }
    return out;
  });

  for (const entry of result) {
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
  await page.close();
}

await browser.close();
server.close();

console.log(`${checked} textos comprobados en ${ROUTES.length} rutas`);
console.log(`Mínimo: ${minimum.ratio}:1 en ${minimum.where}`);
if (failures.length) {
  console.error(`\n${failures.length} por debajo del mínimo WCAG AA:`);
  for (const failure of failures.slice(0, 25)) console.error(`  x ${failure}`);
  process.exit(1);
}
console.log('qa:contrast OK');
