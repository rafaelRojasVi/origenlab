#!/usr/bin/env node
/**
 * Póster de cada modelo con escena 3D: el cuadro de entrada de la historia,
 * quieto, para el nivel bajo («Ver en 3D»), la primera pintura de la página y
 * la imagen social (`og:image`). Y su tira de giro para las tarjetas del
 * catálogo (`components/ui/Turntable.astro`): `public/products/<slug>-turntable.webp`,
 * 24 cuadros de 560x420 en una sola fila (una vuelta completa, 15° por cuadro,
 * el cuadro 0 es el ángulo del póster), ≤ 120 kB (baja la calidad WebP y, si no
 * basta, el tamaño del cuadro). Los dos sobre el estudio oscuro (`scene.ts`).
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
 * Ejecutar: npm run build && npm run posters [slug ...] [--no-turntable]   (sin slugs, todos; no forma parte de `validate`:
 * necesita un navegador con WebGL). Después, `npm run build` otra vez para que
 * las páginas incluyan los pósteres nuevos.
 */
import { createReadStream, existsSync, mkdirSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { dirname, extname, join, normalize } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';
import sharp from 'sharp';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const modelsDir = join(root, 'src', 'content', 'modelos');
const outDir = join(root, 'public', 'products');
const MAX_BYTES = 150_000;
const READY_MS = 60_000;
/* Tira de giro: contrato con `components/ui/Turntable.astro` (24 cuadros, fila única, 4:3). */
const STRIP_FRAMES = 24;
const STRIP_MAX_BYTES = 120_000;
const STRIP_SIZES = [
  [560, 420],
  [480, 360],
];
const onlyPosters = process.argv.includes('--no-turntable');
const slugArgs = process.argv.slice(2).filter((arg) => !arg.startsWith('--'));

if (!existsSync(join(root, 'dist'))) {
  console.error('dist/ no existe: ejecute npm run build antes de npm run posters');
  process.exit(1);
}

const models = readdirSync(modelsDir)
  .filter((file) => file.endsWith('.json'))
  .map((file) => JSON.parse(readFileSync(join(modelsDir, file), 'utf8')))
  .filter((entry) => entry.showroom_id)
  .filter((entry) => slugArgs.length === 0 || slugArgs.includes(entry.slug));
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
  let path;
  try {
    path = normalize(decodeURIComponent(new URL(req.url, 'http://l').pathname)).replace(/^(\.\.[/\\])+/, '');
  } catch {
    res.writeHead(400, { 'content-type': 'text/plain' });
    res.end('bad request');
    return;
  }
  let file = join(dist, path);
  if (existsSync(file) && statSync(file).isDirectory()) file = join(file, 'index.html');
  if (!file.startsWith(dist) || !existsSync(file)) {
    res.writeHead(404, { 'content-type': 'text/html' });
    res.end('missing');
    return;
  }
  res.writeHead(200, { 'content-type': MIME[extname(file)] ?? 'application/octet-stream' });
  const stream = createReadStream(file);
  stream.on('error', () => res.destroy());
  stream.pipe(res);
});
await new Promise((resolve, reject) => {
  server.once('error', reject);
  server.listen(0, '127.0.0.1', resolve);
});
const base = `http://127.0.0.1:${server.address().port}`;
const stop = () => server.close();
process.on('exit', stop);

let browser;
for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, async () => {
    await browser?.close().catch(() => {});
    process.exit(130);
  });
}
let failed = 0;
try {
  // WebGL por software, como qa-interaction.mjs: sirve sin GPU.
  browser = await chromium.launch({ args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
  mkdirSync(outDir, { recursive: true });
  const made = [];

  for (const entry of models) {
    const label = `${entry.slug} (${entry.showroom_id})`;
    // Un modelo con historia que se salta es un fallo: su página quedaría sin póster.
    const hasStory = existsSync(join(root, 'src', 'lib', 'showroom', 'stories', `${entry.showroom_id}.ts`));
    const skip = (why) => {
      if (hasStory) {
        console.error(`   x ${label}: ${why}`);
        failed++;
      } else console.warn(`  salta ${label}: ${why}`);
    };
    const context = await browser.newContext({ viewport: { width: 1200, height: 900 }, deviceScaleFactor: 1 });
    const page = await context.newPage();
    try {
      const url = `${base}${pagePath(entry)}?poster=1`;
      const response = await page.goto(url, { waitUntil: 'load' });
      if (!response?.ok()) {
        skip(`la página responde ${response?.status()} (${url})`);
        continue;
      }
      if ((await page.locator('[data-story]').count()) === 0) {
        skip(hasStory ? 'la página no monta su historia 3D' : `la página no tiene historia 3D (falta src/lib/showroom/stories/${entry.showroom_id}.ts)`);
        continue;
      }
      try {
        await page.waitForSelector('canvas[data-ready]', { timeout: READY_MS });
      } catch {
        skip(`la escena no dibujó un cuadro en ${READY_MS / 1000} s (¿falta el fragmento scene-* o WebGL?)`);
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

      if (!onlyPosters) {
        // Tira de giro: el equipo gira 15° por cuadro en el acto (`snapYaw`, publicado por
        // `scene.ts` en `?poster=1`); dos cuadros dibujados y se captura.
        const hasStudio = await page.evaluate(() => typeof window.__studio?.snapYaw === 'function');
        if (!hasStudio) throw new Error('la escena no publica window.__studio (¿scene.ts sin el gancho de pósteres?)');
        const frames = [];
        for (let i = 0; i < STRIP_FRAMES; i++) {
          await page.evaluate(
            (yaw) =>
              new Promise((resolve) => {
                window.__studio.snapYaw(yaw);
                requestAnimationFrame(() => requestAnimationFrame(() => requestAnimationFrame(resolve)));
              }),
            (i * 2 * Math.PI) / STRIP_FRAMES,
          );
          frames.push(await page.screenshot({ type: 'png' }));
        }
        await page.evaluate(() => window.__studio.snapYaw(0));
        let strip;
        let used;
        search: for (const [w, h] of STRIP_SIZES) {
          const tiles = await Promise.all(frames.map((frame) => sharp(frame).resize(w, h).toBuffer()));
          const sheet = sharp({ create: { width: w * STRIP_FRAMES, height: h, channels: 3, background: '#0d0f10' } }).composite(
            tiles.map((input, i) => ({ input, left: i * w, top: 0 })),
          );
          const raw = await sheet.png().toBuffer();
          for (const quality of [70, 62, 54, 46, 38]) {
            strip = await sharp(raw).webp({ quality, effort: 6 }).toBuffer();
            used = `${w}x${h} q${quality}`;
            if (strip.length <= STRIP_MAX_BYTES) break search;
          }
        }
        writeFileSync(join(outDir, `${entry.slug}-turntable.webp`), strip);
        console.log(`  ok ${label} → public/products/${entry.slug}-turntable.webp (${STRIP_FRAMES} cuadros, ${used}, ${Math.round(strip.length / 1024)} kB)`);
        if (strip.length > STRIP_MAX_BYTES) {
          console.error(`   x ${entry.slug}: la tira pesa más de ${STRIP_MAX_BYTES / 1000} kB`);
          failed++;
        }
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
