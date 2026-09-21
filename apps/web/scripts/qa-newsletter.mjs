#!/usr/bin/env node
/**
 * QA del formulario del boletín sobre la compilación de vista previa.
 *
 * Se ejecuta contra `dist-preview/`, que es la única compilación en la que el
 * formulario existe. El endpoint no está desplegado, así que todas las
 * respuestas se simulan interceptando la petición en el navegador: eso permite
 * probar justo lo que más importa y lo que en producción nadie querría
 * provocar a propósito, que es el fallo.
 *
 * La comprobación central es la última: **un backend caído no puede pintar un
 * éxito**. Se prueban un 500, una respuesta 200 con un cuerpo que no confirma
 * nada, una respuesta que no es JSON y una caída de red, y en los cuatro casos
 * el formulario tiene que decir que no se guardó nada.
 *
 * Ejecutar: npm run build:preview && node scripts/qa-newsletter.mjs
 */
import { createServer } from 'node:http';
import { createReadStream, existsSync, statSync } from 'node:fs';
import { dirname, extname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const dist = join(root, 'dist-preview');
let failed = false;

function check(condition, message) {
  console.log(`${condition ? '  ok' : '   x'} ${message}`);
  if (!condition) failed = true;
}

if (!existsSync(dist)) {
  console.error('dist-preview/ no existe: ejecute npm run build:preview primero');
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

const SUCCESS_MARK = 'Solicitud recibida';
const FAILURE_MARK = 'No pudimos registrar su solicitud';

/** Abre /newsletter/ con el endpoint interceptado por el manejador dado. */
async function withForm(handler, viewport = { width: 1280, height: 900 }) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  const calls = [];
  await page.route('**/api/newsletter/subscribe', async (route) => {
    calls.push(route.request().postData() ?? '');
    await handler(route);
  });
  await page.goto(`${base}/newsletter/`, { waitUntil: 'load' });
  return { context, page, calls };
}

const ok = (route) =>
  route.fulfill({ status: 200, contentType: 'application/json', body: '{"status":"pending"}' });

/* -- 1. Estructura y accesibilidad ---------------------------------------- */
console.log('\nEstructura y accesibilidad');
{
  const { context, page } = await withForm(ok);

  check(await page.locator('form[data-newsletter-form]').count() === 1, 'hay un formulario del boletín');

  const consent = page.locator('input[name="consentimiento"]');
  check(!(await consent.isChecked()), 'la casilla de consentimiento no viene marcada');
  check(await consent.evaluate((el) => el.required), 'la casilla de consentimiento es obligatoria');
  check(
    await page.locator('.nl__consent a[href="/privacidad/"]').count() > 0,
    'el texto de consentimiento enlaza la política de privacidad',
  );

  /* Toda entrada visible tiene etiqueta asociada. */
  const unlabelled = await page.evaluate(() => {
    const form = document.querySelector('[data-newsletter-form]');
    if (!form) return ['sin formulario'];
    return [...form.querySelectorAll('input')]
      .filter((input) => input.type !== 'hidden' && input.tabIndex !== -1)
      .filter((input) => {
        const byFor = input.id && document.querySelector(`label[for="${input.id}"]`);
        return !byFor && !input.closest('label');
      })
      .map((input) => input.name);
  });
  check(unlabelled.length === 0, `toda entrada tiene etiqueta (${unlabelled.join(', ') || 'ninguna suelta'})`);

  const decoy = page.locator('input[name="sitio_web"]');
  check(await decoy.count() === 1, 'existe el campo señuelo');
  /*
   * El señuelo no puede esconderse con `display: none`: demasiados envíos
   * automáticos lo detectan y lo saltan. Se recorta a un píxel fuera de la
   * vista, así que `isVisible()` de Playwright lo da por visible aunque nadie
   * pueda verlo ni pulsarlo. Lo que se comprueba es lo que importa: que no
   * ocupe superficie utilizable.
   */
  const decoyBox = (await page.locator('.nl__decoy').boundingBox()) ?? { width: 0, height: 0 };
  check(
    decoyBox.width <= 1 && decoyBox.height <= 1,
    `el contenedor del señuelo no ocupa superficie (${decoyBox.width} x ${decoyBox.height})`,
  );
  /* Y en el punto donde estaría el campo no hay nada del señuelo que pulsar. */
  const decoyReachable = await page.evaluate(() => {
    const input = document.querySelector('input[name="sitio_web"]');
    if (!input) return true;
    const box = input.getBoundingClientRect();
    const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
    return hit === input;
  });
  check(!decoyReachable, 'el señuelo no se puede pulsar con el cursor');
  check(await decoy.evaluate((el) => el.tabIndex === -1), 'el señuelo está fuera del orden de tabulación');
  check(
    await page.locator('[data-newsletter-status]').getAttribute('role') === 'status',
    'la región de estado se anuncia con role status',
  );
  check(
    (await page.locator('[data-newsletter-status]').textContent())?.trim() === '',
    'la región de estado está vacía en reposo',
  );

  /* Objetivo táctil de 44 px en las casillas. */
  const smallTargets = await page.evaluate(() =>
    [...document.querySelectorAll('.nl__check')]
      .map((el) => el.getBoundingClientRect().height)
      .filter((height) => height < 44).length,
  );
  check(smallTargets === 0, 'las casillas cumplen el objetivo táctil de 44 px');

  await context.close();
}

/* -- 2. Consentimiento obligatorio ---------------------------------------- */
console.log('\nConsentimiento');
{
  const { context, page, calls } = await withForm(ok);
  await page.fill('#nl-email', 'lab@ejemplo.cl');
  await page.click('[data-newsletter-submit]');
  await page.waitForTimeout(150);

  check(calls.length === 0, 'sin consentimiento no se envía nada al servidor');
  const error = page.locator('[data-error-for="consentimiento"]');
  check(await error.isVisible(), 'se muestra el error de consentimiento');
  check(
    !(await page.locator('[data-newsletter-status]').textContent())?.includes(SUCCESS_MARK),
    'no aparece ningún mensaje de éxito',
  );
  check(
    await page.evaluate(() => document.activeElement?.getAttribute('name') === 'consentimiento'),
    'el foco va a la casilla de consentimiento',
  );
  await context.close();
}

/* -- 3. Validación del correo --------------------------------------------- */
console.log('\nValidación');
{
  const { context, page, calls } = await withForm(ok);
  await page.check('input[name="consentimiento"]');
  await page.click('[data-newsletter-submit]');
  await page.waitForTimeout(150);
  check(calls.length === 0, 'sin correo no se envía nada');
  check(
    await page.locator('#nl-email').getAttribute('aria-invalid') === 'true',
    'el campo de correo queda marcado como inválido',
  );
  check(
    await page.evaluate(() => document.activeElement?.id === 'nl-email'),
    'el foco va al primer campo inválido',
  );

  await page.fill('#nl-email', 'no-es-un-correo');
  await page.click('[data-newsletter-submit]');
  await page.waitForTimeout(150);
  check(calls.length === 0, 'un correo mal formado tampoco se envía');
  check(
    ((await page.locator('[data-error-for="email"]').textContent()) ?? '').length > 0,
    'el error del correo explica qué revisar',
  );

  await page.fill('#nl-email', 'lab@ejemplo.cl');
  await page.click('[data-newsletter-submit]');
  await page.waitForTimeout(250);
  check(calls.length === 1, 'con correo válido y consentimiento sí se envía');
  check(
    await page.locator('#nl-email').getAttribute('aria-invalid') === null,
    'el error anterior se limpia al corregir',
  );
  await context.close();
}

/* -- 4. Envío correcto ----------------------------------------------------- */
console.log('\nEnvío correcto');
{
  const { context, page, calls } = await withForm(ok);
  await page.fill('#nl-email', 'lab@ejemplo.cl');
  await page.fill('#nl-nombre', 'Persona de prueba');
  await page.check('input[name="intereses"][value="centrifugacion"]');
  await page.check('input[name="consentimiento"]');
  await page.click('[data-newsletter-submit]');
  await page.waitForFunction(
    (mark) => document.querySelector('[data-newsletter-status]')?.textContent?.includes(mark),
    SUCCESS_MARK,
    { timeout: 3000 },
  );

  const body = calls[0] ?? '';
  check(body.includes('email=lab%40ejemplo.cl'), 'el correo viaja en el cuerpo');
  check(body.includes('consentimiento=si'), 'el consentimiento viaja en el cuerpo');
  check(body.includes('consent_version='), 'la versión del texto aceptado viaja en el cuerpo');
  check(body.includes('intereses=centrifugacion'), 'el interés marcado viaja en el cuerpo');
  check(body.includes('rendered_at='), 'la marca de tiempo del formulario viaja en el cuerpo');
  check(
    await page.locator('[data-newsletter-status]').getAttribute('data-state') === 'ok',
    'la región de estado queda en éxito',
  );
  check(await page.inputValue('#nl-email') === '', 'el formulario se limpia tras el éxito');
  await context.close();
}

/* -- 5. Ningún fallo puede pintar un éxito -------------------------------- */
console.log('\nFallos: ninguno muestra éxito');
const FAILURES = [
  {
    label: 'error 500 del servidor',
    handler: (route) => route.fulfill({ status: 500, contentType: 'text/plain', body: 'boom' }),
  },
  {
    label: 'respuesta 200 que no confirma nada',
    handler: (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '{"status":"nope"}' }),
  },
  {
    label: 'respuesta 200 que no es JSON',
    handler: (route) => route.fulfill({ status: 200, contentType: 'text/html', body: '<h1>ok</h1>' }),
  },
  {
    label: 'endpoint ausente (404)',
    handler: (route) => route.fulfill({ status: 404, contentType: 'text/plain', body: 'no' }),
  },
  { label: 'caída de red', handler: (route) => route.abort('failed') },
];

for (const failure of FAILURES) {
  const { context, page } = await withForm(failure.handler);
  await page.fill('#nl-email', 'lab@ejemplo.cl');
  await page.check('input[name="consentimiento"]');
  await page.click('[data-newsletter-submit]');
  await page.waitForFunction(
    () => document.querySelector('[data-newsletter-status]')?.dataset.state === 'error',
    null,
    { timeout: 5000 },
  ).catch(() => {});

  const status = (await page.locator('[data-newsletter-status]').textContent()) ?? '';
  check(status.includes(FAILURE_MARK), `${failure.label}: se muestra el fallo`);
  check(!status.includes(SUCCESS_MARK), `${failure.label}: no se muestra ningún éxito`);
  check(status.includes('No se ha guardado nada'), `${failure.label}: se dice que no se guardó nada`);
  check(
    await page.inputValue('#nl-email') === 'lab@ejemplo.cl',
    `${failure.label}: el formulario conserva lo escrito`,
  );
  await context.close();
}

/* -- 6. Teclado y móvil ---------------------------------------------------- */
console.log('\nTeclado y móvil (375 px)');
{
  const { context, page } = await withForm(ok, { width: 375, height: 812 });
  await page.focus('#nl-email');
  const reachable = await page.evaluate(() => {
    const order = [];
    return new Promise((resolve) => {
      const form = document.querySelector('[data-newsletter-form]');
      const focusable = form.querySelectorAll(
        'input:not([type=hidden]):not([tabindex="-1"]), button',
      );
      for (const el of focusable) order.push(el.name || el.type);
      resolve(order);
    });
  });
  check(reachable.includes('consentimiento'), 'la casilla de consentimiento es alcanzable con teclado');
  check(reachable.at(-1) === 'submit', 'el botón de envío es el último elemento enfocable');
  check(!reachable.includes('sitio_web'), 'el señuelo no aparece en el recorrido de teclado');

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  check(!overflow, 'a 375 px no hay desbordamiento horizontal');
  await context.close();
}

await browser.close();
server.close();

if (failed) {
  console.error('\nqa:newsletter FALLÓ');
  process.exit(1);
}
console.log('\nqa:newsletter OK');
