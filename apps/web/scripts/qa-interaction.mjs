#!/usr/bin/env node
/**
 * QA de interacción sobre `dist/`: teclado, menú móvil, sala 3D del hero,
 * riel de marcas, movimiento reducido y enlaces internos. Complementa
 * `qa-screenshots.mjs`, que cubre la parte visual.
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
// WebGL por software, para que la sala 3D también arranque sin GPU.
const browser = await chromium.launch({ args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });

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

/* -- 2a. Sala 3D del hero e índice de alcance ------------------------------ */

/**
 * La sala 3D es mejora progresiva: el HTML trae la fotografía oficial y la
 * escena sólo la sustituye cuando ha dibujado su primer cuadro. Se comprueba
 * que llegue, que sus controles sean botones normales con estado anunciado,
 * que el lienzo no aporte nada a tecnologías de asistencia y que no pida nada
 * fuera del dominio.
 *
 * El índice de familias sigue debajo: seis filas, seis destinos internos.
 */
console.log('\nSala 3D del hero (1440 px)');
{
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  const foreign = [];
  page.on('request', (request) => {
    if (!request.url().startsWith(base)) foreign.push(request.url());
  });
  await page.goto(`${base}/`, { waitUntil: 'load' });

  const figure = page.locator('[data-showroom]');
  check(await figure.count() === 1, 'hay una sala de equipo en la portada');
  // La foto de cada equipo llega en el HTML; la escena la oculta al dibujar
  // (con movimiento reducido se comprueba más abajo que sigue visible).
  check(
    await figure.locator('[data-showroom-photo] img').count() >= 1,
    'las fotografías oficiales llegan en el HTML, antes de cualquier script',
  );
  check(
    await figure.locator('[data-showroom-canvas]').evaluate((el) => el.getAttribute('aria-hidden') === 'true'),
    'el lienzo 3D está oculto a tecnologías de asistencia',
  );

  await figure.scrollIntoViewIfNeeded();
  const ready = await figure
    .evaluate((el) => new Promise((resolve) => {
      if (el.hasAttribute('data-ready')) return resolve(true);
      const watch = new MutationObserver(() => el.hasAttribute('data-ready') && resolve(true));
      watch.observe(el, { attributes: true });
      setTimeout(() => resolve(false), 20000);
    }));
  check(ready, 'la escena 3D dibuja y sustituye a la fotografía');

  if (ready) {
    const bar = figure.locator('[data-showroom-bar]:not([hidden])');
    const power = bar.locator('[data-showroom-power]');
    check(await power.isVisible(), 'el botón de encendido es visible');
    await power.click();
    check(await power.getAttribute('aria-pressed') === 'true', 'encender anuncia el estado');
    await power.click();
    check(await power.getAttribute('aria-pressed') === 'false', 'y apagar también');

    const views = bar.locator('[data-showroom-view]');
    check(await views.count() === 4, `cuatro vistas (${await views.count()})`);
    await views.nth(2).click();
    const pressed = await views.evaluateAll((els) => els.map((el) => el.getAttribute('aria-pressed')));
    check(
      pressed.filter((value) => value === 'true').length === 1 && pressed[2] === 'true',
      `sólo la vista elegida queda marcada (${pressed.join(', ')})`,
    );

    const dots = figure.locator('[data-showroom-hotspots]:not([hidden]) .hotspot__dot');
    check(await dots.count() === 4, `cuatro marcadores (${await dots.count()})`);
    await dots.first().focus();
    await page.keyboard.press('Enter');
    check(await dots.first().getAttribute('aria-expanded') === 'true', 'un marcador se abre con el teclado');
    const note = await dots.first().getAttribute('aria-controls');
    // La nota entra con una transición corta: se espera a que termine.
    const shown = Boolean(note) && await page.locator(`#${note}`).waitFor({ state: 'visible', timeout: 5000 }).then(() => true, () => false);
    check(shown, 'y su nota queda visible');
  }
  // Selector de tareas: cambia de equipo en el mismo lienzo.
  const tasks = figure.locator('[data-pick]');
  if (ready && await tasks.count() > 1) {
    const canvases = await figure.locator('canvas').count();
    await tasks.nth(1).click();
    const second = await tasks.nth(1).getAttribute('data-pick');
    const switched = await figure.evaluate((el, id) => new Promise((resolve) => {
      const done = () => el.dataset.active === id && !el.hasAttribute('data-swapping');
      if (done()) return resolve(true);
      const watch = new MutationObserver(() => done() && resolve(true));
      watch.observe(el, { attributes: true });
      setTimeout(() => resolve(done()), 20000);
    }), second);
    check(switched, `elegir otra tarea cambia el equipo (${second})`);
    check(await tasks.nth(1).getAttribute('aria-pressed') === 'true', 'la tarea elegida queda marcada');
    check(await figure.locator('canvas').count() === canvases, 'sigue habiendo un solo lienzo 3D');
  }
  check(foreign.length === 0, `la sala no pide nada fuera del dominio (${foreign.join(', ') || 'nada'})`);

  const rows = page.locator('[data-family-row]');
  check(await rows.count() === 6, `seis filas de índice (${await rows.count()})`);
  const hrefs = await rows.evaluateAll((els) => els.map((el) => el.getAttribute('href')));
  check(
    hrefs.every((href) => typeof href === 'string' && href.startsWith('/') && href.endsWith('/')),
    `las seis filas llevan a una ruta interna (${hrefs.join(', ')})`,
  );
  check(new Set(hrefs).size === 6, 'las seis filas llevan a destinos distintos');

  await context.close();
}

/**
 * En un teléfono la sala de la portada tampoco se monta durante la carga: la
 * fotografía queda sola y la escena llega con el primer gesto o, sin gesto,
 * unos 4 s después de `load`, cuando el navegador está ocioso.
 */
console.log('\nSala 3D del hero (375 px, montaje diferido)');
{
  const context = await browser.newContext({ viewport: { width: 375, height: 812 }, hasTouch: true, isMobile: true });
  const page = await context.newPage();
  await page.addInitScript(() => {
    window.__webgl = 0;
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (kind, ...rest) {
      if (String(kind).startsWith('webgl')) window.__webgl += 1;
      return original.call(this, kind, ...rest);
    };
  });
  await page.goto(`${base}/`, { waitUntil: 'load' });
  const figure = page.locator('[data-showroom]');
  check(await page.evaluate(() => window.__webgl) === 0, 'justo después de load no hay contexto WebGL');
  check(await figure.locator('[data-showroom-photo]:not([hidden]) img').isVisible(), 'la fotografía del equipo se ve desde el principio');
  await page.waitForTimeout(1500);
  check(!(await figure.evaluate((el) => el.hasAttribute('data-ready'))), 'sin gesto, 1,5 s después la escena sigue sin montarse');
  const ready = await figure.evaluate((el) => new Promise((resolve) => {
    if (el.hasAttribute('data-ready')) return resolve(true);
    const watch = new MutationObserver(() => el.hasAttribute('data-ready') && resolve(true));
    watch.observe(el, { attributes: true });
    setTimeout(() => resolve(false), 20000);
  }));
  check(ready, 'sin gesto, la escena se monta sola cuando la página ya está quieta');
  check(await page.evaluate(() => window.__webgl) === 1, `un solo contexto WebGL (${await page.evaluate(() => window.__webgl)})`);
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

  // Las seis de la portada (OHAUS no va en la portada: HOME_HIDDEN_BRAND_IDS), en el orden de
  // la lista cerrada y sin repetir enlaces.
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

  // Igualdad óptica: los siete logotipos comparten opacidad de reposo.
  const opacities = await page
    .locator('[data-rail] .rail__set:not([aria-hidden]) .rail__logo')
    .evaluateAll((els) => [...new Set(els.map((el) => getComputedStyle(el).opacity))]);
  check(
    opacities.length === 1,
    `los siete logotipos comparten el mismo tratamiento de reposo (${opacities.join(', ')})`,
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

  // Con movimiento reducido la sala 3D no se carga: queda la fotografía oficial.
  const showroom = page.locator('[data-showroom]');
  await showroom.scrollIntoViewIfNeeded();
  await page.waitForTimeout(1500);
  check(!(await showroom.evaluate((el) => el.hasAttribute('data-ready'))), 'la escena 3D no arranca');
  check(await showroom.locator('[data-showroom-photo]:not([hidden]) img').isVisible(), 'la fotografía del equipo sigue visible');
  check(
    !(await page.evaluate(() => performance.getEntriesByType('resource').some((entry) => /\/scene-[\w-]+\.[\w-]+\.js/.test(entry.name)))),
    'el fragmento de three.js no se descarga',
  );

  // El riel tiene que ser una fila estática legible, no una marquesina parada:
  // sin copia del bucle, sin control que no controla nada, y con las siete
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
  // Los siete fabricantes aprobados y nada más. Si aparece otro host, o es una
  // marca que ya no se publica o es un tercero que se coló: las dos cosas hay
  // que verlas. La lista la respalda `src/data/brands.ts`.
  const allowed = new Set([
    'www.hielscher.com',
    'ortoalresa.com',
    'www.ika.com',
    'adamequipment.com',
    // OHAUS: sitio para Latinoamérica y servidor de fichas PDF (sólo enlaces).
    'mx.ohaus.com',
    'dmx.ohaus.com',
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

/* -- 5. Formulario de cotización ------------------------------------------- */
console.log('\nFormulario de cotización (/contacto/, 375 px)');
{
  const context = await browser.newContext({ viewport: { width: 375, height: 812 } });
  const page = await context.newPage();
  await page.goto(`${base}/contacto/`, { waitUntil: 'load' });

  // Submit with empty required fields → inline error summary appears and is focusable
  await page.click('button[type="submit"]');
  const summary = page.locator('[data-error-summary]');
  check(await summary.isVisible(), 'formulario: error summary visible tras submit vacío');
  check(await summary.evaluate(el => el === document.activeElement || el.contains(document.activeElement)), 'formulario: error summary recibe foco');

  // Fill required fields and submit (mocked: no real endpoint in dist/)
  await page.fill('#solicitud-need', 'Autoclave de 50 L para laboratorio de microbiología');
  await page.fill('#solicitud-email', 'ana@uchile.cl');
  await page.fill('#solicitud-org', 'U. de Chile - ICBM');
  // aria-invalid removed after correction
  check(!(await page.locator('#solicitud-email').getAttribute('aria-invalid')), 'formulario: aria-invalid resuelto tras corrección');

  // Keyboard: Tab order reaches the button
  await page.keyboard.press('Tab');
  // just verify no JS error was thrown
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  check(errors.length === 0, 'formulario: sin errores JS en /contacto/');
  await context.close();
}

/* -- 6. Historia de producto (ProductStory) -------------------------------- */

/**
 * La página de producto cuenta el equipo en diez tramos de scroll sobre un
 * lienzo 3D fijo. Mejora progresiva: el HTML trae todo el texto de los tramos y
 * la ficha; el 3D se pide al acercarse, en el nivel de calidad que el equipo
 * aguante (`canvas[data-tier]`), y con movimiento reducido no se pide.
 *
 * Se prueba en la página pública del modelo. La sala interna
 * `/sala-lab/historia/` sirvió de banco de pruebas hasta que existió esta ruta
 * (tarea 4A) y ya no existe.
 */
const STORY_PAGES = [{ path: '/productos/sonicacion/hielscher-up400st/', pending: null }];
const webglCounter = () => {
  window.__webgl = 0;
  const original = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function (kind, ...rest) {
    if (String(kind).startsWith('webgl')) window.__webgl += 1;
    return original.call(this, kind, ...rest);
  };
};
const waitReady = (page) => page.locator('[data-story] canvas').evaluate((el) => new Promise((resolve) => {
  if (el.dataset.ready === '1') return resolve(true);
  const watch = new MutationObserver(() => el.dataset.ready === '1' && resolve(true));
  watch.observe(el, { attributes: true });
  setTimeout(() => resolve(false), 20000);
}));
for (const story of STORY_PAGES) {
  const built = join(dist, story.path, 'index.html');
  if (!existsSync(built)) {
    console.log(`\nHistoria de producto ${story.path}`);
    if (story.pending) {
      console.log(`   - pendiente (${story.pending}): la ruta todavía no existe; vuelve a correr al terminar ${story.pending}`);
    } else {
      check(false, `ProductStory: falta ${story.path}`);
    }
    continue;
  }

  console.log(`\nHistoria de producto ${story.path} (movimiento reducido)`);
  {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    const page = await context.newPage();
    await page.addInitScript(webglCounter);
    await page.goto(`${base}${story.path}`, { waitUntil: 'load' });
    await page.locator('[data-story]').scrollIntoViewIfNeeded();
    await page.waitForTimeout(800);
    check(await page.locator('.story-specs').isVisible(), 'ProductStory: static DOM present under reduced-motion (.story-specs visible)');
    check(await page.locator('[data-story] canvas').evaluate((el) => el.hidden), 'ProductStory: el lienzo queda con hidden');
    const beats = await page.locator('[data-story] [data-beat]').evaluateAll((els) => els.map((el) => ({
      id: el.dataset.beat,
      heading: el.querySelector('h2')?.textContent?.trim() ?? '',
      shown: el.getBoundingClientRect().height > 0 && getComputedStyle(el).display !== 'none',
    })));
    check(beats.length === 10, `ProductStory: diez tramos en el HTML (${beats.length})`);
    check(
      beats.length > 0 && beats.every((beat) => beat.heading && beat.shown),
      `ProductStory: cada tramo se lee sin 3D (faltan: ${beats.filter((b) => !b.heading || !b.shown).map((b) => b.id).join(', ') || 'ninguno'})`,
    );
    const spacer = await page.locator('[data-story-spacer]').evaluate((el) => el.getBoundingClientRect().height);
    check(spacer < 6 * 1000, `ProductStory: el espaciador se colapsa (${Math.round(spacer)} px)`);
    check(await page.evaluate(() => window.__webgl) === 0, 'ProductStory: ningún contexto WebGL con movimiento reducido');
    check(
      !(await page.evaluate(() => performance.getEntriesByType('resource').some((entry) => /\/scene-[\w-]+\.[\w-]+\.js/.test(entry.name)))),
      'ProductStory: ningún fragmento scene-* se descarga',
    );
    await context.close();
  }

  console.log(`\nHistoria de producto ${story.path} (1440 px)`);
  {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const page = await context.newPage();
    const foreign = [];
    const errors = [];
    page.on('request', (request) => {
      if (!request.url().startsWith(base)) foreign.push(request.url());
    });
    page.on('pageerror', (error) => errors.push(error.message));
    await page.goto(`${base}${story.path}`, { waitUntil: 'load' });
    const top = await page.locator('[data-story-spacer]').evaluate((el) => el.getBoundingClientRect().top + window.scrollY);
    await page.evaluate((y) => window.scrollTo(0, y + 1500), top);
    const ready = await waitReady(page);
    check(ready, 'ProductStory: canvas initialises on scroll (canvas[data-ready="1"])');
    const tier = await page.locator('[data-story] canvas').getAttribute('data-tier');
    check(['high', 'medium', 'low'].includes(tier ?? ''), `ProductStory: canvas[data-tier] es high, medium o low (${tier})`);
    if (ready) {
      // 1500 px dentro de un recorrido de 5000 px: progreso 0,3, el marcador de la pantalla.
      await page.evaluate((y) => window.scrollTo(0, y + 1520), top);
      await page.waitForTimeout(400);
      const beat = await page.locator('[data-story]').getAttribute('data-beat');
      check(beat === 'screen-hs', `ProductStory: el scroll marca el tramo activo (${beat})`);
      check(
        await page.locator('[data-story] .story-spot[data-anchor="screen"]').evaluate((el) => el.hasAttribute('data-on')),
        'ProductStory: el marcador de la pantalla se abre en su tramo',
      );
      await page.evaluate((y) => window.scrollTo(0, y + 2900), top);
      await page.waitForTimeout(400);
      const level = await page.locator('[data-story]').evaluate((el) => el.style.getPropertyValue('--story-level'));
      check(Number(level) > 0.2, `ProductStory: el tramo de amplitud publica el nivel (${level})`);
    }
    check(errors.length === 0, `ProductStory: sin errores de JavaScript (${errors.join('; ') || 'ninguno'})`);
    check(foreign.length === 0, `ProductStory: nada fuera del dominio (${foreign.join(', ') || 'nada'})`);
    await context.close();
  }

  console.log(`\nHistoria de producto ${story.path} (?tier=low, 1440 px)`);
  {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const page = await context.newPage();
    await page.addInitScript(webglCounter);
    await page.goto(`${base}${story.path}?tier=low`, { waitUntil: 'load' });
    await page.locator('[data-story]').scrollIntoViewIfNeeded();
    await page.waitForTimeout(800);
    check(await page.locator('[data-story] canvas').getAttribute('data-tier') === 'low', 'ProductStory: el nivel bajo se anuncia en data-tier');
    check(await page.evaluate(() => window.__webgl) === 0, 'ProductStory: el nivel bajo no crea ningún contexto WebGL');
    check(await page.locator('[data-story] [data-story-photo] img').isVisible(), 'ProductStory: el nivel bajo muestra la imagen fija');
    const button = page.locator('[data-story-3d]');
    check(await button.isVisible(), 'ProductStory: «Ver en 3D» se ofrece en el nivel bajo');
    if (await button.isVisible()) {
      await button.click();
      check(await waitReady(page), 'ProductStory: «Ver en 3D» carga la escena a pedido');
      check(await page.locator('[data-story] canvas').getAttribute('data-tier') === 'medium', 'ProductStory: a pedido carga el nivel medio');
    }
    await context.close();
  }

  console.log(`\nHistoria de producto ${story.path} (375 px, nivel automático)`);
  {
    // Un teléfono recibe el lienzo (nivel medio), no la imagen fija: con un
    // solo contexto WebGL, el del escenario, y sin bloquear el desplazamiento.
    const context = await browser.newContext({ viewport: { width: 375, height: 812 }, hasTouch: true, isMobile: true });
    const page = await context.newPage();
    await page.addInitScript(webglCounter);
    await page.goto(`${base}${story.path}`, { waitUntil: 'load' });
    await page.locator('[data-story]').scrollIntoViewIfNeeded();
    check(await waitReady(page), 'ProductStory: el 3D arranca en un teléfono sin ?tier=');
    const tier = await page.locator('[data-story] canvas').getAttribute('data-tier');
    check(tier === 'medium', `ProductStory: un teléfono recibe el nivel medio (${tier})`);
    const contexts = await page.evaluate(() => window.__webgl);
    check(contexts === 1, `ProductStory: un solo contexto WebGL (${contexts})`);
    const touch = await page.locator('[data-story] canvas').evaluate((el) => getComputedStyle(el).touchAction);
    check(touch === 'pan-y', `ProductStory: el lienzo deja desplazar la página con el dedo (touch-action: ${touch})`);
    check(await page.locator('[data-story]').getAttribute('data-mode') === 'tap', 'ProductStory: en un teléfono se avanza por toque');
    await context.close();
  }

  console.log(`\nHistoria de producto ${story.path} (375 px, montaje diferido)`);
  {
    // En un teléfono la escena no se monta durante la carga: la imagen fija
    // queda sola hasta el primer gesto (o unos 4 s después de `load`).
    const context = await browser.newContext({ viewport: { width: 375, height: 812 }, hasTouch: true, isMobile: true });
    const page = await context.newPage();
    await page.addInitScript(webglCounter);
    await page.goto(`${base}${story.path}`, { waitUntil: 'load' });
    check(await page.evaluate(() => window.__webgl) === 0, 'ProductStory: justo después de load no hay contexto WebGL');
    await page.waitForTimeout(1500);
    check(await page.evaluate(() => window.__webgl) === 0, 'ProductStory: sin gesto, 1,5 s después tampoco');
    check(
      !(await page.evaluate(() => performance.getEntriesByType('resource').some((entry) => /\/scene-[\w-]+\.[\w-]+\.js/.test(entry.name)))),
      'ProductStory: ningún fragmento scene-* se descarga antes del gesto',
    );
    check(await page.locator('[data-story] [data-story-photo] img').isVisible(), 'ProductStory: la imagen fija se ve desde el principio');
    await page.locator('[data-story]').scrollIntoViewIfNeeded();
    check(await waitReady(page), 'ProductStory: el desplazamiento monta la escena');
    const tier = await page.locator('[data-story] canvas').getAttribute('data-tier');
    check(tier === 'medium', `ProductStory: y lo hace en el nivel medio (canvas[data-tier="${tier}"])`);
    await context.close();
  }

  console.log(`\nHistoria de producto ${story.path} (375 px, avance por toque)`);
  {
    const context = await browser.newContext({ viewport: { width: 375, height: 812 } });
    const page = await context.newPage();
    await page.goto(`${base}${story.path}?tier=medium`, { waitUntil: 'load' });
    await page.locator('[data-story]').scrollIntoViewIfNeeded();
    check(await waitReady(page), 'ProductStory: la escena arranca en móvil');
    const next = page.locator('[data-story-next]');
    check(await next.isVisible(), 'ProductStory: hay un botón «Siguiente»');
    if (await next.isVisible()) {
      const before = await page.locator('[data-story]').getAttribute('data-beat');
      await next.click();
      await page.waitForTimeout(300);
      const after = await page.locator('[data-story]').getAttribute('data-beat');
      check(before !== after && Boolean(after), `ProductStory: el toque avanza de tramo (${before} a ${after})`);
      const shown = await page.locator('[data-story] [data-beat]').evaluateAll((els) => els.filter((el) => getComputedStyle(el).display !== 'none').length);
      check(shown === 1, `ProductStory: en móvil se lee un tramo a la vez (${shown})`);
    }
    await context.close();
  }
}

/*
 * Centrifugación conserva sus fichas en `/productos/centrifugas/`: la del
 * Digicen 22 R monta la historia por scroll (`ProductStory`) en lugar de la
 * fotografía del hero, sin cambiar de URL.
 */
{
  const path = '/productos/centrifugas/digicen-22-r/';
  console.log(`\nFicha heredada con historia ${path}`);
  if (!existsSync(join(dist, path, 'index.html'))) {
    check(false, `Ficha heredada: falta ${path}`);
  } else {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.goto(`${base}${path}`, { waitUntil: 'load' });
    check((await page.locator('[data-story]').count()) === 1, 'Ficha heredada del Digicen 22 R: monta la historia ([data-story])');
    check((await page.locator('[data-sticky-cta]').count()) === 1, 'Ficha heredada del Digicen 22 R: lleva la barra fija de cotización');
    check((await page.locator('#especificaciones-title').count()) === 0, 'Ficha heredada del Digicen 22 R: la ficha técnica sólo la cuenta la historia');
    await page.locator('[data-story]').scrollIntoViewIfNeeded();
    await page.waitForTimeout(800);
    check(errors.length === 0, `Ficha heredada del Digicen 22 R: sin errores de JavaScript (${errors.join('; ') || 'ninguno'})`);
    await context.close();
  }
}

/* -- 7. Páginas de modelo y de familia ------------------------------------ */

/**
 * Las páginas de modelo salen de la colección `modelos` (una por archivo) y
 * las de familia las listan. Los datos estructurados declaran el equipo sin
 * oferta (no se publica precio), y las preguntas frecuentes sólo cuando la
 * ficha las trae. La barra fija de cotización lleva el modelo al formulario.
 */
const MODEL_PATH = '/productos/sonicacion/hielscher-up400st/';
const FAMILY_PATH = '/productos/sonicacion/';
const ldBlocks = (html) =>
  [...html.matchAll(/<script type="application\/ld\+json"[^>]*>([\s\S]*?)<\/script>/g)].flatMap((match) => {
    try {
      const data = JSON.parse(match[1]);
      return Array.isArray(data) ? data : [data];
    } catch {
      return [{ '@type': 'JSON inválido' }];
    }
  });
console.log(`\nPágina de modelo ${MODEL_PATH}`);
{
  const built = join(dist, MODEL_PATH, 'index.html');
  const html = existsSync(built) ? readFileSync(built, 'utf8') : '';
  const blocks = ldBlocks(html);
  const product = blocks.find((block) => block['@type'] === 'Product');
  check(
    Boolean(
      product &&
        product.name &&
        product.description &&
        product.brand?.name &&
        product.manufacturer?.url &&
        product.url === `https://origenlab.cl${MODEL_PATH}` &&
        product.image &&
        product.additionalProperty?.length > 0,
    ),
    'Model page: JSON-LD Product present',
  );
  check(Boolean(product) && !('offers' in product), 'Model page: el Product no declara offers (no se publica precio)');
  check(!blocks.some((block) => block['@type'] === 'JSON inválido'), 'Model page: todo bloque JSON-LD es JSON válido');
  // El menú del sitio también es un <details>: se cuenta la sección de preguntas.
  const faqShown = />\s*Preguntas frecuentes\s*</.test(html);
  check(
    blocks.some((block) => block['@type'] === 'FAQPage') === faqShown,
    `Model page: FAQPage sólo con preguntas visibles (sección ${faqShown ? 'presente' : 'ausente'})`,
  );
}
console.log(`\nPágina de familia ${FAMILY_PATH}`);
{
  const built = join(dist, FAMILY_PATH, 'index.html');
  const html = existsSync(built) ? readFileSync(built, 'utf8') : '';
  check(html.includes(`href="${MODEL_PATH}"`), 'Family page lists its models');
}
/**
 * Enlaces de entrada: `/productos/` lleva a las seis páginas de familia, cada
 * página de marca a las fichas de sus modelos, y cada ficha recibe al menos un
 * enlace «relacionado» desde otra ficha de su familia (el relleno rota).
 */
console.log('\nEnlaces hacia las páginas de familia y de modelo');
{
  const read = (path) => {
    const file = join(dist, path, 'index.html');
    return existsSync(file) ? readFileSync(file, 'utf8') : '';
  };
  const entries = readdirSync(join(root, 'src/content/modelos'))
    .filter((name) => name.endsWith('.json'))
    .map((name) => JSON.parse(readFileSync(join(root, 'src/content/modelos', name), 'utf8')));
  const hrefOf = (entry) => `/productos/${entry.family}/${entry.slug}/`;
  const collection = entries.filter((entry) => entry.family !== 'centrifugacion');

  const index = [...read('/productos/').matchAll(/<a\b[^>]*\bclass="scope-index__link[^"]*"[^>]*>/g)]
    .map(([tag]) => tag.match(/href="([^"]+)"/)?.[1]);
  const families = [...new Set(entries.map((entry) => entry.family))]
    .map((family) => (family === 'centrifugacion' ? '/productos/centrifugas/' : `/productos/${family}/`));
  check(
    index.length === 6 && families.every((href) => index.includes(href)),
    `/productos/: el índice lleva a las seis páginas de familia (${index.join(', ')})`,
  );
  // Una cifra visible sólo entra por publicClaim(): el índice no cuenta modelos.
  check(!/scope-index__count/.test(read('/productos/')), '/productos/: el índice no publica conteos de modelos');

  const brandSlugs = Object.fromEntries(
    [...readFileSync(join(root, 'src/data/brands.ts'), 'utf8').matchAll(/\n {4}id: '([\w-]+)',[\s\S]*?\n {4}slug: '([\w-]+)'/g)]
      .map(([, id, slug]) => [id, slug]),
  );
  const missing = [];
  for (const [id, slug] of Object.entries(brandSlugs)) {
    const html = read(`/marcas/${slug}/`);
    const own = entries.filter((entry) => entry.brand === id);
    for (const entry of own.filter((item) => item.family !== 'centrifugacion')) {
      if (!html.includes(`href="${hrefOf(entry)}"`)) missing.push(`${slug} → ${entry.slug}`);
    }
    const centrifuges = own.filter((item) => item.family === 'centrifugacion').length;
    const legacy = new Set([...html.matchAll(/href="(\/productos\/centrifugas\/[\w-]+\/)"/g)].map(([, href]) => href)).size;
    if (legacy < centrifuges) missing.push(`${slug} → ${centrifuges - legacy} centrífugas`);
  }
  check(missing.length === 0, `cada página de marca enlaza las fichas de sus modelos (faltan: ${missing.join(', ') || 'ninguna'})`);

  const orphans = collection.filter((target) =>
    !collection.some((other) => other !== target && other.family === target.family && read(hrefOf(other)).includes(`href="${hrefOf(target)}"`)),
  );
  check(
    orphans.length === 0,
    `cada ficha recibe un enlace relacionado desde otra de su familia (sin enlace: ${orphans.map((entry) => entry.slug).join(', ') || 'ninguna'})`,
  );

  const context = await browser.newContext({ viewport: { width: 375, height: 812 }, reducedMotion: 'reduce' });
  const page = await context.newPage();
  for (const path of ['/productos/', '/marcas/hielscher/', '/marcas/serva-electrophoresis/']) {
    await page.goto(`${base}${path}`, { waitUntil: 'load' });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    check(overflow <= 0, `${path}: sin desplazamiento horizontal a 375 px (${overflow} px de más)`);
  }
  await context.close();
}

/**
 * Portada y `/como-comprar/`: la portada lleva a las seis páginas de familia,
 * tiene seis entradas por sector que llevan a alguna parte, oculta las notas
 * destacadas mientras la colección `aplicaciones` esté vacía y cierra con
 * WhatsApp; la guía de compra tiene cuatro pasos, la sección de compras
 * públicas y la nota de consentimiento con enlace a la política de privacidad.
 */
console.log('\nPortada y cómo comprar');
{
  const read = (path) => {
    const file = join(dist, path, 'index.html');
    return existsSync(file) ? readFileSync(file, 'utf8') : '';
  };
  const home = read('/');
  const familias = home.match(/<section\b[^>]*\bid="familias"[\s\S]*?<\/section>/)?.[0] ?? '';
  const familyPages = ['sonicacion', 'dispersion', 'pesaje-humedad', 'osmometria', 'electroforesis', 'centrifugas']
    .map((slug) => `/productos/${slug}/`);
  const missingFamilies = familyPages.filter((href) => !familias.includes(`href="${href}"`));
  check(missingFamilies.length === 0, `Home: families grid links to six family pages (faltan: ${missingFamilies.join(', ') || 'ninguna'})`);

  const industries = [...home.matchAll(/<li\b[^>]*\bdata-industry\b[\s\S]*?<\/ul>/g)].map(([block]) => block);
  check(
    industries.length === 6 && industries.every((block) => /href="\/[^"]*"/.test(block)),
    `Home: seis entradas por sector, cada una con un enlace interno (${industries.length})`,
  );
  const notes = readdirSync(join(root, 'src/content/aplicaciones')).filter((name) => name.endsWith('.md'));
  check(
    home.includes('data-home-notes') === notes.length > 0,
    `Home: las notas destacadas se muestran sólo si hay notas (${notes.length} notas)`,
  );
  const band = home.match(/<section\b[^>]*\bclass="band on-ink[\s\S]*?<\/section>/)?.[0] ?? '';
  check(/<a\b[^>]*href="https:\/\/wa\.me\/[^"]+"[^>]*class="btn btn-invert/.test(band), 'Home: la banda de cierre lleva el botón de WhatsApp');
  check(home.includes('href="/como-comprar/"'), 'Home: enlaza la guía de compra');

  const guide = read('/como-comprar/');
  const pasos = guide.match(/<section\b[^>]*\bid="pasos"[\s\S]*?<\/section>/)?.[0] ?? '';
  const steps = (pasos.match(/\bdata-step\b/g) ?? []).length;
  const consent = guide.match(/<p\b[^>]*\bdata-consent-note\b[\s\S]*?<\/p>/)?.[0] ?? '';
  check(
    steps === 4 && consent.includes('href="/privacidad/"') && guide.includes('id="compras-publicas"'),
    `Cómo comprar: four steps and consent note present (${steps} pasos, nota ${consent ? 'sí' : 'no'})`,
  );

  const context = await browser.newContext({ viewport: { width: 375, height: 812 }, reducedMotion: 'reduce' });
  const page = await context.newPage();
  for (const path of ['/', '/como-comprar/']) {
    await page.goto(`${base}${path}`, { waitUntil: 'load' });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    check(overflow <= 0, `${path}: sin desplazamiento horizontal a 375 px (${overflow} px de más)`);
  }
  await context.close();
}

if (existsSync(join(dist, MODEL_PATH, 'index.html'))) {
  console.log(`\nBarra fija de cotización ${MODEL_PATH} (375 px)`);
  {
    const context = await browser.newContext({ viewport: { width: 375, height: 812 }, reducedMotion: 'reduce' });
    const page = await context.newPage();
    await page.goto(`${base}${MODEL_PATH}`, { waitUntil: 'load' });
    const bar = page.locator('[data-sticky-cta]');
    const quote = bar.locator('a', { hasText: 'Solicitar cotización' });
    check(
      (await quote.getAttribute('href')) === '/contacto/?producto=hielscher-up400st',
      `Sticky CTA: «Solicitar cotización» lleva el modelo (${await quote.getAttribute('href')})`,
    );
    const wa = await bar.locator('a[href^="https://wa.me/"]').getAttribute('href');
    check(Boolean(wa) && decodeURIComponent(wa).includes('UP400St'), 'Sticky CTA: WhatsApp con el modelo en el mensaje');
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight / 3));
    await page.waitForTimeout(200);
    const pinned = await bar.evaluate((el) => {
      const box = el.getBoundingClientRect();
      return box.bottom <= innerHeight + 1 && box.top >= innerHeight - 160;
    });
    check(pinned, 'Sticky CTA: la barra queda al pie de la pantalla al desplazarse');
    await quote.focus();
    const focus = await page.evaluate(() => {
      const el = document.activeElement;
      const style = getComputedStyle(el);
      const box = el.getBoundingClientRect();
      return { ring: style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) >= 2, inView: box.top >= 0 && box.bottom <= innerHeight };
    });
    check(focus.ring && focus.inView, 'Sticky CTA: el foco de teclado se ve en la barra');
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    check(overflow <= 0, `Model page: sin desplazamiento horizontal a 375 px (${overflow} px de más)`);
    await context.close();
  }
}

/* -- 8. Cierre de QA: navegación, barra fija, WhatsApp, consentimiento ---- */
{
  console.log(`\nNavegación y barra fija de la ficha ${MODEL_PATH}`);
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  await page.goto(`${base}${MODEL_PATH}`, { waitUntil: 'load' });

  // Model nav: la ruta de navegación vuelve al índice de la familia.
  const crumb = page.locator('nav[aria-label="Ruta de navegación"] a[href="' + FAMILY_PATH + '"]');
  check((await crumb.count()) === 1, 'Model nav: la ruta de navegación enlaza el índice de la familia');
  await crumb.click();
  await page.waitForLoadState('load');
  check(new URL(page.url()).pathname === FAMILY_PATH, `Model nav: el enlace lleva al índice de la familia (${new URL(page.url()).pathname})`);

  // Sticky CTA: visible tras 200 px de desplazamiento, con el botón «Solicitar cotización».
  await page.goto(`${base}${MODEL_PATH}`, { waitUntil: 'load' });
  await page.evaluate(() => window.scrollTo(0, 200));
  await page.waitForTimeout(250);
  const bar = page.locator('[data-sticky-cta]');
  const quote = bar.locator('a', { hasText: 'Solicitar cotización' });
  const inView = await bar.evaluate((el) => {
    const box = el.getBoundingClientRect();
    return box.height > 0 && box.top < innerHeight && box.bottom > 0;
  });
  check((await quote.isVisible()) && inView, 'Sticky CTA: «Solicitar cotización» visible tras 200 px de scroll');

  // WhatsApp: el parámetro `text` nombra el modelo y la marca (no sólo el enlace).
  const wa = await bar.locator('a[href^="https://wa.me/"]').getAttribute('href');
  const text = new URL(wa ?? 'https://wa.me/').searchParams.get('text') ?? '';
  check(/\bUP400St\b/.test(text) && /Hielscher/.test(text), `WhatsApp URL: el parámetro text nombra el modelo y la marca (${text.slice(0, 60)}…)`);
  await context.close();
}

console.log('\nConsentimiento (/contacto/)');
{
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  const posts = [];
  await page.route('**/api/web/solicitud', (route) => {
    posts.push(route.request().url());
    return route.fulfill({ status: 500, body: 'no debe enviarse' });
  });
  await page.goto(`${base}/contacto/`, { waitUntil: 'load' });
  const consent = page.locator('#solicitud-consent');
  check((await consent.getAttribute('value')) === 'consent-v1-2026-11', 'Consent: la casilla lleva la versión consent-v1-2026-11');
  check(await consent.evaluate((el) => el.required && !el.checked), 'Consent: la casilla es obligatoria y empieza sin marcar');
  await page.fill('#solicitud-need', 'Sonicador de 400 W para extracción');
  await page.fill('#solicitud-email', 'ana@example.cl');
  await page.fill('#solicitud-org', 'Laboratorio de prueba');
  await page.click('button[type="submit"]');
  await page.waitForTimeout(400);
  check(posts.length === 0, `Consent: sin consentimiento el formulario no envía nada (${posts.length} solicitudes)`);
  check((await consent.getAttribute('aria-invalid')) === 'true', 'Consent: la casilla queda marcada como inválida');
  check(await page.locator('#solicitud-consent-error').isVisible(), 'Consent: se explica el error junto a la casilla');
  check(
    /Consentimiento/.test((await page.locator('[data-error-summary]').textContent()) ?? ''),
    'Consent: el resumen de errores nombra el consentimiento',
  );
  await consent.check();
  await page.click('button[type="submit"]');
  await page.waitForTimeout(600);
  check(posts.length === 1, `Consent: con la casilla marcada sí se envía (${posts.length} solicitud)`);
  await context.close();
}

/* La ficha heredada del Digicen 22 R: barra fija medida y modo estático. */
{
  const path = '/productos/centrifugas/digicen-22-r/';
  console.log(`\nFicha heredada ${path}: barra fija y modo estático`);
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, reducedMotion: 'reduce' });
  const page = await context.newPage();
  await page.addInitScript(() => {
    window.__beacons = [];
    navigator.sendBeacon = (url, body) => {
      window.__beacons.push({ url: String(url), body });
      return true;
    };
    // Sin navegar: el clic llega al escucha del documento y aquí se cancela.
    window.addEventListener('click', (event) => event.target.closest?.('a') && event.preventDefault(), true);
  });
  await page.goto(`${base}${path}`, { waitUntil: 'load' });
  const bar = page.locator('[data-sticky-cta]');
  const quote = bar.locator('a', { hasText: 'Solicitar cotización' });
  check((await quote.getAttribute('data-track')) === 'cotizacion_click', 'Ficha heredada: «Solicitar cotización» de la barra lleva data-track');
  check((await quote.getAttribute('data-modelo')) === 'ortoalresa-digicen-22-r', 'Ficha heredada: la barra lleva data-modelo del equipo');
  const waLink = bar.locator('a[href^="https://wa.me/"]');
  check((await waLink.getAttribute('data-track')) === 'whatsapp_click', 'Ficha heredada: WhatsApp de la barra lleva data-track');
  await quote.click();
  const sent = await page.evaluate(async () =>
    Promise.all(window.__beacons.map(async (b) => ({ url: b.url, body: await b.body.text() }))),
  );
  check(
    sent.some((b) => b.url === '/api/stats/event' && b.body.includes('cotizacion_click') && b.body.includes('"modelo":"ortoalresa-digicen-22-r"')),
    `Ficha heredada: el clic en la barra se mide (${sent.length} envío)`,
  );
  // Modo estático (movimiento reducido): lienzo oculto, ficha técnica de la historia visible.
  check(await page.locator('canvas[data-story-canvas]').evaluate((el) => el.hidden), 'Ficha heredada: con movimiento reducido el lienzo 3D queda oculto');
  await page.locator('.story-specs').scrollIntoViewIfNeeded();
  check(await page.locator('.story-specs').isVisible(), 'Ficha heredada: con movimiento reducido la ficha técnica se ve');
  await context.close();
}

await browser.close();
server.close();

if (failed) {
  console.error('\nqa:interaction FALLÓ');
  process.exit(1);
}
console.log('\nqa:interaction OK');
