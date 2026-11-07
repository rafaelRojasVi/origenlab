#!/usr/bin/env node
/**
 * Contenido de las páginas de modelo y de aplicaciones.
 *
 * Comprueba cada `src/content/modelos/*.json` contra
 * `src/content/modelos.schema.json` (la misma forma que lee el formulario del
 * tablero) y contra los registros del sitio. Falla, nombrando el archivo,
 * cuando:
 *
 *   - falta una clave obligatoria, sobra una clave, o un tipo, largo, patrón o
 *     valor de enumeración no cumple el esquema
 *   - aparece `price`, `stock`, `lead_time` o `warranty`: nada de eso está
 *     verificado para estas marcas
 *   - `specs[*].source`, `official_url` u `official_pdf_url` no es una URL
 *     http(s) del fabricante
 *   - `brand` no está en `APPROVED_BRAND_IDS`, o el enum del esquema se
 *     desvió de esa lista
 *   - `slug` no coincide con el nombre del archivo, o dos archivos colisionan
 *   - `image_id` no apunta a una fila `VERIFIED` de `productImages.ts`
 *   - `showroom_id` no tiene escena en `src/lib/showroom/content/`
 *   - `related` nombra un modelo que no existe (o a sí mismo)
 *
 * También exige que cada `src/content/aplicaciones/*.md` abra con un
 * encabezado que declare `title`. No hace peticiones de red: que las fuentes
 * sigan respondiendo lo comprueba `npm run verify:sources`.
 *
 * Ejecutar: npm run validate:content
 */
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const modelosDir = join(root, 'src/content/modelos');
const aplicacionesDir = join(root, 'src/content/aplicaciones');
const showroomDir = join(root, 'src/lib/showroom/content');
const schema = JSON.parse(readFileSync(join(root, 'src/content/modelos.schema.json'), 'utf8'));

// Los registros se importan como módulos: Node 22.18+ y 24 eliminan los tipos
// al cargar un `.ts`, el mismo camino que `validate-images.mjs`.
const { APPROVED_BRAND_IDS } = await import('../src/data/brands.ts');
const { productImages } = await import('../src/data/productImages.ts');

const URL_PATTERN = '^https?://';
const FORBIDDEN_KEYS = ['price', 'stock', 'lead_time', 'warranty'];

let failed = false;
function assert(condition, message) {
  if (!condition) {
    console.error(`  x ${message}`);
    failed = true;
  }
}

/** Valida `value` contra el subconjunto de JSON Schema que usa el esquema. */
function check(value, node, path, errors) {
  const type = Array.isArray(value) ? 'array' : value === null ? 'null' : typeof value;
  if (node.type && node.type !== type) {
    errors.push(`${path} debe ser ${node.type}, no ${type}`);
    return;
  }
  if (type === 'string') {
    if (node.enum && !node.enum.includes(value)) errors.push(`${path} «${value}» no está en [${node.enum.join(', ')}]`);
    if (node.maxLength !== undefined && value.length > node.maxLength)
      errors.push(`${path} mide ${value.length} caracteres y el máximo es ${node.maxLength}`);
    if (node.pattern && !new RegExp(node.pattern).test(value))
      errors.push(node.pattern === URL_PATTERN ? `${path} must be a URL` : `${path} no cumple ${node.pattern}`);
  } else if (type === 'array') {
    value.forEach((item, n) => check(item, node.items ?? {}, `${path}[${n}]`, errors));
  } else if (type === 'object') {
    for (const key of node.required ?? []) {
      if (!(key in value)) errors.push(`${path ? `${path}.` : ''}${key} es obligatorio y falta`);
    }
    for (const [key, child] of Object.entries(value)) {
      const here = path ? `${path}.${key}` : key;
      if (FORBIDDEN_KEYS.includes(key)) errors.push(`${here} está prohibido: no hay dato verificado de ${key}`);
      else if (!node.properties?.[key]) {
        if (node.additionalProperties === false) errors.push(`${here} no está en el esquema`);
      } else check(child, node.properties[key], here, errors);
    }
  }
}

/* -- Deriva entre esquema y registros ---------------------------------------- */

assert(
  JSON.stringify([...schema.properties.brand.enum].sort()) === JSON.stringify([...APPROVED_BRAND_IDS].sort()),
  'modelos.schema.json: el enum de brand no coincide con APPROVED_BRAND_IDS',
);
for (const key of FORBIDDEN_KEYS) {
  assert(!(key in schema.properties), `modelos.schema.json: declara «${key}», que está prohibido`);
}
const verifiedImageIds = new Set(productImages.filter((row) => row.status === 'VERIFIED').map((row) => row.id));
const allImageIds = new Set(productImages.map((row) => row.id));

/* -- Modelos ------------------------------------------------------------------ */

const files = existsSync(modelosDir) ? readdirSync(modelosDir).filter((f) => f.endsWith('.json')).sort() : [];
const parsed = new Map();
for (const file of files) {
  try {
    parsed.set(file, JSON.parse(readFileSync(join(modelosDir, file), 'utf8')));
  } catch (error) {
    assert(false, `${file}: JSON inválido (${error.message})`);
  }
}
const slugs = new Set([...parsed.keys()].map((file) => file.replace(/\.json$/, '')));

for (const [file, data] of parsed) {
  const errors = [];
  check(data, schema, '', errors);
  for (const message of errors) assert(false, `${file}: ${message}`);
  if (typeof data !== 'object' || data === null || Array.isArray(data)) continue;

  const id = file.replace(/\.json$/, '');
  if (typeof data.slug === 'string') assert(data.slug === id, `${file}: slug «${data.slug}» debe ser «${id}»`);
  if (typeof data.brand === 'string')
    assert(APPROVED_BRAND_IDS.includes(data.brand), `${file}: brand «${data.brand}» no está en APPROVED_BRAND_IDS`);
  if (typeof data.image_id === 'string') {
    assert(allImageIds.has(data.image_id), `${file}: image_id «${data.image_id}» no existe en productImages.ts`);
    assert(
      !allImageIds.has(data.image_id) || verifiedImageIds.has(data.image_id),
      `${file}: image_id «${data.image_id}» no tiene fila VERIFIED en productImages.ts`,
    );
  }
  if (typeof data.showroom_id === 'string')
    assert(
      existsSync(join(showroomDir, `${data.showroom_id}.ts`)),
      `${file}: showroom_id «${data.showroom_id}» no tiene escena en src/lib/showroom/content/`,
    );
  if (Array.isArray(data.related)) {
    for (const other of data.related) {
      assert(other !== id, `${file}: related no puede nombrar al propio modelo`);
      assert(other === id || slugs.has(other), `${file}: related «${other}» no es un modelo de la colección`);
    }
  }
}

/* -- Aplicaciones ------------------------------------------------------------- */

const pages = existsSync(aplicacionesDir) ? readdirSync(aplicacionesDir).filter((f) => f.endsWith('.md')).sort() : [];
for (const file of pages) {
  const source = readFileSync(join(aplicacionesDir, file), 'utf8');
  const header = /^---\r?\n([\s\S]*?)\r?\n---/.exec(source);
  assert(header !== null, `${file}: debe abrir con un encabezado --- ... ---`);
  if (header) assert(/^title:\s*\S/m.test(header[1]), `${file}: el encabezado debe declarar title`);
}

if (failed) {
  console.error(`\nvalidate:content: FALLA (${files.length} modelos, ${pages.length} aplicaciones)`);
  process.exit(1);
}
console.log(`validate:content: OK (${files.length} modelos, ${pages.length} aplicaciones)`);
