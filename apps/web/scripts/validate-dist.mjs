#!/usr/bin/env node
/**
 * Invariantes del sitio construido (`dist/`).
 *
 * Comprueba sobre el HTML real lo que no puede verificarse leyendo el código
 * fuente: que no se escape ninguna petición a terceros, que la jerarquía de
 * encabezados sea válida, que toda imagen declare dimensiones y que el sitemap
 * contenga exactamente las rutas públicas.
 */
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const dist = join(root, 'dist');
let failed = false;

function assert(condition, message) {
  if (!condition) {
    console.error(`  x ${message}`);
    failed = true;
  }
}

if (!existsSync(dist)) {
  console.error('dist/ no existe: ejecute npm run build primero');
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

/** Páginas del sitio público (excluye las fuentes HTML de la firma de correo). */
const pages = walk(dist, '.html').filter((file) => !relative(dist, file).startsWith('email/'));
assert(pages.length >= 20, `dist: se esperaban al menos 20 páginas, hay ${pages.length}`);

/**
 * Superficies que no se indexan: la página de trabajo de marca, el 404 y las
 * tres rutas legales. Las rutas legales salen de esta lista el día que
 * `legal.ts` registre una revisión profesional; hasta entonces se sirven con
 * noindex, fuera del sitemap y con Disallow en robots.txt.
 */
const INTERNAL = [
  'logo-lab/index.html',
  '404.html',
  'privacidad/index.html',
  'cookies/index.html',
  'aviso-legal/index.html',
];

/**
 * Rutas cuyo `noindex` se sostiene en cuatro capas a la vez: la etiqueta meta,
 * la ausencia del sitemap, el `Disallow` de robots.txt y el `X-Robots-Tag` de
 * .htaccess. Comprobarlas juntas evita el fallo real de este sitio, que es
 * abrir una capa y olvidar las otras tres.
 */
const SUPPRESSED_ROUTES = ['/privacidad/', '/cookies/', '/aviso-legal/'];

/**
 * Afirmaciones de privacidad cuya verdad depende del HTML construido. La
 * redacción literal vive en `src/data/legal.ts` (`SITE_CLAIMS`), y aquí se
 * comprueba que siga siendo cierta: un sitio con formulario que afirme no
 * tener ninguno es una declaración falsa, no una errata.
 */
const NO_FORMS_CLAIM = 'El sitio no tiene formularios ni recibe envíos';
const NO_APP_STORAGE_CLAIM = 'La aplicación de OrigenLab no fija cookies ni almacenamiento del navegador';

/**
 * Afirmaciones sin aprobar: su redacción literal no puede aparecer en ninguna
 * página construida. Es la comprobación de extremo a extremo del registro de
 * `src/data/claims.ts`; la forma de cada registro la comprueba
 * `validate:catalog`.
 */
const claimsSrc = readFileSync(join(root, 'src/data/claims.ts'), 'utf8');
const withheldTexts = claimsSrc
  .split(/\n  \{\n/)
  .slice(1)
  .filter((block) => !/status: 'approved'/.test(block))
  .map((block) => ({
    id: block.match(/id: '([^']+)'/)?.[1] ?? '(sin id)',
    text: block.match(/text: '([^']*)'/)?.[1] ?? '',
  }))
  .filter((claim) => claim.text.length > 0);
assert(withheldTexts.length > 0, 'claims.ts: se esperaba al menos una afirmación redactada sin aprobar');

for (const file of pages) {
  const rel = relative(dist, file);
  const html = readFileSync(file, 'utf8');
  const at = (message) => `${rel}: ${message}`;

  /* -- Terceros ---------------------------------------------------------- */
  for (const match of html.matchAll(/<script[^>]+src="([^"]+)"/g)) {
    assert(!/^(https?:)?\/\//.test(match[1]), at(`script externo ${match[1]}`));
  }
  for (const match of html.matchAll(/<link[^>]+rel="stylesheet"[^>]+href="([^"]+)"/g)) {
    assert(!/^(https?:)?\/\//.test(match[1]), at(`hoja de estilo externa ${match[1]}`));
  }
  // Sólo sobre el marcado: el rastro de un tercero vive en un atributo, no en
  // una frase. La política de privacidad nombra a Tidio y a las tipografías de
  // Google justamente para dejar constancia de que se retiraron.
  const markup = (html.match(/<[^>]+>/g) ?? []).join(' ');
  assert(
    !/tidio|googletagmanager|google-analytics|fonts\.googleapis|fonts\.gstatic|hotjar|facebook\.net/i.test(markup),
    at('rastro de un tercero'),
  );
  assert(!html.includes('name="generator"'), at('expone la versión del generador'));

  /* -- Cabecera ---------------------------------------------------------- */
  assert(/<html lang="es-CL">/.test(html), at('falta lang="es-CL"'));
  assert(/<link rel="canonical" href="https:\/\/origenlab\.cl\/[^"]*"/.test(html), at('canónica ausente o mal formada'));
  const canonical = html.match(/<link rel="canonical" href="([^"]+)"/)?.[1];
  assert(canonical?.endsWith('/'), at(`la canónica debe llevar barra final: ${canonical}`));
  assert(/<meta name="theme-color"/.test(html), at('falta theme-color'));
  assert(/property="og:image" content="https:\/\/origenlab\.cl\/og\/origenlab-og\.png"/.test(html), at('og:image debe ser el PNG absoluto'));
  const description = html.match(/<meta name="description" content="([^"]*)"/)?.[1] ?? '';
  assert(description.length > 50, at('descripción demasiado corta'));
  assert(description.length <= 165, at(`descripción de ${description.length} caracteres (máx. 165)`));

  const title = html.match(/<title>([^<]*)<\/title>/)?.[1] ?? '';
  assert(title.length > 0 && title.length <= 70, at(`título de ${title.length} caracteres`));
  assert(!/[—–]/.test(title), at('el título usa raya o semirraya; el separador es "|"'));

  /* -- Estructura -------------------------------------------------------- */
  const headings = [...html.matchAll(/<h([1-6])[^>]*>/g)].map((match) => Number(match[1]));
  assert(headings.filter((level) => level === 1).length === 1, at(`h1 x${headings.filter((l) => l === 1).length}`));
  let previous = 0;
  for (const level of headings) {
    assert(!previous || level <= previous + 1, at(`salto de encabezado h${previous} -> h${level}`));
    previous = level;
  }
  assert(html.includes('id="contenido"'), at('falta el destino del enlace de salto'));

  /* -- Imágenes ---------------------------------------------------------- */
  for (const match of html.matchAll(/<img\b[^>]*>/g)) {
    const tag = match[0];
    assert(/\bwidth=/.test(tag) && /\bheight=/.test(tag), at(`img sin dimensiones: ${tag.slice(0, 90)}`));
    assert(/\balt=/.test(tag), at(`img sin alt: ${tag.slice(0, 90)}`));
  }

  /* -- Barra final: la canónica y el sitemap la llevan; los enlaces también -- */
  for (const match of html.matchAll(/<a\b[^>]*href="(\/[^"#?]*)"/g)) {
    const href = match[1];
    const isAsset = /\.[a-z0-9]{2,5}$/i.test(href);
    assert(isAsset || href.endsWith('/'), at(`enlace interno sin barra final: ${href}`));
  }

  /* -- Nuevas pestañas y enlaces externos -------------------------------- */
  for (const match of html.matchAll(/<a\b[^>]*target="_blank"[^>]*>/g)) {
    assert(/rel="[^"]*noopener/.test(match[0]), at(`target="_blank" sin noopener: ${match[0].slice(0, 80)}`));
  }

  /* -- Tipografía de la copia ------------------------------------------- */
  const visible = stripElements(stripElements(html, 'script'), 'style').replace(/<[^>]+>/g, ' ');
  assert(!visible.includes('—'), at('raya (em dash) en la copia visible'));
  assert(!/\s–\s/.test(visible), at('semirraya usada como separador en la copia visible'));
  assert(!visible.includes('...'), at('tres puntos en vez de puntos suspensivos'));

  /* -- Afirmaciones sin aprobar ------------------------------------------ */
  const flat = visible.replace(/\s+/g, ' ');
  for (const claim of withheldTexts) {
    assert(
      !flat.includes(claim.text),
      at(`publica la afirmación sin aprobar "${claim.text}" (claims.ts: ${claim.id})`),
    );
  }

  /* -- Superficies internas --------------------------------------------- */
  if (INTERNAL.includes(rel)) {
    assert(/<meta name="robots" content="noindex/.test(html), at('superficie interna sin noindex'));
  } else {
    assert(!/<meta name="robots" content="noindex/.test(html), at('página pública marcada como noindex'));
  }
}


/**
 * Elimina todos los elementos `<name>…</name>` sin distinguir mayúsculas y
 * repite hasta que no quede ninguno, de modo que un cierre anidado o partido
 * no deje un fragmento de etiqueta en el texto que se inspecciona después.
 */
function stripElements(html, name) {
  const pattern = new RegExp(`<${name}\\b[\\s\\S]*?<\\/${name}\\s*>`, 'gi');
  let previous;
  let current = html;
  do {
    previous = current;
    current = current.replace(pattern, '');
  } while (current !== previous);
  return current;
}

/* -- Coherencia entre lo que el sitio hace y lo que dice ------------------
 *
 * `/privacidad/` afirma dos cosas comprobables sobre el sitio construido: que
 * no tiene formularios y que la aplicación no fija cookies ni almacenamiento.
 * Las dos se comprueban aquí en las dos direcciones, porque el fallo que
 * importa no es un error de programación sino una redacción que sobrevive a un
 * cambio de código.
 */
const allHtml = pages.map((file) => readFileSync(file, 'utf8'));
const formPages = pages.filter((_file, i) => /<form\b/.test(allHtml[i])).map((f) => relative(dist, f));
const claimsNoForms = allHtml.some((html) => html.includes(NO_FORMS_CLAIM));

assert(
  formPages.length === 0 || !claimsNoForms,
  `coherencia: el sitio tiene formulario en ${formPages.join(', ')} y sigue afirmando "${NO_FORMS_CLAIM}"`,
);
assert(
  formPages.length > 0 || claimsNoForms,
  `coherencia: el sitio no tiene formularios y la política ya no lo afirma. Redacción esperada: "${NO_FORMS_CLAIM}"`,
);
assert(
  allHtml.some((html) => html.includes(NO_APP_STORAGE_CLAIM)),
  `coherencia: falta la afirmación acotada sobre almacenamiento: "${NO_APP_STORAGE_CLAIM}"`,
);

/* -- Sitemap y robots ----------------------------------------------------- */

const sitemapIndex = join(dist, 'sitemap-index.xml');
assert(existsSync(sitemapIndex), 'dist: falta sitemap-index.xml');
const sitemapUrls = walk(dist, '.xml')
  .filter((file) => /sitemap-\d+\.xml$/.test(file))
  .flatMap((file) => [...readFileSync(file, 'utf8').matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => m[1]));

assert(sitemapUrls.length > 0, 'sitemap: sin URLs');
assert(!sitemapUrls.some((url) => url.includes('/logo-lab')), 'sitemap: contiene la página interna /logo-lab/');
assert(!sitemapUrls.some((url) => url.includes('/404')), 'sitemap: contiene la página 404');

const publicRoutes = pages
  .map((file) => relative(dist, file))
  .filter((rel) => !INTERNAL.includes(rel))
  .map((rel) => `https://origenlab.cl/${rel.replace(/index\.html$/, '')}`);
for (const route of publicRoutes) {
  assert(sitemapUrls.includes(route), `sitemap: falta la ruta pública ${route}`);
}
assert(
  sitemapUrls.length === publicRoutes.length,
  `sitemap: ${sitemapUrls.length} URLs frente a ${publicRoutes.length} rutas públicas`,
);

const robots = readFileSync(join(dist, 'robots.txt'), 'utf8');
assert(robots.includes('Sitemap: https://origenlab.cl/sitemap-index.xml'), 'robots.txt: sitemap mal referenciado');
assert(robots.includes('Disallow: /logo-lab/'), 'robots.txt: /logo-lab/ debe quedar fuera del índice');
assert(robots.includes('Disallow: /email/'), 'robots.txt: /email/ debe quedar fuera del índice');
for (const route of SUPPRESSED_ROUTES) {
  assert(robots.includes(`Disallow: ${route}`), `robots.txt: ${route} debe quedar fuera del índice`);
  assert(!sitemapUrls.some((url) => url.includes(route)), `sitemap: contiene la ruta suprimida ${route}`);
}

const htaccess = readFileSync(join(dist, '.htaccess'), 'utf8');
assert(htaccess.includes('ErrorDocument 404 /404.html'), '.htaccess: falta ErrorDocument');
assert(/RewriteCond %\{HTTP_HOST\} \^www\\\./.test(htaccess), '.htaccess: falta la redirección de www al dominio raíz');
assert(htaccess.includes('Content-Security-Policy'), '.htaccess: falta la CSP');
const robotsTagRule = htaccess.match(/<If "%\{REQUEST_URI\} =~ m#\^\/\(([^)]*)\)/)?.[1] ?? '';
assert(robotsTagRule.length > 0, '.htaccess: no se encontró la regla de X-Robots-Tag');
for (const route of SUPPRESSED_ROUTES) {
  const segment = route.replace(/\//g, '');
  assert(
    robotsTagRule.split('|').includes(segment),
    `.htaccess: ${route} necesita X-Robots-Tag noindex`,
  );
}

/* -- Peso ----------------------------------------------------------------- */

const js = walk(dist, '.js').reduce((total, file) => total + statSync(file).size, 0);
const css = walk(dist, '.css').reduce((total, file) => total + statSync(file).size, 0);
assert(js < 15_000, `dist: ${Math.round(js / 1024)} kB de JavaScript (presupuesto 15 kB)`);
assert(css < 60_000, `dist: ${Math.round(css / 1024)} kB de CSS (presupuesto 60 kB)`);

if (failed) {
  console.error('\nvalidate:dist FALLÓ');
  process.exit(1);
}
console.log(`validate:dist OK (${pages.length} páginas, ${Math.round(js / 1024)} kB JS, ${Math.round(css / 1024)} kB CSS)`);
