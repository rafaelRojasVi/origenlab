#!/usr/bin/env node
/**
 * Póster de cada modelo con escena 3D: el cuadro de entrada de la historia,
 * quieto, para el nivel bajo («Ver en 3D»), la primera pintura de la página y
 * la imagen social (`og:image`).
 *
 * Levanta `astro preview` sobre `dist/` en un puerto libre, abre
 * `/productos/<familia>/<slug>/?poster=1` en Chromium (WebGL por software) a
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
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { dirname, join } from 'node:path';
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

const freePort = () =>
  new Promise((resolve, reject) => {
    const probe = createServer();
    probe.once('error', reject);
    probe.listen(0, '127.0.0.1', () => {
      const { port } = probe.address();
      probe.close(() => resolve(port));
    });
  });

const port = await freePort();
const base = `http://127.0.0.1:${port}`;
const preview = spawn('npx', ['astro', 'preview', '--host', '127.0.0.1', '--port', String(port)], {
  cwd: root,
  stdio: ['ignore', 'ignore', 'inherit'],
});
const stop = () => preview.kill();
process.on('exit', stop);

let browser;
let failed = 0;
try {
  for (let tries = 0; ; tries++) {
    try {
      if ((await fetch(`${base}/`)).ok) break;
    } catch {}
    if (tries > 100) throw new Error('astro preview no respondió en 30 s');
    await new Promise((resolve) => setTimeout(resolve, 300));
  }

  // WebGL por software, como qa-interaction.mjs: sirve sin GPU.
  browser = await chromium.launch({ args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
  mkdirSync(outDir, { recursive: true });
  const made = [];

  for (const entry of models) {
    const label = `${entry.slug} (${entry.showroom_id})`;
    const context = await browser.newContext({ viewport: { width: 1200, height: 900 }, deviceScaleFactor: 1 });
    const page = await context.newPage();
    try {
      const url = `${base}/productos/${entry.family}/${entry.slug}/?poster=1`;
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
