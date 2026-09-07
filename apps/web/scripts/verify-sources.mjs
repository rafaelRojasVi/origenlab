#!/usr/bin/env node
/**
 * Comprueba que todo enlace externo que el sitio publica sigue respondiendo.
 *
 * Cubre las tres listas de destinos externos que existen:
 *   - `src/data/sourceRegistry.ts`  página oficial y PDF por marca
 *   - `src/data/brands.ts`          sitio del fabricante
 *   - `src/data/products.ts`        página de producto y ficha PDF por modelo
 *
 * **No** forma parte de `npm run validate`. Sale a la red, y una puerta de
 * calidad que depende de que seis servidores ajenos estén levantados falla por
 * motivos que no tienen nada que ver con el cambio que se está validando. Se
 * ejecuta a mano y su resultado se anota en `lastVerified` del registro.
 *
 * Un PDF tiene que responder además con `content-type` de PDF: varios sitios
 * devuelven 200 con una página de error cuando el archivo ya no existe.
 *
 * Ejecutar: npm run verify:sources
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');

/** Algunos fabricantes rechazan clientes sin navegador declarado. */
const UA =
  'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36';
const TIMEOUT_MS = 30_000;

function read(rel) {
  return readFileSync(join(root, rel), 'utf8');
}

const registrySrc = read('src/data/sourceRegistry.ts');
const brandsSrc = read('src/data/brands.ts');
const productsSrc = read('src/data/products.ts');

/** Destinos: [etiqueta, url, tipo esperado]. */
const targets = [];

for (const block of registrySrc.split(/\n  \{\n/).slice(1)) {
  const id = block.match(/brandId: '([^']+)'/)?.[1];
  if (!id) continue;
  const official = block.match(/officialUrl:\s*\n?\s*'([^']+)'/)?.[1];
  const pdf = block.match(/officialPdfUrl:\s*\n?\s*'([^']+)'/)?.[1];
  if (official) targets.push([`${id} · página oficial`, official, 'html']);
  if (pdf) targets.push([`${id} · PDF oficial`, pdf, 'pdf']);
}

for (const block of brandsSrc.split(/\n  \{\n/).slice(1)) {
  const id = block.match(/id: '([^']+)'/)?.[1];
  const url = block.match(/websiteUrl: '([^']+)'/)?.[1];
  if (id && url) targets.push([`${id} · sitio`, url, 'html']);
}

for (const [, label, url] of [
  ...[...productsSrc.matchAll(/slug: '([^']+)'/g)].map((m) => m[1]),
].flatMap((slug) => {
  const index = productsSrc.indexOf(`slug: '${slug}'`);
  const block = productsSrc.slice(
    productsSrc.lastIndexOf('\n  {', index),
    productsSrc.indexOf('\n  },', index),
  );
  const out = [];
  const manufacturer = block.match(/manufacturerUrl: '([^']+)'/)?.[1];
  const datasheet = block.match(/datasheetUrl: '([^']+)'/)?.[1];
  if (manufacturer) out.push([slug, `${slug} · página de producto`, manufacturer, 'html']);
  if (datasheet) out.push([slug, `${slug} · ficha PDF`, datasheet, 'pdf']);
  return out;
})) {
  targets.push([label, url, url.endsWith('.pdf') ? 'pdf' : 'html']);
}

/** Sin duplicados: varios modelos comparten el PDF de serie. */
const seen = new Set();
const unique = targets.filter(([, url]) => {
  if (seen.has(url)) return false;
  seen.add(url);
  return true;
});

async function check(url) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const response = await fetch(url, {
      redirect: 'follow',
      signal: controller.signal,
      headers: { 'user-agent': UA, accept: '*/*' },
    });
    return {
      ok: response.ok,
      status: response.status,
      type: response.headers.get('content-type') ?? '',
    };
  } catch (error) {
    return { ok: false, status: 0, type: '', error: String(error.message ?? error) };
  } finally {
    clearTimeout(timer);
  }
}

let failures = 0;
let warnings = 0;

console.log(`Comprobando ${unique.length} destinos externos\n`);

for (const [label, url, kind] of unique) {
  const result = await check(url);
  const isPdf = result.type.includes('pdf');
  let verdict;

  if (result.ok && kind === 'pdf' && !isPdf) {
    verdict = `AVISO responde 200 pero no es un PDF (${result.type || 'sin content-type'})`;
    warnings += 1;
  } else if (result.ok) {
    verdict = `ok  ${result.status}`;
  } else if (result.status === 403) {
    // ika.com sirve 403 a peticiones automatizadas. No es un enlace roto: es
    // protección antibot, y se anota como aviso para revisarlo en navegador.
    verdict = 'AVISO 403 a peticiones automatizadas (comprobar en navegador)';
    warnings += 1;
  } else {
    verdict = `FALLA ${result.status || result.error}`;
    failures += 1;
  }

  console.log(`${verdict.padEnd(56)} ${label}\n${' '.repeat(56)} ${url}`);
}

console.log(`\n${unique.length} destinos · ${failures} fallos · ${warnings} avisos`);
if (failures > 0) process.exit(1);
