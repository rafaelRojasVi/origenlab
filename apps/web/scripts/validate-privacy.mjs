#!/usr/bin/env node
/**
 * Coherencia entre lo que el sitio afirma sobre privacidad y lo que hace.
 *
 * `validate-dist.mjs` comprueba la estructura del sitio construido. Este script
 * comprueba una cosa más estrecha y más fácil de romper sin darse cuenta: que
 * las afirmaciones de `/privacidad/` y `/cookies/` sigan siendo verdad en el
 * JavaScript y en el HTML que se despliegan.
 *
 * El fallo que existe para atrapar no es un error de programación, es un error
 * de redacción que sobrevive a un cambio de código: alguien añade una
 * preferencia recordada, o un tercero, y las dos páginas legales se quedan
 * diciendo lo de siempre. Una afirmación sobre privacidad que nadie comprueba
 * envejece hasta volverse falsa.
 *
 * Ejecutar: node scripts/validate-privacy.mjs [carpeta]
 */
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const target = process.argv[2] ?? 'dist';
const dist = join(root, target);
let failed = false;

function assert(condition, message) {
  console.log(`${condition ? '  ok' : '   x'} ${message}`);
  if (!condition) failed = true;
}

if (!existsSync(dist)) {
  console.error(`${target}/ no existe: ejecute npm run build primero`);
  process.exit(1);
}

function walk(dir, ext, acc = []) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) walk(full, ext, acc);
    else if (entry.name.endsWith(ext)) acc.push(full);
  }
  return acc;
}

const pages = walk(dist, '.html').filter((file) => !relative(dist, file).startsWith('email/'));
const scripts = walk(dist, '.js');

/* -- 1. Almacenamiento del navegador --------------------------------------
 *
 * La afirmación publicada es deliberadamente acotada: **la aplicación de
 * OrigenLab** no fija cookies ni almacenamiento. Lo que haga la infraestructura
 * por delante del origen no se promete en ninguna parte, y por eso aquí sólo se
 * comprueba el código propio, que es lo único que el repositorio controla.
 */
const STORAGE_WRITES = [
  { pattern: /document\s*\.\s*cookie\s*=/, label: 'document.cookie' },
  { pattern: /localStorage\s*\.\s*setItem\b/, label: 'localStorage.setItem' },
  { pattern: /sessionStorage\s*\.\s*setItem\b/, label: 'sessionStorage.setItem' },
  { pattern: /indexedDB\s*\.\s*open\b/, label: 'indexedDB.open' },
  { pattern: /navigator\s*\.\s*storage\b/, label: 'navigator.storage' },
];

const sources = [
  ...scripts.map((file) => ({ rel: relative(dist, file), body: readFileSync(file, 'utf8') })),
  ...pages.map((file) => ({ rel: relative(dist, file), body: readFileSync(file, 'utf8') })),
];

for (const { pattern, label } of STORAGE_WRITES) {
  const offenders = sources.filter((source) => pattern.test(source.body)).map((s) => s.rel);
  assert(
    offenders.length === 0,
    `sin escritura de ${label}${offenders.length ? ` (aparece en ${offenders.join(', ')})` : ''}`,
  );
}

/* -- 2. La página de cookies dice lo que corresponde ---------------------- */
const cookiesPage = pages.find((file) => relative(dist, file) === 'cookies/index.html');
assert(cookiesPage !== undefined, 'existe la página /cookies/');
if (cookiesPage) {
  const html = readFileSync(cookiesPage, 'utf8');
  assert(
    html.includes('La aplicación de OrigenLab no fija cookies ni almacenamiento del navegador'),
    'la página de cookies acota su afirmación a la aplicación de OrigenLab',
  );
  assert(
    /Cloudflare/.test(html) && /cookies técnicas o de seguridad/.test(html),
    'la página de cookies nombra lo que puede emitir la infraestructura sin prometer su ausencia',
  );
  assert(
    !/no fija ninguna cookie\b(?!.*aplicación)/.test(html.replace(/\s+/g, ' ')),
    'la página de cookies no promete la ausencia absoluta de cookies',
  );
}

/* -- 3. Ningún banner de consentimiento decorativo ------------------------ */
for (const file of pages) {
  const html = readFileSync(file, 'utf8');
  const rel = relative(dist, file);
  assert(
    !/(aceptar todas|acepto todas|rechazar todas|gestionar cookies)/i.test(html),
    `${rel}: sin banner de consentimiento`,
  );
}

/* -- 4. Ningún formulario envía fuera del dominio -------------------------
 *
 * Hoy el sitio no tiene ninguno y `/privacidad/` lo afirma. El bucle existe
 * para el día que lo tenga: un formulario que envíe a otro dominio convierte
 * esa página en una declaración falsa sin que nadie toque su texto.
 */
for (const file of pages) {
  const html = readFileSync(file, 'utf8');
  const rel = relative(dist, file);
  for (const tag of html.matchAll(/<form\b[^>]*>/g)) {
    const action = tag[0].match(/action="([^"]*)"/)?.[1] ?? '';
    assert(
      action.startsWith('/') && !action.startsWith('//'),
      `${rel}: el formulario envía a una ruta del propio dominio (${action || 'sin action'})`,
    );
  }
}

/* -- 5. Ninguna petición a otro dominio desde el JavaScript --------------- */
for (const file of scripts) {
  const body = readFileSync(file, 'utf8');
  const rel = relative(dist, file);
  const remote = [...body.matchAll(/["'`](https?:)?\/\/([a-z0-9.-]+)/gi)]
    .map((match) => match[2])
    .filter((host) => host && !host.endsWith('origenlab.cl'));
  assert(remote.length === 0, `${rel}: sin destino remoto${remote.length ? ` (${[...new Set(remote)].join(', ')})` : ''}`);
}

if (failed) {
  console.error('\nvalidate:privacy FALLÓ');
  process.exit(1);
}
console.log('\nvalidate:privacy OK');
