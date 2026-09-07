#!/usr/bin/env node
/**
 * Capturas de revisión del rediseño, a los anchos pedidos en el encargo.
 *
 * Distinto de `qa:screens`, que es la puerta de accesibilidad y recorre 16
 * rutas en tres viewports fijos. Esto produce el material que se mira: página
 * completa a 1440, 1024 y 390, más los recortes concretos que hay que juzgar de
 * cerca (cabecera clara y oscura, familias de equipo, hero móvil, pie).
 *
 * Salida en `design/review-2026-09-06/`.
 *
 * Ejecutar: npm run design:review-shots
 */
import { existsSync, mkdirSync, createReadStream, statSync } from 'node:fs';
import { createServer } from 'node:http';
import { dirname, extname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';
import sharp from 'sharp';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const dist = join(root, 'dist');
const out = join(root, 'design', 'review-2026-09-06');
mkdirSync(out, { recursive: true });

if (!existsSync(dist)) {
  console.error('dist/ no existe: ejecute npm run build primero');
  process.exit(1);
}

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
  '.xml': 'application/xml',
  '.txt': 'text/plain; charset=utf-8',
};

const server = createServer((req, res) => {
  const url = new URL(req.url, 'http://localhost');
  let file = join(dist, decodeURIComponent(url.pathname));
  if (existsSync(file) && statSync(file).isDirectory()) file = join(file, 'index.html');
  if (!existsSync(file)) {
    res.writeHead(404).end('not found');
    return;
  }
  res.writeHead(200, { 'content-type': MIME[extname(file)] ?? 'application/octet-stream' });
  createReadStream(file).pipe(res);
});
await new Promise((r) => server.listen(0, r));
const base = `http://localhost:${server.address().port}`;

const browser = await chromium.launch();

/** Recorre la página para disparar el revelado antes de capturar. */
async function settle(page) {
  await page.evaluate(() => document.fonts.ready);
  await page.evaluate(async () => {
    const root = document.documentElement;
    const previous = root.style.scrollBehavior;
    root.style.scrollBehavior = 'auto';
    const step = Math.round(window.innerHeight * 0.8);
    for (let y = 0; y < document.body.scrollHeight; y += step) {
      window.scrollTo(0, y);
      await new Promise((r) => setTimeout(r, 110));
    }
    window.scrollTo(0, 0);
    await new Promise((r) => setTimeout(r, 700));
    root.style.scrollBehavior = previous;
  });
  await page.waitForLoadState('networkidle');
}

async function shot(route, name, width, height, { fullPage = true, clip, scrollTo } = {}) {
  const page = await browser.newPage({
    viewport: { width, height: height ?? 900 },
    deviceScaleFactor: 2,
  });
  await page.goto(`${base}${route}`, { waitUntil: 'networkidle' });
  await settle(page);
  if (scrollTo !== undefined) {
    await page.evaluate((y) => window.scrollTo(0, y), scrollTo);
    await page.waitForTimeout(500);
  }
  const file = join(out, `${name}.png`);
  await page.screenshot({ path: file, fullPage: clip ? false : fullPage, clip });
  await page.close();
  const meta = await sharp(file).metadata();
  console.log(`${name}.png`.padEnd(42) + `${meta.width}x${meta.height}`);
}

/* -- Página completa a los tres anchos del encargo ------------------------ */
await shot('/', 'home-desktop-1440', 1440, 1000);
await shot('/', 'home-tablet-1024', 1024, 900);
await shot('/', 'home-mobile-390', 390, 844);

/* -- Recortes que hay que juzgar de cerca --------------------------------- */
// Cabecera sobre papel: la marca en su contexto real.
await shot('/', 'header-light-1440', 1440, 300, {
  clip: { x: 0, y: 0, width: 1440, height: 130 },
});
// Cabecera sobre fondo oscuro: la banda de cierre y el pie son las superficies
// invertidas del sitio, y ahí vive el lockup en `tone="paper"`.
await shot('/contacto/', 'footer-dark-1440', 1440, 1000, { fullPage: true });

// Hero móvil y primera banda de marcas.
await shot('/', 'hero-mobile-390', 390, 844, {
  clip: { x: 0, y: 0, width: 390, height: 844 },
});

// Marcas: las seis, con su familia.
await shot('/marcas/', 'marcas-desktop-1440', 1440, 1000);

// Página interna del sistema de marca.
await shot('/logo-lab/', 'logo-lab-1440', 1440, 1000);

await browser.close();
server.close();
console.log(`\nCapturas en design/review-2026-09-06/`);
