#!/usr/bin/env node
/**
 * Póster de cada modelo con escena 3D: el cuadro de entrada de la historia,
 * quieto, para el nivel bajo («Ver en 3D»), la primera pintura de la página y
 * la imagen social (`og:image`).
 *
 * Sirve `dist/` en un puerto libre, abre
 * `/productos/<familia>/<slug>/?poster=1` (centrifugación: su ficha en
 * `/productos/centrifugas/<slug>/`, ver `pagePath`) en Chromium (WebGL por software) a
 * 1200x900 y guarda `public/products/<slug>-poster.jpg`. `?poster=1` lo
 * entiende `ProductStory.astro`: nivel alto, sin movimiento ni historia, monta
 * de inmediato y deja sólo el equipo sobre el fondo de la página.
 *
 * Recorre `src/content/modelos/*.json` con `showroom_id`; el que no tenga
 * historia (página sin `[data-story]`) o cuya escena no llegue a dibujar se
 * salta con un aviso y no detiene a los demás. Cada póster pesa ≤ 150 kB (baja
 * la calidad JPEG desde 82 hasta cumplirlo).
 *
 * Ejecutar: npm run build && npm run posters   (no forma parte de `validate`:
 * necesita un navegador con WebGL). Después, `npm run build` otra vez para que
 * las páginas incluyan los pósteres nuevos.
 */
import { createReadStream, existsSync, mkdirSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { dirname, extname, join, normalize } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const modelsDir = join(root, 'src', 'content', 'modelos');
const outDir = join(root, 'public', 'products');
const MAX_BYTES = 150_000;
const READY_MS = 60_000;

if (!existsSync(join(root, 'dist'))) {
  console.error('dist/ no existe: ejecute npm run build antes de npm run posters');
  process.exit(1);
}

const models = readdirSync(modelsDir)
  .filter((file) => file.endsWith('.json'))
  .map((file) => JSON.parse(readFileSync(join(modelsDir, file), 'utf8')))
  .filter((entry) => entry.showroom_id);
if (models.length === 0) {
  console.log('Ningún modelo declara showroom_id: nada que generar.');
  process.exit(0);
}

/*
 * Dónde vive la página de cada modelo (`lib/modelos.ts` → `modelHref`):
 * `/productos/<familia>/<slug>/`, salvo centrifugación, que conserva sus fichas
 * en `/productos/centrifugas/<slug>/` con el slug de `src/data/products.ts`
 * (la ficha de la colección y la ficha existente comparten `id`).
 */
const legacyProducts = readFileSync(join(root, 'src', 'data', 'products.ts'), 'utf8');
const legacySlug = (id) => legacyProducts.match(new RegExp(`id:\\s*'${id}',[\\s\\S]*?slug:\\s*'([^']+)'`))?.[1];
const pagePath = (entry) =>
  entry.family === 'centrifugacion'
    ? `/productos/centrifugas/${legacySlug(entry.slug) ?? entry.slug}/`
    : `/productos/${entry.family}/${entry.slug}/`;

/*
 * Servidor estático de `dist/` en el propio proceso, como `qa-interaction.mjs`.
 * No se usa `astro preview`: en Astro 7 se desprende a segundo plano (el proceso
 * hijo sale con código 0 y el servidor queda huérfano, fuera de alcance de
 * `kill()`). Un servidor en proceso se cierra solo con el script.
 */
const dist = join(root, 'dist');
const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.webp': 'image/webp',
  '.avif': 'image/avif',
  '.ico': 'image/x-icon',
  '.woff2': 'font/woff2',
  '.json': 'application/json',
  '.xml': 'application/xml',
  '.txt': 'text/plain; charset=utf-8',
};
const server = createServer((req, res) => {
  const path = normalize(decodeURIComponent(new URL(req.url, 'http://l').pathname)).replace(/^(\.\.[/\\])+/, '');
  let file = join(dist, path);
  if (existsSync(file) && statSync(file).isDirectory()) file = join(file, 'index.html');
  if (!file.startsWith(dist) || !existsSync(file)) {
    res.writeHead(404, { 'content-type': 'text/html' });
    res.end('missing');
    return;
  }
  res.writeHead(200, { 'content-type': MIME[extname(file)] ?? 'application/octet-stream' });
  createReadStream(file).pipe(res);
});
await new Promise((resolve, reject) => {
  server.once('error', reject);
  server.listen(0, '127.0.0.1', resolve);
});
const base = `http://127.0.0.1:${server.address().port}`;
const stop = () => server.close();
process.on('exit', stop);
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => process.exit(130));

let browser;
let failed = 0;
try {
  // WebGL por software, como qa-interaction.mjs: sirve sin GPU.
  browser = await chromium.launch({ args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
  mkdirSync(outDir, { recursive: true });
  const made = [];

  for (const entry of models) {
    const label = `${entry.slug} (${entry.showroom_id})`;
    const context = await browser.newContext({ viewport: { width: 1200, height: 900 }, deviceScaleFactor: 1 });
    const page = await context.newPage();
    try {
      const url = `${base}${pagePath(entry)}?poster=1`;
      const response = await page.goto(url, { waitUntil: 'load' });
      if (!response?.ok()) {
        console.warn(`  salta ${label}: la página responde ${response?.status()} (${url})`);
        continue;
      }
      if ((await page.locator('[data-story]').count()) === 0) {
        console.warn(`  salta ${label}: la página no tiene historia 3D (falta src/lib/showroom/stories/${entry.showroom_id}.ts)`);
        continue;
      }
      try {
        await page.waitForSelector('canvas[data-ready]', { timeout: READY_MS });
      } catch {
        console.warn(`  salta ${label}: la escena no dibujó un cuadro en ${READY_MS / 1000} s (¿falta el fragmento scene-* o WebGL?)`);
        continue;
      }
      // El lienzo funde con opacidad 0,6 s al primer cuadro; un par de cuadros más y se captura.
      await page.waitForFunction(() => getComputedStyle(document.querySelector('[data-story] canvas')).opacity === '1');
      await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));

      let buffer;
      for (const quality of [82, 74, 66, 58, 50, 42]) {
        buffer = await page.screenshot({ type: 'jpeg', quality });
        if (buffer.length <= MAX_BYTES) break;
      }
      const file = join(outDir, `${entry.slug}-poster.jpg`);
      writeFileSync(file, buffer);
      made.push(entry.slug);
      console.log(`  ok ${label} → public/products/${entry.slug}-poster.jpg (${Math.round(buffer.length / 1024)} kB)`);
      if (buffer.length > MAX_BYTES) {
        console.error(`   x ${entry.slug}: pesa más de ${MAX_BYTES / 1000} kB incluso con calidad 42`);
        failed++;
      }
    } catch (error) {
      console.error(`   x ${label}: ${error.message}`);
      failed++;
    } finally {
      await context.close();
    }
  }
  console.log(`\n${made.length} póster(es) generado(s) de ${models.length} modelo(s) con showroom_id.`);
} catch (error) {
  console.error(error.message);
  failed++;
} finally {
  await browser?.close();
  stop();
}
process.exit(failed ? 1 : 0);
