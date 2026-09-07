#!/usr/bin/env node
/**
 * Captura una página local a PNG. Herramienta de revisión, no una puerta.
 *
 * Sirve un directorio por HTTP en vez de abrir `file://` porque las fuentes
 * auto-hospedadas y los SVG referenciados no se cargan igual bajo `file://`, y
 * una hoja de marca juzgada sin su tipografía no sirve para decidir nada.
 *
 * Uso:
 *   node scripts/shoot.mjs <ruta-html-relativa-a-la-raíz-web> <salida.png> [ancho] [alto]
 *
 * Ejemplos:
 *   node scripts/shoot.mjs design/logo-explorations/comparison-sheet.html \
 *     design/logo-explorations/comparison-sheet.png 1280
 */
import { createReadStream, existsSync, mkdirSync, statSync } from 'node:fs';
import { createServer } from 'node:http';
import { dirname, extname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const webRoot = join(dirname(fileURLToPath(import.meta.url)), '..');
/**
 * Raíz que se sirve. `SHOOT_ROOT=dist` para capturar el sitio construido, donde
 * las rutas de los activos son absolutas y sólo resuelven si `dist/` es la raíz.
 */
const root = process.env.SHOOT_ROOT
  ? join(webRoot, process.env.SHOOT_ROOT)
  : webRoot;

const [pageRel, outRel, widthArg, heightArg] = process.argv.slice(2);
if (!pageRel || !outRel) {
  console.error('uso: node scripts/shoot.mjs <html> <salida.png> [ancho] [alto]');
  process.exit(1);
}

const width = Number(widthArg) || 1280;
const height = Number(heightArg) || 900;
const fullPage = !heightArg;

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.avif': 'image/avif',
  '.ico': 'image/x-icon',
  '.woff2': 'font/woff2',
};

const server = createServer((req, res) => {
  const url = new URL(req.url, 'http://localhost');
  let file = join(root, decodeURIComponent(url.pathname));
  if (existsSync(file) && statSync(file).isDirectory()) file = join(file, 'index.html');
  if (!existsSync(file)) {
    res.writeHead(404).end('not found');
    return;
  }
  res.writeHead(200, { 'content-type': MIME[extname(file)] ?? 'application/octet-stream' });
  createReadStream(file).pipe(res);
});

await new Promise((r) => server.listen(0, r));
const port = server.address().port;

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 2 });
await page.goto(`http://localhost:${port}/${pageRel}`, { waitUntil: 'networkidle' });
await page.evaluate(() => document.fonts.ready);

/**
 * Recorrido previo. El revelado al desplazarse sólo muestra un bloque cuando
 * entra en el viewport, y una captura de página completa no desplaza nada: sin
 * este paso, media portada sale en blanco. Se desactiva `scroll-behavior:
 * smooth` durante el recorrido, o cada salto reinicia la animación del
 * anterior y nunca se llega al pie.
 */
await page.evaluate(async () => {
  const root = document.documentElement;
  const previous = root.style.scrollBehavior;
  root.style.scrollBehavior = 'auto';
  const step = Math.round(window.innerHeight * 0.8);
  for (let y = 0; y < document.body.scrollHeight; y += step) {
    window.scrollTo(0, y);
    await new Promise((resolve) => setTimeout(resolve, 110));
  }
  window.scrollTo(0, 0);
  await new Promise((resolve) => setTimeout(resolve, 700));
  root.style.scrollBehavior = previous;
});
await page.waitForLoadState('networkidle');

const out = resolve(webRoot, outRel);
mkdirSync(dirname(out), { recursive: true });
await page.screenshot({ path: out, fullPage });

await browser.close();
server.close();
console.log(`${outRel} (${width}px${fullPage ? ', página completa' : `x${height}`})`);
