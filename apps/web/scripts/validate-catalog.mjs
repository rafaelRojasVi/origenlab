#!/usr/bin/env node
/**
 * Integridad del catálogo y de los activos (sin runtime de TypeScript).
 *
 * Verifica hechos de negocio y contratos de datos, no decisiones visuales:
 * las páginas pueden rediseñarse sin tocar este archivo, pero no pueden
 * publicar una marca sin logotipo, un producto sin imagen ni una
 * especificación sin grupo.
 */
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
let failed = false;

function assert(condition, message) {
  if (!condition) {
    console.error(`  x ${message}`);
    failed = true;
  }
}

function read(rel) {
  return readFileSync(join(root, rel), 'utf8');
}

function walk(dir, ext, acc = []) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) walk(full, ext, acc);
    else if (entry.name.endsWith(ext)) acc.push(full);
  }
  return acc;
}

const brandsSrc = read('src/data/brands.ts');
const productsSrc = read('src/data/products.ts');
const familiesSrc = read('src/data/productFamilies.ts');
const categoriesSrc = read('src/data/categories.ts');
const contactSrc = read('src/data/contact.ts');
const specGroupsSrc = read('src/data/specGroups.ts');
const claimsSrc = read('src/data/claims.ts');
const scopeSrc = read('src/data/equipmentScope.ts');
const consultationSrc = read('src/data/consultation.ts');
const legalSrc = read('src/data/legal.ts');

const CANONICAL_ORTO_ORDER = [
  'biocen-22',
  'biocen-22-r',
  'digicen-22',
  'digicen-22-r',
  'consul-22',
];
const REMOVED_SLUGS = ['bioprocen-22-r'];
const IMAGE_WIDTHS = [480, 960];

/* -- 1. Copy comercial: nada que no se pueda sostener -------------------- */

const UNSAFE_CLAIMS =
  /distribuidor (oficial|exclusivo)|representante oficial|representación exclusiva|certificad[oa] por|único distribuidor/i;
for (const rel of ['src/data/brands.ts', 'src/data/products.ts', 'src/data/categories.ts', 'src/data/company.ts']) {
  assert(!UNSAFE_CLAIMS.test(read(rel)), `${rel}: afirmación de representación o certificación sin respaldo`);
}
for (const file of walk(join(root, 'src'), '.astro')) {
  const src = readFileSync(file, 'utf8');
  const rel = file.slice(root.length + 1);
  // El texto que niega la representación es legítimo; el que la afirma, no.
  const affirms = src.match(UNSAFE_CLAIMS);
  if (affirms) {
    const line = src.slice(Math.max(0, src.indexOf(affirms[0]) - 60), src.indexOf(affirms[0]) + 40);
    assert(/no (declara|implica|se declara)|sin confirmación|salvo confirmación/i.test(line), `${rel}: "${affirms[0]}" sin la negación que lo acota`);
  }
}

/* -- 2. Marcas ----------------------------------------------------------- */

const brandBlocks = brandsSrc.split(/\n  \{\n/).slice(1);
assert(brandBlocks.length >= 6, 'brands.ts: se esperan al menos las 6 marcas de la firma corporativa');

const signature = read('public/email/origenlab-contacto-signature.html');
assert(
  signature.includes('Marcas con las que trabajamos'),
  'La firma corporativa ya no contiene el texto que respalda el muro de marcas',
);
assert(
  brandsSrc.includes("brandsWallHeading = 'Marcas con las que trabajamos'"),
  'brands.ts: el encabezado del muro debe repetir literalmente el texto verificado de la firma',
);

for (const block of brandBlocks) {
  const id = block.match(/id: '([^']+)'/)?.[1];
  const name = block.match(/name: '([^']+)'/)?.[1];
  assert(id, 'brands.ts: bloque de marca sin id');
  if (!id) continue;

  assert(/catalogPublished: (true|false)/.test(block), `${id}: falta catalogPublished`);
  const published = block.includes('catalogPublished: true');

  const logoPath = block.match(/logoPath: '([^']+)'/)?.[1];
  assert(logoPath, `${id}: falta logoPath`);
  if (logoPath) {
    assert(existsSync(join(root, 'public', logoPath)), `${id}: falta el archivo public${logoPath}`);
  }
  assert(/logoWidth: \d+/.test(block), `${id}: falta logoWidth (evita CLS)`);
  assert(/logoHeight: \d+/.test(block), `${id}: falta logoHeight (evita CLS)`);
  assert(/logoDisplayHeight: \d+/.test(block), `${id}: falta logoDisplayHeight (equilibrio óptico del muro)`);
  assert(/logoSourceUrl: '?\n?\s*'?https/.test(block) || /logoSourceUrl:/.test(block), `${id}: falta la procedencia del logotipo`);
  assert(/websiteUrl: 'https:/.test(block), `${id}: falta websiteUrl del fabricante`);

  if (published) {
    assert(/summary:/.test(block), `${id}: una marca con catálogo publicado necesita summary`);
    assert(/commercialNote:/.test(block), `${id}: falta commercialNote`);
    assert(
      productsSrc.includes(`brandId: '${id}'`),
      `${id}: catalogPublished sin productos en products.ts`,
    );
  } else {
    // Sin datos confirmados no se describe la marca: sólo logotipo y enlace.
    assert(
      !/summary:|listSummary:|commercialNote:/.test(block),
      `${id}: marca sin catálogo publicado no debe llevar descripción propia (ver docs/design/CONTENT_NEEDED.md)`,
    );
  }
  assert(name, `${id}: falta name`);
}

const brandIds = brandBlocks.map((block) => block.match(/id: '([^']+)'/)?.[1]).filter(Boolean);
for (const brandId of [...productsSrc.matchAll(/brandId: '([^']+)'/g)].map((m) => m[1])) {
  assert(brandIds.includes(brandId), `products.ts: brandId desconocido ${brandId}`);
}

/* -- 3. Productos -------------------------------------------------------- */

const slugs = [...productsSrc.matchAll(/slug: '([^']+)'/g)].map((m) => m[1]);
const seen = new Map();
for (const slug of slugs) seen.set(slug, (seen.get(slug) ?? 0) + 1);
for (const [slug, count] of seen) assert(count === 1, `products.ts: slug duplicado ${slug}`);

function blockForSlug(slug) {
  const marker = `slug: '${slug}'`;
  const index = productsSrc.indexOf(marker);
  if (index < 0) return '';
  return productsSrc.slice(productsSrc.lastIndexOf('\n  {', index), productsSrc.indexOf('\n  },', index));
}

for (const slug of REMOVED_SLUGS) {
  assert(!productsSrc.includes(`slug: '${slug}'`), `products.ts: producto retirado presente: ${slug}`);
  const pages = walk(join(root, 'src/pages'), '.astro')
    .map((file) => readFileSync(file, 'utf8'))
    .join('\n');
  assert(!pages.includes(slug), `pages: ruta pública para el producto retirado ${slug}`);
}
assert(
  existsSync(join(root, 'public/products/ortoalresa/bioprocen-22-r.avif')),
  'bioprocen archivado en disco (no borrar sin aprobación explícita)',
);

const familyOrder = familiesSrc.match(/ortoalresaCentrifugeSlugs\s*=\s*\[([\s\S]*?)\]\s*as const/);
assert(familyOrder, 'productFamilies.ts: no se encontró ortoalresaCentrifugeSlugs');

for (const slug of CANONICAL_ORTO_ORDER) {
  const block = blockForSlug(slug);
  assert(block.length > 0, `products.ts: falta el producto activo ${slug}`);
  if (!block) continue;

  assert(familyOrder?.[1].includes(`'${slug}'`), `productFamilies.ts: orden canónico sin ${slug}`);

  for (const field of [
    'manufacturerUrl:',
    'datasheetUrl:',
    'imagePath:',
    'availabilityNote:',
    'commercialNote:',
    'productFamilySlug:',
    'specsAttribution:',
    'equipmentType:',
  ]) {
    assert(block.includes(field), `${slug}: falta ${field}`);
  }

  assert(
    block.match(/datasheetUrl: '(https:[^']+)'/)?.[1],
    `${slug}: datasheetUrl debe ser https`,
  );
  assert(
    block.match(/manufacturerUrl: '(https:[^']+)'/)?.[1],
    `${slug}: manufacturerUrl debe ser https`,
  );

  const imagePath = block.match(/imagePath: '([^']+)'/)?.[1];
  assert(imagePath === `/products/ortoalresa/${slug}.avif`, `${slug}: imagePath debe seguir el slug`);
  assert(existsSync(join(root, 'public', imagePath ?? '')), `${slug}: falta el original ${imagePath}`);
  // Derivados que consume <picture>; sin ellos el srcset apunta a 404.
  for (const width of IMAGE_WIDTHS) {
    for (const ext of ['avif', 'webp']) {
      const derived = `public/products/ortoalresa/${slug}-${width}.${ext}`;
      assert(existsSync(join(root, derived)), `${slug}: falta el derivado ${derived} (npm run build:product-images)`);
    }
  }
}

/* -- 4. Especificaciones: toda etiqueta necesita grupo -------------------- */

const labels = [...productsSrc.matchAll(/\{ label: '([^']+)', value:/g)].map((m) => m[1]);
assert(labels.length > 0, 'products.ts: no se encontró ninguna especificación');
const groupedExact = new Set([...specGroupsSrc.matchAll(/^\s+'?([^':\n]+)'?: '(rendimiento|construccion|instalacion)'/gm)].map((m) => m[1].trim()));
const prefixes = [...specGroupsSrc.matchAll(/\['([^']+)', '(rendimiento|construccion|instalacion)'\]/g)].map((m) => m[1]);
for (const label of new Set(labels)) {
  const covered =
    groupedExact.has(label) || prefixes.some((prefix) => label.startsWith(prefix));
  assert(covered, `specGroups.ts: la especificación "${label}" no tiene grupo asignado`);
}

/* -- 5. Categorías y contacto -------------------------------------------- */

for (const slug of ['alimentos', 'control-de-calidad', 'laboratorio-clinico']) {
  assert(categoriesSrc.includes(`slug: '${slug}'`), `categories.ts: falta ${slug}`);
}
assert(
  (categoriesSrc.match(/shortName: '/g) ?? []).length ===
    (categoriesSrc.match(/slug: '/g) ?? []).length,
  'categories.ts: cada categoría necesita shortName para navegación y migas',
);
assert(
  contactSrc.includes('buildWhatsAppQuoteUrl'),
  'contact.ts: whatsappUrl debe delegar en buildWhatsAppQuoteUrl',
);
assert(
  !/addressLine[\s\S]{0,200}locationPublic: '[^']*Oettinger/.test(contactSrc),
  'contact.ts: la dirección de calle no puede formar parte del texto público',
);
const publicPages = walk(join(root, 'src/pages'), '.astro')
  .concat(walk(join(root, 'src/components'), '.astro'))
  .map((file) => readFileSync(file, 'utf8'))
  .join('\n');
assert(!publicPages.includes('Oettinger'), 'pages/components: la dirección de calle no se publica');

/* -- 5b. Registro de afirmaciones ---------------------------------------- */

/**
 * Una cifra visible tiene que ser comprobable por quien llegue después. El
 * registro obliga a redacción, fuente, fecha de medición, aprobación y estado
 * público; aquí se comprueba que ningún bloque se salte alguno de esos campos.
 */
const claimBlocks = claimsSrc.split(/\n  \{\n/).slice(1);
assert(claimBlocks.length > 0, 'claims.ts: no se encontró ninguna afirmación');

const claimIds = [];
for (const block of claimBlocks) {
  const id = block.match(/id: '([^']+)'/)?.[1];
  assert(id, 'claims.ts: bloque sin id');
  if (!id) continue;
  claimIds.push(id);

  for (const field of ['text:', 'value:', 'source:', 'measuredOn:', 'approvedBy:', 'approvedOn:', 'status:', 'visibility:']) {
    assert(block.includes(field), `claims.ts ${id}: falta ${field}`);
  }

  const status = block.match(/status: '([^']+)'/)?.[1];
  assert(
    ['approved', 'proposed', 'unavailable'].includes(status ?? ''),
    `claims.ts ${id}: status desconocido ${status}`,
  );

  if (status === 'approved') {
    // Aprobar sin fuente ni fecha convertiría el registro en decoración.
    assert(!/source: null/.test(block), `claims.ts ${id}: aprobada sin fuente`);
    assert(!/measuredOn: null/.test(block), `claims.ts ${id}: aprobada sin fecha de medición`);
    assert(!/approvedBy: null/.test(block), `claims.ts ${id}: aprobada sin responsable`);
    assert(!/approvedOn: null/.test(block), `claims.ts ${id}: aprobada sin fecha de aprobación`);
    assert(!/text: '',/.test(block), `claims.ts ${id}: aprobada sin redacción pública`);
  } else {
    assert(
      /visibility: 'internal'/.test(block),
      `claims.ts ${id}: una afirmación no aprobada no puede ser pública`,
    );
    assert(block.includes('note:'), `claims.ts ${id}: sin aprobar y sin explicar por qué`);
  }
}

for (const id of ['clientes-atendidos', 'ventas-cerradas', 'anos-de-experiencia']) {
  assert(claimIds.includes(id), `claims.ts: falta la constancia de la cifra omitida ${id}`);
}

// Ninguna plantilla puede saltarse la puerta: `publicClaim` es el único acceso.
for (const file of walk(join(root, 'src'), '.astro')) {
  const src = readFileSync(file, 'utf8');
  const rel = file.slice(root.length + 1);
  if (!src.includes("from '../../data/claims'") && !src.includes("from '../data/claims'")) continue;
  // `claims.ts` en un comentario es legítimo; `claims.filter(...)` en una
  // plantilla se salta la puerta y publicaría una cifra sin aprobar.
  assert(
    !/\bclaims\.(?!ts\b)[a-zA-Z_$]/.test(src) && !/import \{[^}]*\bclaims\b[^}]*\}/.test(src),
    `${rel}: usar publicClaim(), no el arreglo claims directamente`,
  );
}

/* -- 5c. Alcance, asesoría y estado legal -------------------------------- */

// Las familias sin ficha no pueden llevar modelo, marca ni cifra asociada.
const consultaEntries = scopeSrc.split(/\n  \{\n/).slice(1).filter((block) => block.includes("tier: 'consulta'"));
assert(consultaEntries.length >= 3, 'equipmentScope.ts: se esperan al menos 3 familias por consulta');
for (const block of consultaEntries) {
  const id = block.match(/id: '([^']+)'/)?.[1] ?? '(sin id)';
  for (const field of ['href:', 'countClaimId:', 'imageProductSlug:', 'brandSlug:']) {
    assert(!block.includes(field), `equipmentScope.ts ${id}: una familia por consulta no puede declarar ${field}`);
  }
  assert(block.includes('question:'), `equipmentScope.ts ${id}: falta la pregunta que abre la consulta`);
}

// Sobre la especialista sólo viven los tres hechos confirmados.
assert(
  /name: 'Tatiana Vivanco'/.test(consultationSrc),
  'consultation.ts: falta el nombre confirmado de la especialista',
);
for (const field of ['bio', 'photo', 'yearsOfExperience', 'linkedin', 'degree']) {
  assert(
    !new RegExp(`\\b${field}\\b\\s*:`, 'i').test(consultationSrc),
    `consultation.ts: ${field} no está confirmado y no puede publicarse`,
  );
}
assert(
  !/consultant[\s\S]{0,400}(años de experiencia|experiencia de \d)/i.test(consultationSrc),
  'consultation.ts: no declarar años de experiencia sin dato confirmado',
);

// Ningún dato legal puede rellenarse desde el repositorio.
const legalFactBlocks = legalSrc.split(/\n  \{\n/).slice(1).filter((block) => block.includes('owner:'));
assert(legalFactBlocks.length >= 8, 'legal.ts: inventario de datos legales incompleto');
for (const block of legalFactBlocks) {
  const id = block.match(/id: '([^']+)'/)?.[1] ?? '(sin id)';
  assert(/value: null/.test(block), `legal.ts ${id}: un dato legal no puede rellenarse desde el repositorio`);
  assert(/owner: '(CONTENIDO|LEGAL)'/.test(block), `legal.ts ${id}: falta owner`);
  assert(block.includes('blocks:'), `legal.ts ${id}: falta qué bloquea`);
}
assert(
  /reviewedBy: null as string \| null/.test(legalSrc),
  'legal.ts: el texto legal no puede darse por revisado sin nombre de profesional',
);
// El sitio no tiene carrito ni pasarela: sus condiciones regulan el uso de un
// sitio informativo, no una venta. El título tiene que decir eso.
const avisoSrc = read('src/pages/aviso-legal.astro');
const avisoTitle = avisoSrc.match(/<PageIntro\s+title="([^"]+)"/)?.[1] ?? '';
assert(
  /aviso legal/i.test(avisoTitle) && !/condiciones de venta/i.test(avisoTitle),
  `aviso-legal.astro: el título "${avisoTitle}" no refleja un sitio informativo sin venta en línea`,
);
assert(
  !existsSync(join(root, 'src/pages/condiciones-de-venta.astro')),
  'pages: no crear condiciones de venta; el sitio no cierra ninguna operación en línea',
);

/* -- 6. Activos de marca y sociales -------------------------------------- */

for (const rel of [
  'public/og/origenlab-og.png',
  'public/apple-touch-icon.png',
  'public/favicon.ico',
  'public/favicon.svg',
  'public/fonts/plus-jakarta-sans-latin-wght-normal.woff2',
  'public/fonts/ibm-plex-mono-latin-400-normal.woff2',
]) {
  assert(existsSync(join(root, rel)), `Falta el activo ${rel}`);
}

/* -- 7. Terceros: el sitio no carga nada de fuera ------------------------- */

const layoutSrc = read('src/layouts/Layout.astro');
const seoSrc = read('src/components/Seo.astro');
for (const [label, src] of [['Layout.astro', layoutSrc], ['Seo.astro', seoSrc]]) {
  assert(!/tidio|googletagmanager|google-analytics|fonts\.googleapis|fonts\.gstatic/i.test(src), `${label}: script o tipografía de terceros`);
}
assert(!layoutSrc.includes('Astro.generator'), 'Layout.astro: no exponer la versión del generador');

if (failed) {
  console.error('\nvalidate:catalog FALLÓ');
  process.exit(1);
}
console.log('validate:catalog OK');
