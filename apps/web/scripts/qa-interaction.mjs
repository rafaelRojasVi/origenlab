#!/usr/bin/env node
/**
 * QA de interacción sobre `dist/`: teclado, menú móvil, riel de marcas,
 * movimiento reducido y enlaces internos. Complementa `qa-screenshots.mjs`,
 * que cubre la parte visual.
 *
 * Ejecutar: node scripts/qa-interaction.mjs
 */
import { createServer } from 'node:http';
import { createReadStream, existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, extname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const dist = join(root, 'dist');
let failed = false;

function check(condition, message) {
  console.log(`${condition ? '  ok' : '   x'} ${message}`);
  if (!condition) failed = true;
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
  let file = join(dist, decodeURIComponent(new URL(req.url, 'http://l').pathname));
  if (existsSync(file) && statSync(file).isDirectory()) file = join(file, 'index.html');
  if (!existsSync(file)) {
    res.writeHead(404, { 'content-type': 'text/html' });
    res.end('missing');
    return;
  }
  res.writeHead(200, { 'content-type': MIME[extname(file)] ?? 'application/octet-stream' });
  createReadStream(file).pipe(res);
});
await new Promise((resolve) => server.listen(0, resolve));
const base = `http://localhost:${server.address().port}`;
const browser = await chromium.launch();

/* -- 1. Menú móvil: abrir, Escape, clic fuera, foco de vuelta -------------- */
console.log('\nMenú móvil (375 px)');
{
  const context = await browser.newContext({ viewport: { width: 375, height: 812 } });
  const page = await context.newPage();
  await page.goto(`${base}/`, { waitUntil: 'load' });

  const summary = page.locator('[data-site-menu] summary');
  await summary.click();
  check(await page.locator('[data-site-menu]').evaluate((el) => el.open), 'el clic abre el panel');
  check(await page.locator('.site-menu__panel a', { hasText: 'Productos' }).isVisible(), 'el panel muestra la navegación');

  await page.keyboard.press('Escape');
  check(!(await page.locator('[data-site-menu]').evaluate((el) => el.open)), 'Escape cierra el panel');
  check(
    await page.evaluate(() => document.activeElement?.tagName === 'SUMMARY'),
    'el foco vuelve al disparador',
  );

  await summary.click();
  check(await page.locator('[data-site-menu]').evaluate((el) => el.open), 'vuelve a abrirse');
  await summary.click();
  check(
    !(await page.locator('[data-site-menu]').evaluate((el) => el.open)),
    'el mismo control lo cierra (aspa)',
  );

  await summary.click();
  // El bloqueo lo aplica el manejador de `toggle`, que se encola tras el clic.
  const locked = await page
    .waitForFunction(() => document.documentElement.style.overflow === 'hidden', null, {
      timeout: 2000,
    })
    .then(() => true)
    .catch(() => false);
  check(locked, 'con el panel abierto el documento no se desplaza detrás');
  await page.locator('.site-menu__panel a', { hasText: 'Marcas' }).first().click();
  await page.waitForLoadState('load');
  check(new URL(page.url()).pathname === '/marcas/', `un enlace del panel navega (${new URL(page.url()).pathname})`);

  await page.goto(`${base}/`, { waitUntil: 'load' });
  await page.locator('[data-site-menu]').evaluate((el) => (el.open = false));
  check(
    await page.evaluate(() => document.documentElement.style.overflow !== 'hidden'),
    'con el panel cerrado el documento vuelve a desplazarse',
  );
  await context.close();
}

/* -- 2. Recorrido de teclado ---------------------------------------------- */
console.log('\nTeclado (1440 px)');
{
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  await page.goto(`${base}/`, { waitUntil: 'load' });

  await page.keyboard.press('Tab');
  const first = await page.evaluate(() => document.activeElement?.textContent?.trim());
  check(first === 'Ir al contenido', `el primer tabulador es el enlace de salto (fue "${first}")`);

  const ringed = await page.evaluate(() => {
    const el = document.activeElement;
    if (!el) return false;
    const style = getComputedStyle(el);
    return style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) >= 2;
  });
  check(ringed, 'el elemento enfocado tiene anillo visible de al menos 2 px');

  // El CTA primario debe alcanzarse con un número razonable de tabuladores.
  let steps = 1;
  let reached = false;
  while (steps < 20 && !reached) {
    await page.keyboard.press('Tab');
    steps += 1;
    reached = await page.evaluate(
      () => document.activeElement?.getAttribute('aria-label') === 'Solicitar cotización',
    );
  }
  check(reached, `el CTA primario se alcanza en ${steps} tabuladores`);

  // La cabecera fija no debe tapar ningún elemento alcanzado con el tabulador.
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.locator('body').press('Tab');
  let covered = null;
  for (let i = 0; i < 60 && !covered; i += 1) {
    await page.keyboard.press('Tab');
    covered = await page.evaluate(() => {
      const el = document.activeElement;
      const header = document.querySelector('.site-header');
      if (!el || !header || el === document.body) return null;
      if (getComputedStyle(header).position !== 'sticky') return null;
      const rect = el.getBoundingClientRect();
      const bar = header.getBoundingClientRect();
      // El enlace de salto se dibuja deliberadamente por encima de la cabecera.
      if (rect.height === 0 || header.contains(el) || el.classList.contains('skip-link')) return null;
      const hidden = rect.top < bar.bottom - 1 && rect.bottom > bar.top;
      return hidden ? `${el.tagName.toLowerCase()} "${(el.textContent || '').trim().slice(0, 30)}"` : null;
    });
  }
  check(!covered, `la cabecera fija no cubre ningún elemento enfocado (${covered ?? 'ninguno'})`);
  await context.close();
}

/* -- 2b. Riel de marcas: bucle con control, y sin enlace duplicado -------- */

/**
 * Un bucle permanente sólo es admisible si se puede parar (WCAG 2.2.2), y una
 * marquesina sólo es admisible si su copia no duplica el contenido para quien
 * navega con lector de pantalla o con el tabulador. Las dos cosas se comprueban
 * aquí porque las dos se rompen en silencio: el riel seguiría moviéndose igual.
 */
console.log('\nRiel de marcas (1440 px)');
{
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  await page.goto(`${base}/`, { waitUntil: 'load' });

  const rail = page.locator('[data-rail]');
  check(await rail.count() === 1, 'hay exactamente un riel en la portada');
  check(
    await rail.evaluate((el) => el.hasAttribute('data-rail-ready')),
    'el script habilita el movimiento (data-rail-ready)',
  );

  const track = page.locator('[data-rail-track]');
  const playing = await track.evaluate((el) => {
    const style = getComputedStyle(el);
    return { name: style.animationName, state: style.animationPlayState };
  });
  check(playing.name !== 'none', `la pista tiene animación (${playing.name})`);
  check(playing.state === 'running', 'la pista se mueve al cargar');

  // Las seis, en el orden de la lista cerrada y sin repetir enlaces.
  const ORDER = [
    'Hielscher Ultrasonics',
    'Ortoalresa',
    'IKA',
    'Adam Equipment',
    'Löser Messtechnik',
    'SERVA Electrophoresis',
  ];
  const names = await page.locator('[data-rail] .rail__set:not([aria-hidden]) .rail__name').allTextContents();
  check(
    JSON.stringify(names.map((name) => name.trim())) === JSON.stringify(ORDER),
    `el orden del riel es el de la lista cerrada (${names.join(', ')})`,
  );

  // Sólo la pista: el enlace de la entradilla («Ver las marcas en detalle») no
  // es una marca y contarlo escondería un duplicado real.
  const links = await page.locator('[data-rail-track] a').count();
  check(links === 6, `seis enlaces de marca en la pista, no doce (${links})`);
  const clone = page.locator('[data-rail] .rail__set--clone');
  check(
    await clone.evaluate((el) => el.getAttribute('aria-hidden') === 'true'),
    'la copia del bucle está oculta a tecnologías de asistencia',
  );
  check(
    await clone.locator('a, button, [tabindex]').count() === 0,
    'la copia del bucle no contiene nada enfocable',
  );

  // Igualdad óptica: los seis logotipos comparten opacidad de reposo.
  const opacities = await page
    .locator('[data-rail] .rail__set:not([aria-hidden]) .rail__logo')
    .evaluateAll((els) => [...new Set(els.map((el) => getComputedStyle(el).opacity))]);
  check(
    opacities.length === 1,
    `los seis logotipos comparten el mismo tratamiento de reposo (${opacities.join(', ')})`,
  );

  // Pausa: el cursor, el foco y el control explícito.
  await page.locator('[data-rail] .rail__viewport').hover();
  check(
    (await track.evaluate((el) => getComputedStyle(el).animationPlayState)) === 'paused',
    'el cursor sobre el riel lo detiene',
  );
  await page.mouse.move(0, 0);

  await page.locator('[data-rail-track] a').first().focus();
  check(
    (await track.evaluate((el) => getComputedStyle(el).animationPlayState)) === 'paused',
    'el foco de teclado en un enlace del riel lo detiene',
  );
  await page.locator('h1').first().evaluate((el) => el.focus());

  const toggle = page.locator('[data-rail-toggle]');
  check(await toggle.isVisible(), 'el control de pausa es visible');
  await toggle.click();
  check(
    await toggle.evaluate((el) => el.getAttribute('aria-pressed') === 'true'),
    'el control anuncia el estado de pausa',
  );
  await page.mouse.move(0, 0);
  check(
    (await track.evaluate((el) => getComputedStyle(el).animationPlayState)) === 'paused',
    'con el control pulsado el riel sigue detenido fuera del cursor',
  );
  await toggle.click();
  check(
    (await track.evaluate((el) => getComputedStyle(el).animationPlayState)) === 'running',
    'el mismo control lo reanuda',
  );

  await context.close();
}

/* -- 3. Movimiento reducido ------------------------------------------------ */
console.log('\nMovimiento reducido');
{
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    reducedMotion: 'reduce',
  });
  const page = await context.newPage();
  await page.goto(`${base}/`, { waitUntil: 'load' });
  const animated = await page.evaluate(() => {
    const offenders = [];
    for (const el of document.querySelectorAll('body *')) {
      const style = getComputedStyle(el);
      if (style.animationName !== 'none' && style.animationDuration !== '0s') {
        offenders.push(`${el.tagName.toLowerCase()} ${style.animationName}`);
      }
    }
    return offenders;
  });
  check(animated.length === 0, `sin animaciones activas (${animated.join(', ') || 'ninguna'})`);
  const smooth = await page.evaluate(() => getComputedStyle(document.documentElement).scrollBehavior);
  check(smooth !== 'smooth', `scroll-behavior es "${smooth}"`);

  // El riel tiene que ser una fila estática legible, no una marquesina parada:
  // sin copia del bucle, sin control que no controla nada, y con las seis
  // marcas visibles a la vez.
  const rail = page.locator('[data-rail]');
  check(
    !(await rail.evaluate((el) => el.hasAttribute('data-rail-ready'))),
    'el riel no se marca como animado con movimiento reducido',
  );
  check(
    !(await page.locator('[data-rail] .rail__set--clone').isVisible()),
    'la copia del bucle no se muestra',
  );
  check(
    !(await page.locator('[data-rail-toggle]').isVisible()),
    'el control de pausa se retira: no hay movimiento que parar',
  );
  const visibleLogos = await page
    .locator('[data-rail] .rail__set:not([aria-hidden]) .rail__logo')
    .evaluateAll((els) => els.filter((el) => el.getBoundingClientRect().width > 0).length);
  check(visibleLogos === 6, `las seis marcas se leen a la vez (${visibleLogos})`);

  await context.close();
}

/* -- 4. Enlaces internos --------------------------------------------------- */
console.log('\nEnlaces');
{
  function walk(dir, acc = []) {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = join(dir, entry.name);
      if (entry.isDirectory()) walk(full, acc);
      else if (entry.name.endsWith('.html')) acc.push(full);
    }
    return acc;
  }

  const pages = walk(dist).filter((file) => !relative(dist, file).startsWith('email/'));
  const broken = new Set();
  const external = new Set();

  for (const file of pages) {
    const html = readFileSync(file, 'utf8');
    for (const match of html.matchAll(/(?:href|src|srcset)="([^"]+)"/g)) {
      for (const raw of match[1].split(',')) {
        const url = raw.trim().split(/\s+/)[0];
        if (!url || url.startsWith('#') || url.startsWith('mailto:') || url.startsWith('tel:')) continue;
        if (/^(https?:)?\/\//.test(url)) {
          external.add(new URL(url.startsWith('//') ? `https:${url}` : url).host);
          continue;
        }
        if (!url.startsWith('/')) continue;
        /*
         * Un enlace interno puede llevar ancla: /aplicaciones/#osmolalidad. La
         * ruta y el ancla se comprueban por separado, porque fallan por motivos
         * distintos: la ruta puede no existir, o existir y no tener ese id.
         */
        const [pathPart, fragment] = url.split('#');
        const cleanPath = pathPart.split('?')[0];
        if (!cleanPath) continue;
        const target = cleanPath.endsWith('/')
          ? join(dist, cleanPath, 'index.html')
          : join(dist, cleanPath);
        if (!existsSync(target)) {
          broken.add(`${relative(dist, file)} -> ${url}`);
          continue;
        }
        if (fragment) {
          const targetHtml = readFileSync(target, 'utf8');
          if (!targetHtml.includes(`id="${fragment}"`)) {
            broken.add(`${relative(dist, file)} -> ${url} (la página existe, el ancla no)`);
          }
        }
      }
    }
  }

  check(broken.size === 0, `sin enlaces internos rotos (${[...broken].slice(0, 5).join('; ') || 'ninguno'})`);
  // Los externos sólo pueden ser destinos de navegación (fabricantes, WhatsApp).
  // Los seis fabricantes aprobados y nada más. Si aparece otro host, o es una
  // marca que ya no se publica o es un tercero que se coló: las dos cosas hay
  // que verlas. La lista la respalda `src/data/brands.ts`.
  const allowed = new Set([
    'www.hielscher.com',
    'ortoalresa.com',
    'www.ika.com',
    'adamequipment.com',
    // Löser no ofrece HTTPS: su servidor de 2005 rechaza el saludo TLS.
    // Declarado en brands.ts (`websiteInsecure`) y en CONTENT_NEEDED.md.
    'www.loeser-osmometer.de',
    'www.serva.de',
    'wa.me',
    'origenlab.cl',
    'www.w3.org',
  ]);
  const unexpected = [...external].filter((host) => !allowed.has(host));
  check(unexpected.length === 0, `sin destinos externos inesperados (${unexpected.join(', ') || 'ninguno'})`);
}

await browser.close();
server.close();

if (failed) {
  console.error('\nqa:interaction FALLÓ');
  process.exit(1);
}
console.log('\nqa:interaction OK');
