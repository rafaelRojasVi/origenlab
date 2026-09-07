#!/usr/bin/env node
/**
 * Procedencia y permiso de las fotografías de producto.
 *
 * Comprueba `src/data/productImages.ts` contra el catálogo, contra los archivos
 * de `public/products/` y contra el HTML construido en `dist/`. Falla cuando:
 *
 *   - una imagen local bajo `public/products/` no tiene fila en el registro
 *   - una fila apunta a un host remoto como activo local, o el HTML construido
 *     incrusta una imagen que no se sirve desde el propio sitio
 *   - la marca de una fila no está en la lista cerrada, o el producto o modelo
 *     al que apunta no existe en el catálogo, o el nombre no coincide
 *   - una fila `VERIFIED` no declara base de permiso, prueba, fuente, alt,
 *     original, dimensiones o derivados, o los archivos no existen o no miden
 *     lo que la fila dice
 *   - una fila que no está `VERIFIED` aparece como fotografía en el HTML
 *   - una imagen de familia representativa aparece sin el rótulo que lo dice
 *   - un `<img>` de producto en `dist/` va sin `width`, `height` o `alt`, o su
 *     `alt` es el de la fila pero vacío
 *   - `products.ts` declara un `imagePath` que el registro no respalda
 *
 * No hace peticiones de red: los destinos de `officialUrl` e `imageSourceUrl`
 * los comprueba `npm run verify:sources`.
 *
 * Ejecutar: npm run validate:images   (después de npm run build)
 */
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import sharp from 'sharp';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const dist = join(root, 'dist');
let failed = false;

function assert(condition, message) {
  if (!condition) {
    console.error(`  x ${message}`);
    failed = true;
  }
}

function walk(dir, ext, acc = []) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) walk(full, ext, acc);
    else if (entry.name.endsWith(ext)) acc.push(full);
  }
  return acc;
}

const STATUSES = ['VERIFIED', 'ASSET_PERMISSION_NEEDED', 'CONTENT_NEEDED', 'REJECTED'];
const SOURCE_TYPES = [
  'activo-origenlab',
  'recurso-distribuidor',
  'portal-prensa',
  'entrega-fabricante',
  'licencia-oficial',
];
const SCOPES = ['modelo-exacto', 'familia-representativa'];

/*
 * Los registros se importan como módulos: Node 24 elimina los tipos al cargar
 * `.ts`, de modo que la comprobación ve las filas reales, incluidas las que se
 * construyen con una función auxiliar, y no una lectura por expresión regular.
 */
const { productImages: rows, PRODUCT_IMAGE_WIDTHS: widths } = await import('../src/data/productImages.ts');
const { APPROVED_BRAND_IDS: approved, brands } = await import('../src/data/brands.ts');
const { products } = await import('../src/data/products.ts');
const { brandModels } = await import('../src/data/brandModels.ts');

assert(widths.length > 0, 'productImages.ts: falta PRODUCT_IMAGE_WIDTHS');
const brandNames = Object.fromEntries(brands.map((brand) => [brand.id, brand.name]));
const productsById = Object.fromEntries(products.map((product) => [product.id, product]));
const modelsById = Object.fromEntries(brandModels.map((model) => [model.id, model]));

/* -- 1. Filas del registro ------------------------------------------------ */

for (const row of rows) {
  const id = row.id ?? '(sin id)';
  assert(/^[a-z0-9-]+$/.test(id), `${id}: id inválido`);
  assert(approved.includes(row.brandId), `${id}: marca fuera de la lista cerrada (${row.brandId})`);
  assert(STATUSES.includes(row.status), `${id}: status desconocido (${row.status})`);
  assert(SOURCE_TYPES.includes(row.sourceType), `${id}: sourceType desconocido (${row.sourceType})`);
  assert(SCOPES.includes(row.scope), `${id}: scope desconocido (${row.scope})`);
  assert(/^https?:\/\//.test(row.officialUrl ?? ''), `${id}: falta officialUrl`);
  assert(/^https?:\/\//.test(row.imageSourceUrl ?? ''), `${id}: falta imageSourceUrl`);
  assert(/^\d{4}-\d{2}-\d{2}$/.test(row.verifiedOn ?? ''), `${id}: falta verifiedOn`);
  assert((row.alt ?? '').trim().length >= 12, `${id}: alt vacío o demasiado corto`);
  assert((row.permissionBasis ?? '').trim().length >= 40, `${id}: falta permissionBasis`);
  assert((row.permissionEvidence ?? '').trim().length >= 10, `${id}: falta permissionEvidence`);
  assert(
    (row.productId ? 1 : 0) + (row.modelId ? 1 : 0) <= 1,
    `${id}: apunta a un producto y a un modelo a la vez`,
  );

  // Marca y modelo tienen que existir en el catálogo con el mismo nombre.
  if (row.productId) {
    const product = productsById[row.productId];
    assert(product, `${id}: productId ${row.productId} no existe en products.ts`);
    if (product) {
      assert(product.name === row.model, `${id}: el modelo «${row.model}» no coincide con products.ts («${product.name}»)`);
      assert(product.brandId === row.brandId, `${id}: el producto ${row.productId} pertenece a ${product.brandId}, no a ${row.brandId}`);
    }
  }
  if (row.modelId) {
    const model = modelsById[row.modelId];
    assert(model, `${id}: modelId ${row.modelId} no existe en brandModels.ts`);
    if (model) {
      assert(model.brandId === row.brandId, `${id}: el modelo ${row.modelId} pertenece a ${model.brandId}, no a ${row.brandId}`);
      assert(
        model.name === row.model || model.name.startsWith(`${row.model} `),
        `${id}: el modelo «${row.model}» no coincide con brandModels.ts («${model.name}»)`,
      );
    }
  }
  // Una imagen de familia representativa lo dice también en su alt.
  if (row.scope === 'familia-representativa') {
    assert(/representativa/i.test(row.alt ?? ''), `${id}: imagen de familia cuyo alt no la declara representativa`);
  }

  // Un activo local nunca es una URL remota.
  if (row.masterPath) {
    assert(!/^(https?:)?\/\//.test(row.masterPath), `${id}: masterPath apunta a un host remoto`);
    assert(row.masterPath.startsWith(`/products/${row.brandId}/`), `${id}: masterPath fuera de /products/${row.brandId}/ (${row.masterPath})`);
  }

  if (row.status === 'VERIFIED') {
    assert(row.masterPath, `${id}: VERIFIED sin masterPath`);
    assert(row.masterWidth && row.masterHeight, `${id}: VERIFIED sin dimensiones del original`);
    assert(row.derivatives, `${id}: VERIFIED sin derivados declarados`);
  } else {
    assert(row.note, `${id}: una fila ${row.status} tiene que decir qué falta o por qué se rechazó`);
  }
}

const ids = rows.map((row) => row.id);
assert(new Set(ids).size === ids.length, 'productImages.ts: ids repetidos');
// Un producto o modelo no tiene dos filas publicables.
for (const key of ['productId', 'modelId']) {
  const published = rows.filter((row) => row.status === 'VERIFIED' && row[key]).map((row) => row[key]);
  assert(new Set(published).size === published.length, `productImages.ts: dos filas VERIFIED para el mismo ${key}`);
}

/* -- 2. Archivos: cada imagen local tiene fila; cada fila VERIFIED, archivo -- */

const productsDir = join(root, 'public', 'products');
const derivedSuffix = new RegExp(`-(${widths.join('|')})\\.(avif|webp)$`);
const masters = new Set(rows.filter((row) => row.masterPath).map((row) => row.masterPath));

if (existsSync(productsDir)) {
  for (const brandDir of readdirSync(productsDir)) {
    const dir = join(productsDir, brandDir);
    if (!statSync(dir).isDirectory()) continue;
    assert(approved.includes(brandDir), `public/products/${brandDir}: carpeta de una marca fuera de la lista cerrada`);
    for (const file of readdirSync(dir)) {
      const rel = `/products/${brandDir}/${file}`;
      if (derivedSuffix.test(file)) {
        const master = [...masters].find((path) => rel.startsWith(path.replace(/\.[a-z0-9]+$/, '') + '-'));
        assert(master, `${rel}: derivado sin fila en productImages.ts`);
        continue;
      }
      assert(masters.has(rel), `${rel}: imagen local sin fila de procedencia en productImages.ts`);
    }
  }
}

for (const row of rows) {
  if (row.status !== 'VERIFIED' || !row.masterPath) continue;
  const masterFile = join(root, 'public', row.masterPath);
  assert(existsSync(masterFile), `${row.id}: falta el original ${row.masterPath}`);
  if (existsSync(masterFile)) {
    const meta = await sharp(masterFile).metadata();
    assert(
      meta.width === row.masterWidth && meta.height === row.masterHeight,
      `${row.id}: el original mide ${meta.width}x${meta.height}, la fila dice ${row.masterWidth}x${row.masterHeight}`,
    );
  }
  const base = row.masterPath.replace(/\.[a-z0-9]+$/, '');
  for (const width of widths) {
    const declared = row.derivatives?.[width];
    assert(declared, `${row.id}: la fila no declara el derivado de ${width} px`);
    for (const ext of ['avif', 'webp']) {
      const rel = `${base}-${width}.${ext}`;
      const file = join(root, 'public', rel);
      assert(existsSync(file), `${row.id}: falta el derivado ${rel} (npm run build:product-images)`);
      if (existsSync(file) && declared) {
        const meta = await sharp(file).metadata();
        assert(
          meta.width === declared.width && meta.height === declared.height,
          `${row.id}: ${rel} mide ${meta.width}x${meta.height}, la fila dice ${declared.width}x${declared.height}`,
        );
        // Proporción entre peso y superficie: un derivado de 960 px por encima
        // de 400 kB es una fotografía sin optimizar, no un recorte de catálogo.
        const size = statSync(file).size;
        assert(size <= (width >= 960 ? 400_000 : 160_000), `${row.id}: ${rel} pesa ${Math.round(size / 1024)} kB`);
      }
    }
  }
}

/* -- 3. products.ts: imagePath sólo con respaldo del registro ------------- */

for (const [productId, product] of Object.entries(productsById)) {
  if (!product.imagePath) continue;
  const row = rows.find((candidate) => candidate.productId === productId);
  assert(row, `products.ts ${productId}: imagePath sin fila en productImages.ts`);
  if (row) {
    assert(row.masterPath === product.imagePath, `products.ts ${productId}: imagePath (${product.imagePath}) no coincide con el registro (${row.masterPath})`);
    assert(row.status === 'VERIFIED', `products.ts ${productId}: imagePath con fila ${row.status}; sólo VERIFIED se publica`);
  }
}

/* -- 4. HTML construido --------------------------------------------------- */

if (!existsSync(dist)) {
  console.error('  x dist/ no existe: ejecute npm run build antes de validate:images');
  failed = true;
} else {
  const verified = new Map(rows.filter((row) => row.status === 'VERIFIED').map((row) => [row.id, row]));
  const seen = new Set();
  for (const file of walk(dist, '.html')) {
    const rel = file.slice(dist.length + 1);
    // Las plantillas de firma de correo no son páginas del sitio y llevan
    // rutas absolutas a propósito (el cliente de correo no resuelve relativas).
    if (rel.startsWith('email/')) continue;
    const html = readFileSync(file, 'utf8');
    const at = (message) => `${rel}: ${message}`;

    // Toda imagen de producto en el HTML se sirve desde el propio sitio.
    for (const match of html.matchAll(/<img\b[^>]*\bsrc="([^"]+)"[^>]*>/g)) {
      const src = match[1];
      assert(!/^(https?:)?\/\//.test(src) || src.startsWith('https://origenlab.cl/'), at(`imagen remota: ${src}`));
      if (!src.startsWith('/products/')) continue;
      const tag = match[0];
      assert(/\bwidth="\d+"/.test(tag) && /\bheight="\d+"/.test(tag), at(`imagen de producto sin dimensiones: ${src}`));
      const alt = tag.match(/\balt="([^"]*)"/)?.[1];
      const row = rows.find((candidate) => candidate.masterPath === src);
      assert(row, at(`imagen de producto sin fila en el registro: ${src}`));
      if (!row) continue;
      seen.add(row.id);
      assert(row.status === 'VERIFIED', at(`publica ${row.id} con estado ${row.status}`));
      // El `alt` vacío es legítimo dentro de un enlace cuyo texto ya nombra el modelo.
      if (alt !== '' && alt !== undefined) {
        assert(alt === row.alt, at(`alt de ${row.id} no es el del registro`));
      }
    }
    for (const match of html.matchAll(/srcset="([^"]+)"/g)) {
      for (const candidate of match[1].split(',')) {
        const url = candidate.trim().split(/\s+/)[0];
        assert(!/^(https?:)?\/\//.test(url), at(`srcset remoto: ${url}`));
      }
    }

    // Una imagen de familia representativa lleva el rótulo que lo dice.
    for (const match of html.matchAll(/<figure\b[^>]*data-image-id="([^"]+)"[^>]*data-image-scope="([^"]+)"[^>]*>([\s\S]*?)<\/figure>/g)) {
      const [, id, scope, inner] = match;
      const row = verified.get(id);
      assert(row, at(`figura ${id} sin fila VERIFIED`));
      if (row) assert(scope === row.scope, at(`figura ${id} declara ${scope}, el registro dice ${row.scope}`));
      if (scope === 'familia-representativa') {
        assert(
          /Imagen representativa de la familia/.test(inner),
          at(`figura ${id} es de familia representativa y no lo rotula`),
        );
      }
    }
  }
  for (const [id] of verified) {
    if (!seen.has(id)) console.log(`  · ${id} está VERIFIED y ninguna página lo publica`);
  }
}

/* -- Resumen --------------------------------------------------------------- */

const byStatus = STATUSES.map((status) => `${status} ${rows.filter((row) => row.status === status).length}`).join(', ');
if (failed) {
  console.error(`\nvalidate:images: FALLA (${rows.length} filas: ${byStatus})`);
  process.exit(1);
}
console.log(`validate:images: OK (${rows.length} filas: ${byStatus}; marcas ${Object.keys(brandNames).length})`);
