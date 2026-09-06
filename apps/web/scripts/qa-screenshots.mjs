#!/usr/bin/env node
/**
 * QA visual y de accesibilidad sobre `dist/`.
 *
 * Levanta un servidor estático sobre la salida del build y recorre las rutas
 * públicas en tres viewports, capturando pantalla completa y registrando:
 * peticiones externas, errores de consola, desbordes horizontales, jerarquía de
 * encabezados, objetivos táctiles pequeños y textos por debajo de 13 px.
 *
 * Ejecutar: npm run qa:screens [-- --routes=/,/productos]
 */
import { createServer } from 'node:http';
import { createReadStream, existsSync, mkdirSync, statSync, writeFileSync } from 'node:fs';
import { dirname, extname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const dist = join(root, 'dist');
const outDir = process.env.QA_OUT ?? join(root, '.qa');

const ROUTES = [
  ['home', '/'],
  ['productos', '/productos/'],
  ['familia-centrifugas', '/productos/centrifugas/'],
  ['producto-biocen-22-r', '/productos/centrifugas/biocen-22-r/'],
  ['marcas', '/marcas/'],
  ['marca-ortoalresa', '/marcas/ortoalresa/'],
  ['marca-serva', '/marcas/serva-electrophoresis/'],
  ['aplicaciones', '/aplicaciones/'],
  ['categoria-control-calidad', '/categorias/control-de-calidad/'],
  ['categoria-laboratorio-clinico', '/categorias/laboratorio-clinico/'],
  ['servicios', '/servicios/'],
  ['nosotros', '/nosotros/'],
  ['contacto', '/contacto/'],
  ['404', '/404.html'],
];

const VIEWPORTS = [
  ['mobile', 375, 812],
  ['tablet', 768, 1024],
  ['desktop', 1440, 1000],
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
  '.xml': 'application/xml',
  '.txt': 'text/plain; charset=utf-8',
};

function serve() {
  return new Promise((resolve) => {
    const server = createServer((req, res) => {
      const url = new URL(req.url, 'http://localhost');
      let file = join(dist, decodeURIComponent(url.pathname));
      if (existsSync(file) && statSync(file).isDirectory()) file = join(file, 'index.html');
      if (!existsSync(file)) {
        res.writeHead(404, { 'content-type': 'text/html' });
        createReadStream(join(dist, '404.html')).pipe(res);
        return;
      }
      res.writeHead(200, { 'content-type': MIME[extname(file)] ?? 'application/octet-stream' });
      createReadStream(file).pipe(res);
    });
    server.listen(0, () => resolve({ server, port: server.address().port }));
  });
}

const { server, port } = await serve();
const base = `http://localhost:${port}`;
const browser = await chromium.launch();
const report = [];

const routeFilter = process.argv
  .find((arg) => arg.startsWith('--routes='))
  ?.slice('--routes='.length)
  .split(',');

for (const [name, path] of ROUTES) {
  if (routeFilter && !routeFilter.includes(path) && !routeFilter.includes(name)) continue;
  for (const [device, width, height] of VIEWPORTS) {
    const context = await browser.newContext({
      viewport: { width, height },
      deviceScaleFactor: 1,
      reducedMotion: process.env.QA_REDUCED_MOTION ? 'reduce' : 'no-preference',
    });
    const page = await context.newPage();
    const external = [];
    const consoleErrors = [];
    page.on('request', (request) => {
      if (!request.url().startsWith(base) && !request.url().startsWith('data:')) {
        external.push(request.url());
      }
    });
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text());
    });
    page.on('pageerror', (error) => consoleErrors.push(String(error)));

    const response = await page.goto(base + path, { waitUntil: 'load' });

    // Las imágenes diferidas sólo se piden al entrar en el viewport; sin este
    // recorrido la captura de página completa sale con huecos. Se recorre en
    // pasos de una pantalla y se espera a que la red se calme, sin escuchar
    // eventos `load` (una imagen que el navegador decidió no cargar nunca los
    // emite y la espera no terminaría).
    await page.evaluate(async () => {
      const step = Math.round(window.innerHeight * 0.8);
      for (let y = 0; y < document.body.scrollHeight; y += step) {
        window.scrollTo(0, y);
        await new Promise((resolve) => setTimeout(resolve, 120));
      }
      window.scrollTo(0, 0);
      await new Promise((resolve) => setTimeout(resolve, 120));
    });
    await page.waitForLoadState('networkidle');

    // Si algo quedó sin cargar, es un fallo real de la página, no del recorrido.
    const brokenImages = await page.evaluate(() =>
      [...document.images]
        .filter((img) => !img.complete || img.naturalWidth === 0)
        .map((img) => img.currentSrc || img.src || '(sin src)'),
    );
    await page.evaluate(async () => {
      await document.fonts.ready;
    });

    const audit = await page.evaluate(() => {
      const docWidth = document.documentElement.clientWidth;
      const overflow = [];
      const smallText = [];
      const smallTargets = [];
      const missingDims = [];

      const inScrollRegion = (el) => {
        for (let node = el.parentElement; node; node = node.parentElement) {
          const overflowX = getComputedStyle(node).overflowX;
          if (overflowX === 'auto' || overflowX === 'scroll') return true;
        }
        return false;
      };

      for (const el of document.querySelectorAll('body *')) {
        const rect = el.getBoundingClientRect();
        if (inScrollRegion(el)) continue;
        if (rect.width > 0 && rect.right > docWidth + 1) {
          overflow.push(
            `${el.tagName.toLowerCase()}.${(el.className || '').toString().split(' ')[0]} right=${Math.round(rect.right)}`,
          );
        }
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
        if (el.closest('.sr-only') || el.classList.contains('sr-only')) continue;
        const size = parseFloat(getComputedStyle(el).fontSize);
        if (size < 12.99) smallText.push(`${size}px "${text.slice(0, 40)}"`);
      }

      for (const el of document.querySelectorAll('a[href], button, summary, input, select, textarea')) {
        const rect = el.getBoundingClientRect();
        // Contenido dentro de un <details> cerrado o elementos ocultos.
        if (rect.width === 0 || rect.height === 0) continue;
        // WCAG 2.5.8 exceptúa los enlaces en línea dentro de una frase.
        const parent = el.parentElement;
        const inline =
          el.tagName === 'A' &&
          parent &&
          /^(P|LI|DD|SPAN|FIGCAPTION)$/.test(parent.tagName) &&
          (parent.textContent || '').trim() !== (el.textContent || '').trim();
        if (inline) continue;
        if (rect.height < 24 || rect.width < 24) {
          smallTargets.push(
            `${el.tagName.toLowerCase()} "${(el.textContent || '').trim().slice(0, 30)}" ${Math.round(rect.width)}x${Math.round(rect.height)}`,
          );
        }
      }

      for (const img of document.querySelectorAll('img')) {
        if (!img.getAttribute('width') || !img.getAttribute('height')) {
          missingDims.push(img.getAttribute('src') ?? '(sin src)');
        }
      }

      const headings = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].map((h) => ({
        level: Number(h.tagName[1]),
        text: (h.textContent || '').trim().slice(0, 50),
      }));
      const outlineIssues = [];
      let previous = 0;
      for (const heading of headings) {
        if (previous && heading.level > previous + 1) {
          outlineIssues.push(`h${previous} -> h${heading.level} en "${heading.text}"`);
        }
        previous = heading.level;
      }
      const h1Count = headings.filter((h) => h.level === 1).length;

      return {
        overflow: [...new Set(overflow)].slice(0, 10),
        smallText: [...new Set(smallText)].slice(0, 10),
        smallTargets: [...new Set(smallTargets)].slice(0, 10),
        missingDims: [...new Set(missingDims)].slice(0, 10),
        outlineIssues,
        h1Count,
        headings: headings.slice(0, 40),
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: docWidth,
      };
    });

    const shotDir = join(outDir, device);
    mkdirSync(shotDir, { recursive: true });
    await page.screenshot({ path: join(shotDir, `${name}.png`), fullPage: true });

    report.push({
      route: path,
      name,
      device,
      status: response?.status(),
      external,
      consoleErrors,
      brokenImages,
      ...audit,
    });

    await context.close();
  }
  process.stdout.write(`.`);
}

await browser.close();
server.close();

mkdirSync(outDir, { recursive: true });
writeFileSync(join(outDir, 'report.json'), JSON.stringify(report, null, 2));

console.log('\n--- QA ---');
let problems = 0;
for (const entry of report) {
  const issues = [];
  if (entry.status !== 200) issues.push(`status ${entry.status}`);
  if (entry.external.length) issues.push(`externo: ${entry.external.join(', ')}`);
  if (entry.consoleErrors.length) issues.push(`consola: ${entry.consoleErrors.join(' | ')}`);
  if (entry.overflow.length) issues.push(`desborde: ${entry.overflow.join('; ')}`);
  if (entry.smallText.length) issues.push(`texto <13px: ${entry.smallText.join('; ')}`);
  if (entry.smallTargets.length) issues.push(`objetivo <24px: ${entry.smallTargets.join('; ')}`);
  if (entry.missingDims.length) issues.push(`img sin dimensiones: ${entry.missingDims.join('; ')}`);
  if (entry.brokenImages.length) issues.push(`img sin cargar: ${entry.brokenImages.join('; ')}`);
  if (entry.outlineIssues.length) issues.push(`encabezados: ${entry.outlineIssues.join('; ')}`);
  if (entry.h1Count !== 1) issues.push(`h1 = ${entry.h1Count}`);
  if (entry.scrollWidth > entry.clientWidth + 1)
    issues.push(`scroll horizontal ${entry.scrollWidth} > ${entry.clientWidth}`);
  if (issues.length) {
    problems += 1;
    console.log(`\n[${entry.device}] ${entry.route}`);
    for (const issue of issues) console.log(`  - ${issue}`);
  }
}
console.log(`\n${problems} de ${report.length} vistas con observaciones. Capturas en ${outDir}/`);
