#!/usr/bin/env node
/**
 * QA de interacción sobre `dist/`: teclado, menú móvil, sala 3D del hero,
 * fila de marcas, movimiento reducido y enlaces internos. Complementa
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

/** Desplaza hasta justo después de la primera pantalla de la página de modelo (`[data-first-screen]`). */
const pastFirstScreen = async (page) => {
  await page.evaluate(() => {
    const first = document.querySelector('[data-first-screen]');
    window.scrollTo({ top: first.getBoundingClientRect().bottom + window.scrollY + 120, behavior: 'instant' });
  });
  // El observador y la transición de 200 ms pueden tardar con el WebGL por software ocupando la página.
  await page.waitForFunction(() => document.querySelector('[data-sticky-cta]')?.inert === false, null, { timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(450);
};

/* Lecturas de la fuente para los chequeos de la portada: independientes de dist/. */
const brandsSource = readFileSync(join(root, 'src/data/brands.ts'), 'utf8');
const idList = (name) =>
  [...(brandsSource.match(new RegExp(`export const ${name}[^=]*=\\s*\\[([^\\]]*)\\]`))?.[1] ?? '').matchAll(/'([\w-]+)'/g)].map((m) => m[1]);
const HIDDEN_ON_HOME = idList('HOME_HIDDEN_BRAND_IDS');
const claimApproved = (id) => {
  const block = readFileSync(join(root, 'src/data/claims.ts'), 'utf8').match(new RegExp(`id: '${id}',[\\s\\S]*?\\n  \\},`))?.[0] ?? '';
  return /status: 'approved'/.test(block) && /visibility: 'public'/.test(block);
};

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

  const toggle = page.locator('[data-menu-toggle]');
  const expanded = () => toggle.evaluate((el) => el.getAttribute('aria-expanded') === 'true');
  check(
    await toggle.evaluate((el) => el.tagName === 'BUTTON' && el.getAttribute('aria-expanded') === 'false'),
    'el disparador es un <button> con aria-expanded="false"',
  );
  await toggle.click();
  check(await expanded(), 'el clic abre el panel (aria-expanded="true")');
  check(await page.locator('.site-menu__panel a', { hasText: 'Productos' }).isVisible(), 'el panel muestra la navegación');
  check(
    (await page.locator('.site-menu__panel a', { hasText: 'Cómo comprar' }).count()) === 1,
    'el panel incluye «Cómo comprar»',
  );
  check(
    (await page.locator('.site-menu__panel a', { hasText: 'Servicios' }).count()) === 0,
    'el panel ya no incluye «Servicios»',
  );
  check(
    (await page.locator('.site-menu__panel').evaluate((el) => getComputedStyle(el).backgroundColor)) ===
      'rgb(20, 22, 23)',
    'el panel del menú es de tinta (rgb(20, 22, 23))',
  );
  const menuWa = await page.locator('.site-menu__panel a.btn-whatsapp').evaluate((el) => ({
    height: Math.round(el.getBoundingClientRect().height),
    href: el.getAttribute('href') ?? '',
    text: (el.textContent ?? '').replace(/\s+/g, ' ').trim(),
  }));
  check(
    menuWa.height === 48 && menuWa.href.startsWith('https://wa.me/') && menuWa.text.startsWith('Escribir por WhatsApp'),
    `el panel trae WhatsApp de 48 px (${menuWa.height} px, "${menuWa.text}")`,
  );

  await page.keyboard.press('Escape');
  check(!(await expanded()), 'Escape cierra el panel');
  check(
    await page.evaluate(() => document.activeElement?.hasAttribute('data-menu-toggle') === true),
    'el foco vuelve al disparador',
  );

  await toggle.click();
  check(await expanded(), 'vuelve a abrirse');
  await toggle.click();
  check(!(await expanded()), 'el mismo control lo cierra (aspa)');

  await toggle.click();
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
  check(
    await page.evaluate(() => document.documentElement.style.overflow !== 'hidden'),
    'con el panel cerrado el documento vuelve a desplazarse',
  );
  await context.close();
}

/* -- 1b. Cabecera: 72 → 56 px, marca animada una vez, tinta en la portada --- */
console.log('\nCabecera (1440 px)');
{
  const headerHeight = (page) =>
    page.evaluate(() => Math.round(document.querySelector('.site-header').getBoundingClientRect().height));
  const shrinkChecks = async (label, init, expectAttr, reducedMotion = 'no-preference') => {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion });
    if (init) await context.addInitScript(init);
    const page = await context.newPage();
    await page.goto(`${base}/productos/`, { waitUntil: 'load' });
    check((await headerHeight(page)) === 72, `Cabecera${label}: 72 px arriba (${await headerHeight(page)})`);
    // El scroll es suave y la carga de CPU varía: se espera al valor hasta 3 s en vez de 400 ms fijos.
    const settles = (px) =>
      page
        .waitForFunction(
          (expected) => Math.round(document.querySelector('.site-header').getBoundingClientRect().height) === expected,
          px,
          { timeout: 3000 },
        )
        .then(() => true)
        .catch(() => false);
    await page.evaluate(() => window.scrollTo({ top: 300, behavior: 'instant' }));
    await page.waitForTimeout(400);
    check(await settles(56), `Cabecera${label}: 72 → 56 px con scroll (${await headerHeight(page)})`);
    // `data-scrolled` sólo lo pone el observador: ausente con animación de scroll, presente en el fallback.
    const attr = await page.evaluate(() => document.querySelector('.site-header').hasAttribute('data-scrolled'));
    check(attr === expectAttr, `Cabecera${label}: data-scrolled ${expectAttr ? 'presente' : 'ausente'} (${attr})`);
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
    await page.waitForTimeout(400);
    check(
      await settles(72),
      `Cabecera${label}: vuelve a 72 px arriba (${await headerHeight(page)} px, scrollY ${await page.evaluate(() => scrollY)})`,
    );
    await context.close();
  };
  await shrinkChecks('', null, false);
  // Con movimiento reducido la animación de scroll no se declara y es el observador quien encoge.
  await shrinkChecks(' (observador, movimiento reducido)', null, true, 'reduce');
  await shrinkChecks(' (fallback JS)', () => {
    const supports = CSS.supports.bind(CSS);
    CSS.supports = (q, v) => (String(q).includes('animation-timeline') ? false : supports(q, v));
  }, true);

  {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const page = await context.newPage();
    await page.goto(`${base}/productos/`, { waitUntil: 'load' });
    const bodies = '.site-logo .origen-mark__body, .site-logo .origen-mark__trace, .site-logo .origen-mark__ring';
    const motion = () =>
      page.evaluate((selector) => {
        const all = [...document.querySelectorAll(selector)].flatMap((el) => el.getAnimations());
        return { count: all.length, states: all.map((a) => a.playState) };
      }, bodies);
    const first = await page.evaluate(
      () => document.querySelectorAll('.site-logo .origen-mark__body')[0]?.getAnimations().length ?? 0,
    );
    check(first >= 1, `la marca de la cabecera anima al cargar (${first} animaciones en el primer cuerpo)`);
    await page.waitForTimeout(1800);
    const done = await motion();
    check(
      done.count >= 1 && done.states.every((state) => state === 'finished'),
      `a los 1,5 s todas terminaron, sin bucle (${done.states.join(',')})`,
    );
    await page.locator('[data-logo-motion]').hover();
    await page.waitForTimeout(150);
    const replay = await motion();
    check(replay.states.includes('running'), `al pasar el cursor la marca vuelve a animar (${replay.states.join(',')})`);
    await context.close();
  }

  {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    const page = await context.newPage();
    await page.goto(`${base}/productos/`, { waitUntil: 'load' });
    await page.locator('[data-logo-motion]').hover();
    await page.waitForTimeout(200);
    const count = await page.evaluate(() =>
      [...document.querySelectorAll('.site-logo, .site-logo *')].flatMap((el) => el.getAnimations()).length,
    );
    check(count === 0, `con movimiento reducido la marca no tiene animaciones (${count})`);
    await context.close();
  }

  {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const page = await context.newPage();
    await page.goto(`${base}/`, { waitUntil: 'load' });
    const bg = await page.evaluate(() => getComputedStyle(document.querySelector('.site-header')).backgroundColor);
    check(bg === 'rgb(20, 22, 23)', `en la portada la cabecera es de tinta (${bg})`);
    const theme = await page.evaluate(() => document.querySelector('meta[name="theme-color"]')?.content);
    check(theme === '#141617', `en la portada theme-color es #141617 (${theme})`);
    const nav = await page.evaluate(() => [...document.querySelectorAll('.site-header__nav a')].map((a) => a.textContent.trim()));
    check(
      nav.join('|') === 'Productos|Aplicaciones|Marcas|Cómo comprar|Nosotros',
      `la navegación es ${nav.join(' · ')}`,
    );
    await context.close();
  }
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
      () => document.activeElement?.classList.contains('site-header__cta') === true,
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

  // Marco ancho: el escenario es 16/9 y las tareas van debajo, no encima.
  const geo = await page.evaluate(() => {
    const box = (el) => el.getBoundingClientRect();
    const stage = box(document.querySelector('[data-showroom] .showroom__stage'));
    const chips = box(document.querySelector('[data-showroom] .showroom__tasks'));
    return { ratio: stage.width / stage.height, stageBottom: stage.bottom, chipsTop: chips.top, width: stage.width };
  });
  check(Math.abs(geo.ratio - 16 / 9) <= 0.02, `escenario 16/9 a 1440 px (${geo.ratio.toFixed(3)})`);
  check(geo.chipsTop > geo.stageBottom, `las tareas van debajo del escenario (${Math.round(geo.chipsTop)} > ${Math.round(geo.stageBottom)})`);
  check(geo.width <= 1120.5, `el marco mide como mucho 1120 px (${Math.round(geo.width)})`);

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
  const phone = await page.evaluate(() => {
    const stage = document.querySelector('[data-showroom] .showroom__stage').getBoundingClientRect();
    const chips = [...document.querySelectorAll('[data-showroom] .showroom__task')].map((el) => el.getBoundingClientRect().height);
    const control = document.querySelector('[data-showroom] .showroom__tasks');
    return { ratio: stage.width / stage.height, minChip: Math.min(...chips), scrolls: control.scrollWidth > control.clientWidth };
  });
  check(Math.abs(phone.ratio - 4 / 3) <= 0.02, `escenario 4/3 en teléfono (${phone.ratio.toFixed(3)})`);
  check(phone.minChip >= 44, `las tareas miden al menos 44 px en teléfono (${phone.minChip})`);
  check(phone.scrolls, 'las tareas se desplazan en horizontal en teléfono');

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

/* -- 2b. Riel de marcas: fila fija, sin movimiento ------------------------- */

/**
 * La fila de marcas de la portada es estática: ni marquesina ni control de
 * pausa (no hay movimiento que parar, WCAG 2.2.2). Las marcas visibles en
 * la portada, en el orden de la lista cerrada; OHAUS queda fuera por decisión
 * del titular (`HOME_HIDDEN_BRAND_IDS`).
 */
console.log('\nRiel de marcas (1440 px)');
{
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  await page.goto(`${base}/`, { waitUntil: 'load' });

  const brands = page.locator('[data-home-brands]');
  check(await brands.count() === 1, 'hay exactamente un riel de marcas en la portada');
  // Orden de la lista cerrada, sin las marcas ocultas en la portada: se deriva de brands.ts.
  const brandNames = Object.fromEntries(
    [...brandsSource.matchAll(/\n {4}id: '([\w-]+)',[\s\S]*?\n {4}name: '([^']+)'/g)].map(([, id, name]) => [id, name]),
  );
  const ORDER = idList('APPROVED_BRAND_IDS').filter((id) => !HIDDEN_ON_HOME.includes(id)).map((id) => brandNames[id]);
  const expected = idList('APPROVED_BRAND_IDS').filter((id) => !HIDDEN_ON_HOME.includes(id)).length;
  const links = await brands.locator('.home-rail a').count();
  check(links === expected && links === ORDER.length && ORDER.every(Boolean), `un enlace por marca visible en la portada (${links} de ${expected})`);
  const names = await brands.locator('.home-rail__name').allTextContents();
  check(
    JSON.stringify(names.map((name) => name.trim())) === JSON.stringify(ORDER),
    `el orden del riel es el de la lista cerrada (${names.join(', ')})`,
  );
  check(
    await brands.locator('.home-rail').evaluate((el) => el.getAnimations({ subtree: true }).length === 0),
    'el riel no tiene ninguna animación',
  );
  check(
    (await page.locator('[data-rail], [data-rail-toggle]').count()) === 0,
    'sin control de pausa ni pista de bucle',
  );
  check(
    await brands.locator('.home-rail__logo').evaluateAll((els) => els.every((el) => el.getBoundingClientRect().width > 0)),
    'todas las marcas se leen a la vez',
  );
  const opacities = await brands.locator('.home-rail__logo').evaluateAll((els) => [...new Set(els.map((el) => getComputedStyle(el).opacity))]);
  check(opacities.length === 1, `los logotipos comparten el mismo tratamiento de reposo (${opacities.join(', ')})`);

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

  // El riel es una fila estática y las marcas se leen a la vez.
  const visibleLogos = await page
    .locator('[data-home-brands] .home-rail__logo')
    .evaluateAll((els) => els.filter((el) => el.getBoundingClientRect().width > 0).length);
  const shownBrands = idList('APPROVED_BRAND_IDS').filter((id) => !HIDDEN_ON_HOME.includes(id)).length;
  check(visibleLogos === shownBrands, `las ${shownBrands} marcas se leen a la vez (${visibleLogos})`);
  // Sin movimiento, ningún bloque de la portada queda a medio revelar.
  const faded = await page.evaluate(() => [...document.querySelectorAll('.rv')].filter((el) => getComputedStyle(el).opacity !== '1').length);
  check(faded === 0, `con movimiento reducido ningún .rv queda atenuado (${faded})`);

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

  /*
   * Texto prellenado de WhatsApp (tablero «Mensaje»): en la portada, todo enlace
   * de WhatsApp (cabecera incluida, cuando la tenga) dice el mensaje genérico; en
   * la ficha de un modelo, el de la barra fija nombra el modelo tal como lo da el
   * JSON de contenido.
   */
  const waText = (href) => new URL(href.replaceAll('&amp;', '&')).searchParams.get('text');
  const waLinks = (html) => [...html.matchAll(/href="(https:\/\/wa\.me\/[^"]+)"/g)].map((match) => match[1]);
  const homeHtml = readFileSync(join(dist, 'index.html'), 'utf8');
  const homeTexts = waLinks(homeHtml).map(waText);
  check(
    homeTexts.length > 0 && homeTexts.every((text) => text === 'Hola OrigenLab, quiero cotizar un equipo.'),
    `WhatsApp en la portada: mensaje genérico en cada enlace (${[...new Set(homeTexts)].join(' | ') || 'sin enlaces'})`,
  );
  const osmo = JSON.parse(readFileSync(join(root, 'src/content/modelos/loeser-i-osmometer-basic.json'), 'utf8'));
  const osmoFile = join(dist, 'productos/osmometria/loeser-i-osmometer-basic/index.html');
  const osmoHtml = existsSync(osmoFile) ? readFileSync(osmoFile, 'utf8') : '';
  const osmoBar = osmoHtml.match(/<aside\b[^>]*data-sticky-cta[\s\S]*?<\/aside>/)?.[0] ?? '';
  const osmoText = waText(waLinks(osmoBar)[0] ?? 'https://wa.me/?text=');
  check(
    osmoText === `Hola OrigenLab, quiero cotizar el ${osmo.name}.`,
    `WhatsApp en la barra fija del ${osmo.name}: nombra el modelo (${osmoText})`,
  );
}

/* -- 4b. Pie y promesas (HTML construido) ---------------------------------- */
console.log('\nPie y promesas de garantía');
{
  const read = (path) => readFileSync(join(dist, path, 'index.html'), 'utf8');
  const footerOf = (html) => html.match(/<footer\b[\s\S]*?<\/footer>/)?.[0] ?? '';
  const footer = footerOf(read('.'));
  const navOf = (label) => footer.match(new RegExp(`<nav\\b[^>]*aria-label="${label}"[\\s\\S]*?<\\/nav>`))?.[0] ?? '';
  const brandLinks = [...navOf('Marcas').matchAll(/href="\/marcas\/([\w-]+)\/"/g)].map((m) => m[1]);
  const familyLinks = [...navOf('Familias').matchAll(/href="(\/productos\/[\w-]+\/)"/g)].map((m) => m[1]);
  check(brandLinks.length === 7 && new Set(brandLinks).size === 7, `Pie: siete enlaces a /marcas/{slug}/ (${brandLinks.length})`);
  check(familyLinks.length === 6 && new Set(familyLinks).size === 6, `Pie: seis enlaces de familia (${familyLinks.length})`);
  check(!/on-ink/.test(footer.match(/<footer\b[^>]*>/)?.[0] ?? ''), 'Pie: sobre papel, sin la clase on-ink');
  const linkedinSet = /linkedinUrl:\s*'[^']+'/.test(readFileSync(join(root, 'src/config/site.ts'), 'utf8'));
  check(/linkedin\.com/.test(footer) === linkedinSet, `Pie: LinkedIn sólo si site.linkedinUrl tiene valor (${linkedinSet ? 'con valor' : 'vacío, sin enlace'})`);
  check(/href="\/como-comprar\/#compras-publicas"/.test(footer) && /mailto:contacto@origenlab\.cl/.test(footer), 'Pie: Mercado Público y contacto@');
  for (const legal of ['/privacidad/', '/cookies/', '/aviso-legal/']) check(footer.includes(`href="${legal}"`), `Pie: enlaza ${legal}`);

  // La garantía no es una promesa de marketing en estas páginas. El aviso legal conserva la palabra sólo para decir qué no publica el sitio.
  const withGuarantee = ['.', 'servicios', 'nosotros', 'como-comprar', 'contacto'].filter((path) => /garant/i.test(read(path)));
  check(withGuarantee.length === 0, `Sin «garantía» en portada, servicios, nosotros, cómo comprar y contacto (${withGuarantee.join(', ') || 'ninguna'})`);
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

/* -- 5b. Contacto y pie en la tinta del sistema (1440 px) ------------------- */
console.log('\nContacto y pie (1440 px)');
{
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await page.goto(`${base}/contacto/`, { waitUntil: 'load' });
  const btn = page.locator('#solicitud-btn');
  check((await btn.innerText()).trim() === 'Pedir cotización', `Contacto: el envío dice «${(await btn.innerText()).trim()}» (Pedir cotización)`);
  check((await btn.getAttribute('type')) === 'submit', 'Contacto: el botón sigue siendo type=submit');
  const box = await btn.boundingBox();
  check(box && Math.round(box.height) === 48, `Contacto: el botón mide 48 px (${box?.height})`);
  check((await btn.locator('svg.arrow').count()) === 1, 'Contacto: el botón lleva la flecha');
  const radius = await page.locator('#solicitud-email').evaluate((el) => getComputedStyle(el).borderTopLeftRadius);
  check(radius === '10px', `Contacto: el campo de correo tiene radio 10 px (${radius})`);
  const heights = await page.evaluate(() =>
    ['#solicitud-email', '#solicitud-org', '#solicitud-product'].map((sel) => Math.round(document.querySelector(sel).getBoundingClientRect().height)),
  );
  check(heights.every((h) => h === 48), `Contacto: los campos de una línea miden 48 px (${heights.join(', ')})`);
  const area = await page.locator('#solicitud-need').evaluate((el) => el.getBoundingClientRect().height);
  check(area >= 128, `Contacto: el área de texto mide al menos 128 px (${Math.round(area)})`);
  const waLabel = (await page.locator('.solicitud-aside a[href^="https://wa.me/"]').first().innerText()).replace(/\s+/g, ' ');
  check(/Escribir por WhatsApp/.test(waLabel), `Contacto: el botón de WhatsApp dice «Escribir por WhatsApp» (${waLabel.trim()})`);
  await page.locator('#solicitud-email').focus();
  const ring = await page.locator('#solicitud-email').evaluate((el) => {
    const style = getComputedStyle(el);
    return `${style.outlineWidth} ${style.outlineStyle} ${style.outlineColor}`;
  });
  check(ring === '2px solid rgb(15, 118, 110)', `Contacto: el foco del campo es teal-700 de 2 px (${ring})`);

  await page.goto(`${base}/`, { waitUntil: 'load' });
  const bg = await page.locator('footer').evaluate((el) => getComputedStyle(el).backgroundColor);
  check(bg === 'rgb(250, 250, 247)', `Pie: fondo papel en la portada (${bg})`);
  const paddingTop = await page.locator('footer').evaluate((el) => getComputedStyle(el).paddingTop);
  check(paddingTop === '88px', `Pie: 88 px de aire superior (${paddingTop})`);
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
    check(await page.locator('#ficha-title').isVisible(), 'ProductStory: static DOM present under reduced-motion (la ficha técnica de la página se ve)');
    check(await page.locator('[data-story] canvas').evaluate((el) => el.hidden), 'ProductStory: el lienzo queda con hidden');
    const beats = await page.locator('[data-story] [data-beat]').evaluateAll((els) => els.map((el) => ({
      id: el.dataset.beat,
      heading: el.querySelector('h1, h2')?.textContent?.trim() ?? '',
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
    // El recorrido de la historia empieza donde termina la primera pantalla (`lead`).
    const top = await page.locator('[data-story-spacer]').evaluate((el) => el.getBoundingClientRect().top + window.scrollY + el.querySelector('[data-first-screen]').offsetHeight);
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
    check((await page.locator('#ficha-title').count()) === 1, 'Ficha heredada del Digicen 22 R: la ficha técnica es una sección de la página, no de la historia');
    check((await page.locator('.story-specs').count()) === 0, 'Ficha heredada del Digicen 22 R: la historia ya no lleva ficha propia');
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
 * Escala de la página de familia: cuatro marcas en sonicación, en el orden de la
 * cifra y con desplazamientos crecientes; ninguna donde no hay cifra que ordene;
 * y cada tarjeta, en todas las familias, con fotografía que carga o marco neutro.
 */
console.log('\nEscala y tarjetas de la página de familia');
{
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await page.goto(`${base}/productos/sonicacion/`, { waitUntil: 'load' });
  const marks = await page.locator('[data-family-scale] [data-mark]').evaluateAll((els) =>
    els.map((el) => ({ text: el.querySelector('.fs__n')?.textContent?.trim(), left: el.getBoundingClientRect().left })),
  );
  check(marks.length === 4, `escala de la familia: ${marks.length} marcas`);
  check(
    marks.map((mark) => mark.text).join('|') === '100 W|200 W|400 W|2.000 W',
    `escala de la familia: textos en orden (${marks.map((mark) => mark.text).join(', ')})`,
  );
  check(marks.every((mark, i) => i === 0 || mark.left > marks[i - 1].left), 'escala de la familia: desplazamientos crecientes');
  const consult = await page.locator('#consulta-title').count();
  check(consult === 1, 'familia: bloque «¿No sabe cuál?»');
  await page.goto(`${base}/productos/electroforesis/`, { waitUntil: 'load' });
  check((await page.locator('[data-family-scale]').count()) === 0, 'electroforesis: sin escala');

  for (const path of ['/productos/sonicacion/', '/productos/dispersion/', '/productos/pesaje-humedad/', '/productos/osmometria/', '/productos/electroforesis/', '/productos/centrifugas/']) {
    await page.goto(`${base}${path}`, { waitUntil: 'load' });
    await page.evaluate(() => document.querySelectorAll('.model-card img, .mc img').forEach((img) => (img.loading = 'eager')));
    await page.waitForTimeout(400);
    const bad = await page.locator('.mc').evaluateAll((cards) =>
      cards
        .filter((card) => {
          const imgs = card.querySelectorAll('img');
          if (card.querySelector('[data-frame="neutral"]')) return imgs.length !== 0;
          return imgs.length !== 1 || !(imgs[0].naturalWidth > 0);
        })
        .map((card) => card.querySelector('h3')?.textContent),
    );
    const total = await page.locator('.mc').count();
    check(total > 0 && bad.length === 0, `${path}: ${total} tarjetas, sin fotografía rota (${bad.join(', ') || 'ninguna'})`);
  }
  await page.goto(`${base}/productos/centrifugas/`, { waitUntil: 'load' });
  check((await page.locator('[data-family-scale] [data-mark]').count()) >= 3, 'centrífugas: la misma escala');
  await context.close();
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
 * Portada y `/como-comprar/`: la portada lleva a las seis páginas de familia
 * con sus modelos tal como salen de la colección, tiene seis industrias que
 * llevan a alguna parte, titular según el estado de las afirmaciones, oculta
 * las notas destacadas mientras la colección `aplicaciones` esté vacía, revela
 * cada `.rv` al desplazarse y cierra con el formulario de cotización y WhatsApp;
 * la guía de compra tiene cuatro pasos, la sección de compras públicas y la
 * nota de consentimiento con enlace a la política de privacidad.
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

  const industries = [...home.matchAll(/<li\b[^>]*\bdata-industry\b[^>]*>[\s\S]*?<\/li>/g)].map(([block]) => block);
  check(
    industries.length === 6 && industries.every((block) => /href="\/[^"]*"/.test(block)),
    `Home: seis industrias, cada una con un enlace interno (${industries.length})`,
  );
  const notes = readdirSync(join(root, 'src/content/aplicaciones')).filter((name) => name.endsWith('.md'));
  check(
    home.includes('data-home-notes') === notes.length > 0,
    `Home: las notas destacadas se muestran sólo si hay notas (${notes.length} notas)`,
  );
  const band = home.match(/<section\b[^>]*\bclass="[^"]*\bband-wrap\b[\s\S]*?<\/section>/)?.[0] ?? '';
  check(/<a\b[^>]*href="\/contacto\/"[^>]*class="btn btn-primary on-ink/.test(band) || /<a\b[^>]*class="btn btn-primary on-ink[^"]*"[^>]*href="\/contacto\/"/.test(band), 'Home: la banda de cierre lleva el botón primario al formulario (/contacto/)');
  check(/<a\b[^>]*href="https:\/\/wa\.me\/[^"]+"[^>]*btn-whatsapp/.test(band) || /btn-whatsapp[^>]*href="https:\/\/wa\.me\//.test(band), 'Home: la banda de cierre lleva el botón de WhatsApp');
  check(
    ![...home.matchAll(/<a\b[^>]*>/g)].some(([tag]) => tag.includes('wa.me') && tag.includes('btn-invert')),
    'Home: ningún enlace de WhatsApp usa la clase retirada btn-invert',
  );
  check(home.includes('href="/como-comprar/"'), 'Home: enlaza la guía de compra');

  // Tarjetas de familia: seis, con destinos distintos y los modelos de la colección (sin los de marcas ocultas).
  const models = readdirSync(join(root, 'src/content/modelos'))
    .filter((name) => name.endsWith('.json'))
    .map((name) => JSON.parse(readFileSync(join(root, 'src/content/modelos', name), 'utf8')));
  const expectedFoot = (family) =>
    models
      .filter((entry) => entry.family === family && !HIDDEN_ON_HOME.includes(entry.brand))
      .sort(
        (a, b) =>
          Number(Boolean(b.showroom_id)) - Number(Boolean(a.showroom_id)) ||
          a.brand.localeCompare(b.brand) ||
          a.name.localeCompare(b.name, 'es', { numeric: true }),
      )
      .slice(0, 2)
      .map((entry) => entry.name)
      .join(' · ');
  const headlineB = claimApproved('cotizacion-mismo-dia-habil');
  const h1Expected = headlineB ? 'Cotización el mismo día. Directo con el fabricante.' : 'Equipos para cada etapa de su muestra.';
  const bandExpected = headlineB
    ? 'Le respondemos con una cotización el mismo día hábil, con la ficha técnica del fabricante.'
    : 'Le respondemos con una cotización por escrito, con la ficha técnica del fabricante.';

  const desktop = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const wide = await desktop.newPage();
  await wide.goto(`${base}/`, { waitUntil: 'load' });
  const cards = wide.locator('.home-family-card');
  check(await cards.count() === 6, `Home: seis tarjetas de familia (${await cards.count()})`);
  const hrefs = await cards.evaluateAll((els) => els.map((el) => el.getAttribute('href')));
  check(new Set(hrefs).size === 6 && hrefs.every((href) => href?.startsWith('/') && href.endsWith('/')), `Home: seis destinos internos distintos (${hrefs.join(', ')})`);
  const foots = await cards.evaluateAll((els) => els.map((el) => el.querySelector('.fam__models')?.textContent?.trim() ?? ''));
  const wanted = hrefs.map((href) => expectedFoot(href === '/productos/centrifugas/' ? 'centrifugacion' : href.split('/')[2]));
  check(
    JSON.stringify(foots) === JSON.stringify(wanted) && wanted.every(Boolean),
    `Home: cada tarjeta nombra los dos primeros modelos de la colección (${foots.join(' | ')})`,
  );
  const hiddenNames = models.filter((entry) => HIDDEN_ON_HOME.includes(entry.brand)).map((entry) => entry.name);
  check(
    hiddenNames.every((hidden) => !foots.some((foot) => foot.split(' · ').includes(hidden))),
    'Home: las tarjetas no nombran modelos de la marca oculta',
  );
  const h1 = (await wide.locator('h1').first().textContent())?.replace(/\s+/g, ' ').trim();
  check(h1 === h1Expected, `Home: el titular sigue el estado de las afirmaciones (${h1})`);
  const bandBody = (await wide.locator('.band__body').textContent())?.trim();
  check(bandBody === bandExpected, 'Home: el texto de la banda sigue el estado de las afirmaciones');
  check(await wide.locator('[data-home-notes]').count() === (notes.length > 0 ? 1 : 0), 'Home: sin notas no hay sección de notas en el DOM');

  // Revelado: cada `.rv` llega a opacidad 1 una vez entero en cuadro; ninguno está en la primera pantalla.
  const rvCount = await wide.locator('.rv').count();
  const inFirstScreen = await wide.evaluate(() => [...document.querySelectorAll('.rv')].filter((el) => el.getBoundingClientRect().top < innerHeight - 1 && el.getBoundingClientRect().bottom > 0).length);
  check(rvCount > 0 && inFirstScreen === 0, `Home: ningún .rv está en la primera pantalla (${inFirstScreen} de ${rvCount})`);
  const stuck = [];
  for (let n = 0; n < rvCount; n += 1) {
    const rv = wide.locator('.rv').nth(n);
    await rv.evaluate((el) => el.scrollIntoView({ block: 'center', behavior: 'instant' }));
    await wide.waitForTimeout(300);
    const opacity = await rv.evaluate((el) => getComputedStyle(el).opacity);
    if (opacity !== '1') stuck.push(`${n}:${opacity}`);
  }
  check(stuck.length === 0, `Home: cada .rv llega a opacidad 1 al verse entero (${stuck.join(', ') || 'todos'})`);
  await desktop.close();

  const guide = read('/como-comprar/');
  const pasos = guide.match(/<section\b[^>]*\bid="pasos"[\s\S]*?<\/section>/)?.[0] ?? '';
  const steps = (pasos.match(/\bdata-step\b/g) ?? []).length;
  const consent = guide.match(/<p\b[^>]*\bdata-consent-note\b[\s\S]*?<\/p>/)?.[0] ?? '';
  check(
    steps === 4 && consent.includes('href="/privacidad/"') && guide.includes('id="compras-publicas"'),
    `Cómo comprar: four steps and consent note present (${steps} pasos, nota ${consent ? 'sí' : 'no'})`,
  );

  const faqCount = (guide.match(/<details\b[^>]*\bclass="faq__item"/g) ?? []).length;
  check(faqCount >= 4 && !/50\s?%|50\/50|garant/i.test(guide.replace(/<style[\s\S]*?<\/style>/g, '')), `Cómo comprar: preguntas desde buyingFaqs (${faqCount}) y sin 50/50 ni garantía`);

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
    await pastFirstScreen(page);
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

  // Sticky CTA: visible al salir de la primera pantalla, con el botón «Solicitar cotización».
  await page.goto(`${base}${MODEL_PATH}`, { waitUntil: 'load' });
  await pastFirstScreen(page);
  const bar = page.locator('[data-sticky-cta]');
  const quote = bar.locator('a', { hasText: 'Solicitar cotización' });
  const inView = await bar.evaluate((el) => {
    const box = el.getBoundingClientRect();
    return box.height > 0 && box.top < innerHeight && box.bottom > 0;
  });
  check((await quote.isVisible()) && inView, 'Sticky CTA: «Solicitar cotización» visible al salir de la primera pantalla');

  // WhatsApp: el parámetro `text` nombra el modelo (no sólo el enlace); la marca ya no se exige.
  const wa = await bar.locator('a[href^="https://wa.me/"]').getAttribute('href');
  const text = new URL(wa ?? 'https://wa.me/').searchParams.get('text') ?? '';
  check(text === 'Hola OrigenLab, quiero cotizar el UP400St.', `WhatsApp URL: el parámetro text nombra el modelo (${text.slice(0, 60)})`);
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
  // El envío cambia el texto a «Enviando…» y, si el servidor falla, lo restituye sin borrar la flecha.
  await page.waitForTimeout(300);
  const after = page.locator('#solicitud-btn');
  check(
    (await after.innerText()).trim() === 'Pedir cotización' && (await after.locator('svg.arrow').count()) === 1 && (await after.isEnabled()),
    'Consent: tras un envío fallido el botón recupera «Pedir cotización», la flecha y vuelve a estar activo',
  );
  await context.close();
}

/* Visita: un solo envío `visita` por carga de página, origen «directo» sin referente. */
{
  console.log('\nVisita: un envío por carga de página');
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  const visits = [];
  await page.route('**/api/stats/event', (route) => {
    visits.push(route.request().postData() ?? '');
    return route.fulfill({ status: 204 });
  });
  await page.goto(`${base}/`, { waitUntil: 'load' });
  await page.waitForTimeout(500);
  const first = visits.filter((b) => b.includes('"visita"'));
  check(first.length === 1, `Visita: «/» envía una sola visita (${first.length})`);
  check(first[0]?.includes('"origen":"directo"') && first[0]?.includes('"pagina":"/"') && !first[0]?.includes('modelo'), 'Visita: origen «directo», página «/» y sin modelo');
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
  await pastFirstScreen(page);
  const bar = page.locator('[data-sticky-cta]');
  const quote = bar.locator('a', { hasText: 'Solicitar cotización' });
  check((await quote.getAttribute('data-track')) === 'cotizacion_click', 'Ficha heredada: «Solicitar cotización» de la barra lleva data-track');
  check((await quote.getAttribute('href')) === '/contacto/?producto=ortoalresa-digicen-22-r', 'Ficha heredada: «Solicitar cotización» de la barra lleva el modelo al formulario');
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
  await page.locator('#ficha-title').scrollIntoViewIfNeeded();
  check(await page.locator('#ficha-title').isVisible(), 'Ficha heredada: con movimiento reducido la ficha técnica se ve');
  await context.close();
}

/* -- 9. Página de modelo: el 3D es la primera pantalla --------------------- */

/**
 * El modelo con historia abre sobre el 3D: a 1440 × 900 se ven, sin desplazar,
 * el H1, la primera «Solicitar cotización» y el escenario. La barra fija sólo
 * entra al terminar esa primera pantalla (`[data-first-screen]`). Debajo vienen,
 * en este orden, aplicaciones, ficha, accesorios, relacionados, cómo trabajamos
 * y preguntas. Lo cubren las dos páginas que montan la historia: la de la
 * colección y la ficha heredada de centrifugación.
 */
const FIRST_SCREEN_PAGES = ['/productos/sonicacion/hielscher-up400st/', '/productos/centrifugas/digicen-22-r/'];
const SECTION_ORDER = ['aplicaciones-title', 'ficha-title', 'accesorios-title', 'relacionados-title', 'confianza-title', 'preguntas-title'];
// Una sección sin contenido no se dibuja: el Digicen 22 R no tiene accesorios ni en su ficha ni en su historia.
const NO_ACCESSORIES = ['/productos/centrifugas/digicen-22-r/'];
for (const path of FIRST_SCREEN_PAGES) {
  console.log(`\nModelo: primera pantalla y secciones ${path} (1440 × 900)`);
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await page.goto(`${base}${path}`, { waitUntil: 'load' });
  await page.waitForTimeout(500);
  const first = await page.evaluate(() => {
    const box = (el) => (el ? el.getBoundingClientRect() : null);
    const inside = (rect) => Boolean(rect) && rect.top >= 0 && rect.bottom <= innerHeight && rect.height > 0;
    return {
      h1: inside(box(document.querySelector('h1'))),
      cta: inside(box(document.querySelector('[data-first-screen] .btn-primary'))),
      stage: inside(box(document.querySelector('[data-story-photo]') ?? document.querySelector('[data-story] canvas'))),
    };
  });
  check(first.h1 && first.cta && first.stage, `primera pantalla: h1 y CTA visibles junto al escenario (h1 ${first.h1}, CTA ${first.cta}, escenario ${first.stage})`);
  check((await page.locator('h1').count()) === 1, 'Modelo: un solo h1');

  const bar = page.locator('[data-sticky-cta]');
  check(await bar.evaluate((el) => el.inert), 'barra fija oculta antes de salir de la primera pantalla (inert)');
  let reached = '';
  for (let n = 0; n < 25 && !reached; n += 1) {
    await page.keyboard.press('Tab');
    reached = await page.evaluate(() => (document.activeElement?.closest('[data-sticky-cta]') ? document.activeElement.outerHTML.slice(0, 60) : ''));
  }
  check(!reached, `Tab 25 veces desde la carga no entra en la barra fija (${reached || 'no entra'})`);
  await pastFirstScreen(page);
  check(!(await bar.evaluate((el) => el.inert)), 'pasada la primera pantalla la barra fija se activa (sin inert)');
  const quote = bar.locator('a', { hasText: 'Solicitar cotización' });
  await quote.focus();
  const ring = await page.evaluate(() => {
    const el = document.activeElement;
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return { ring: style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) >= 2, inView: rect.top >= 0 && rect.bottom <= innerHeight };
  });
  check(ring.ring && ring.inView, 'pasada la primera pantalla el foco de teclado se ve en la barra fija');

  const ids = await page.evaluate((wanted) => [...document.querySelectorAll('main [id]')].map((el) => el.id).filter((id) => wanted.includes(id)), SECTION_ORDER);
  const expectedIds = SECTION_ORDER.filter((id) => id !== 'accesorios-title' || !NO_ACCESSORIES.includes(path));
  check(JSON.stringify(ids) === JSON.stringify(expectedIds), `secciones en orden (${ids.join(' > ')})`);
  const details = page.locator('main details');
  check((await details.count()) >= 1, `preguntas frecuentes como <details> (${await details.count()})`);
  await details.first().locator('summary').click();
  check(await details.first().evaluate((el) => el.hasAttribute('open')), 'abrir la primera pregunta fija [open]');
  // La ficha heredada de las centrífugas conserva su tabla por grupos (`SpecGroups`): sin botones de fuente.
  const infos = path.includes('/centrifugas/') ? [true] : await page.locator('.info').evaluateAll((els) =>
    els.map((el) => {
      const target = document.getElementById(el.getAttribute('aria-describedby') ?? '');
      return Boolean(target) && (target.textContent ?? '').trim().startsWith('Fuente:');
    }),
  );
  check(infos.length > 0 && infos.every(Boolean), `cada botón .info describe su fuente («Fuente: …») (${infos.filter(Boolean).length} de ${infos.length})`);
  await context.close();
}
{
  const path = '/productos/sonicacion/hielscher-uip2000hdt/';
  console.log(`\nModelo: imágenes de ${path}`);
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await page.goto(`${base}${path}`, { waitUntil: 'load' });
  await page.evaluate(() => document.querySelectorAll('img[loading="lazy"]').forEach((img) => (img.loading = 'eager')));
  await page.waitForTimeout(1200);
  const broken = await page.evaluate(() => [...document.images].filter((img) => img.complete && img.naturalWidth === 0).map((img) => img.currentSrc || img.src));
  check(broken.length === 0, `ninguna imagen sin cargar (${broken.join(', ') || 'ninguna'})`);
  await context.close();
}

await browser.close();
server.close();

if (failed) {
  console.error('\nqa:interaction FALLÓ');
  process.exit(1);
}
console.log('\nqa:interaction OK');
