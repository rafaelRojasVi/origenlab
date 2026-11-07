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
 *   - `family` no es uno de los seis slugs de familia (`FAMILY_SLUGS`, que
 *     son también los segmentos de URL `/productos/{family}/`), o el enum del
 *     esquema se desvió de ellos
 *   - `slug` no coincide con el nombre del archivo, o dos archivos colisionan
 *   - `image_id` no apunta a una fila `VERIFIED` de `productImages.ts`
 *   - `showroom_id` no tiene escena en `src/lib/showroom/content/`
 *   - `related` nombra un modelo que no existe (o a sí mismo)
 *
 * También exige que cada `src/content/aplicaciones/*.md` abra con un
 * encabezado que declare `title`. No hace peticiones de red: que las fuentes
 * sigan respondiendo lo comprueba `npm run verify:sources`.
 *
 * `--self-test` aplica el validador a fixtures negativos (clave prohibida, fuente
 * no http, image_id inválido, marca no aprobada, etc.) y comprueba que
 * `content.config.ts` construye zod desde el esquema.
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
/** Las seis familias: segmento de URL de `/productos/{family}/{slug}/`. */
const FAMILY_SLUGS = ['sonicacion', 'dispersion', 'pesaje-humedad', 'osmometria', 'electroforesis', 'centrifugacion'];
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
assert(
  JSON.stringify([...schema.properties.family.enum].sort()) === JSON.stringify([...FAMILY_SLUGS].sort()),
  'modelos.schema.json: el enum de family no coincide con los seis slugs de familia',
);
for (const key of FORBIDDEN_KEYS) {
  assert(!(key in schema.properties), `modelos.schema.json: declara «${key}», que está prohibido`);
}
const verifiedImageIds = new Set(productImages.filter((row) => row.status === 'VERIFIED').map((row) => row.id));
const allImageIds = new Set(productImages.map((row) => row.id));

/* -- Modelos ------------------------------------------------------------------ */

/** Todos los errores de un modelo, ya con el nombre del archivo. `slugs` son los modelos que existen. */
function modelErrors(file, data, slugs) {
  const found = [];
  check(data, schema, '', found);
  if (typeof data === 'object' && data !== null && !Array.isArray(data)) {
    const id = file.replace(/\.json$/, '');
    if (typeof data.slug === 'string' && data.slug !== id) found.push(`slug «${data.slug}» debe ser «${id}»`);
    if (typeof data.family === 'string' && !FAMILY_SLUGS.includes(data.family))
      found.push(`family «${data.family}» no es una de las seis familias`);
    if (typeof data.brand === 'string' && !APPROVED_BRAND_IDS.includes(data.brand))
      found.push(`brand «${data.brand}» no está en APPROVED_BRAND_IDS`);
    if (typeof data.image_id === 'string') {
      if (!allImageIds.has(data.image_id)) found.push(`image_id «${data.image_id}» no existe en productImages.ts`);
      else if (!verifiedImageIds.has(data.image_id))
        found.push(`image_id «${data.image_id}» no tiene fila VERIFIED en productImages.ts`);
    }
    if (typeof data.showroom_id === 'string' && !existsSync(join(showroomDir, `${data.showroom_id}.ts`)))
      found.push(`showroom_id «${data.showroom_id}» no tiene escena en src/lib/showroom/content/`);
    if (Array.isArray(data.related)) {
      for (const other of data.related) {
        if (other === id) found.push('related no puede nombrar al propio modelo');
        else if (!slugs.has(other)) found.push(`related «${other}» no es un modelo de la colección`);
      }
    }
  }
  return found.map((message) => `${file}: ${message}`);
}

/* -- Autoprueba: los casos negativos se conservan, no se prueban a mano ------- */

function selfTest() {
  const base = JSON.parse(readFileSync(join(modelosDir, 'hielscher-up400st.json'), 'utf8'));
  const slugs = new Set(['hielscher-up400st', 'hielscher-up200st']);
  const run = (mutate) => {
    const data = structuredClone(base);
    mutate(data);
    return modelErrors('hielscher-up400st.json', data, slugs).join('\n');
  };
  const cases = [
    ['modelo válido', (d) => d, null],
    ['clave price', (d) => (d.price = '1'), 'price está prohibido'],
    ['clave stock', (d) => (d.stock = 3), 'stock está prohibido'],
    ['clave lead_time', (d) => (d.lead_time = '2 semanas'), 'lead_time está prohibido'],
    ['clave warranty', (d) => (d.warranty = '1 año'), 'warranty está prohibido'],
    ['clave ajena', (d) => (d.color = 'rojo'), 'color no está en el esquema'],
    ['falta lead', (d) => delete d.lead, 'lead es obligatorio y falta'],
    ['familia fuera del enum', (d) => (d.family = 'otra'), 'family «otra» no está en'],
    ['familia fuera de las seis', (d) => (d.family = 'otra'), 'family «otra» no es una de las seis familias'],
    ['marca no aprobada', (d) => (d.brand = 'acme'), 'brand «acme» no está en APPROVED_BRAND_IDS'],
    ['source vacío', (d) => (d.specs[0].source = ''), 'specs[0].source must be a URL'],
    ['source javascript:', (d) => (d.specs[0].source = 'javascript:alert(1)'), 'specs[0].source must be a URL'],
    ['source ftp', (d) => (d.specs[0].source = 'ftp://x.y/z'), 'specs[0].source must be a URL'],
    ['official_url sin http', (d) => (d.official_url = 'www.hielscher.com'), 'official_url must be a URL'],
    ['image_id inexistente', (d) => (d.image_id = 'no-existe'), 'no existe en productImages.ts'],
    ['image_id sin fila VERIFIED', (d) => (d.image_id = 'ortoalresa-bioprocen-22-r'), 'no tiene fila VERIFIED'],
    ['showroom_id sin escena', (d) => (d.showroom_id = 'no-existe'), 'no tiene escena'],
    ['slug distinto del archivo', (d) => (d.slug = 'otro'), 'slug «otro» debe ser'],
    ['related inexistente', (d) => (d.related = ['fantasma']), 'related «fantasma» no es un modelo'],
    ['headline demasiado largo', (d) => (d.headline = 'x'.repeat(121)), 'headline mide 121'],
  ];
  for (const [name, mutate, expected] of cases) {
    const out = run(mutate);
    if (expected === null) assert(out === '', `autoprueba «${name}»: debía pasar y dio\n${out}`);
    else assert(out.includes(expected), `autoprueba «${name}»: se esperaba «${expected}» y salió «${out}»`);
  }
  // La definición zod se construye desde el esquema JSON: una sola fuente de verdad.
  const config = readFileSync(join(root, 'src/content.config.ts'), 'utf8');
  assert(/from '\.\/content\/modelos\.schema\.json'/.test(config), 'content.config.ts debe importar modelos.schema.json');
  if (failed) {
    console.error(`\nvalidate:content --self-test: FALLA`);
    process.exit(1);
  }
  console.log(`validate:content --self-test: OK (${cases.length} casos)`);
}

if (process.argv.includes('--self-test')) {
  selfTest();
  process.exit(0);
}

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
  for (const message of modelErrors(file, data, slugs)) assert(false, message);
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
