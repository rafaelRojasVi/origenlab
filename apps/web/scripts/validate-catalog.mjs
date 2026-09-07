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
const modelsSrc = read('src/data/brandModels.ts');
const applicationsSrc = read('src/data/applications.ts');

const CANONICAL_ORTO_ORDER = [
  'biocen-22',
  'biocen-22-r',
  'digicen-22',
  'digicen-22-r',
  'consul-22',
];
/**
 * Productos retirados que no pueden volver sin la verificación que les falta.
 * `bioprocen-22-r` salió en 2026-05; `temed-25ml` y `repel-silane-ge17-1332-01`
 * el 2026-09-07, porque no fue posible verificar su identidad exacta ni su
 * alcance en el catálogo de SERVA.
 */
const REMOVED_SLUGS = ['bioprocen-22-r', 'temed-25ml', 'repel-silane-ge17-1332-01'];
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
assert(brandBlocks.length === 6, `brands.ts: se esperan exactamente 6 marcas, hay ${brandBlocks.length}`);

// La lista cerrada, la ortografía de los nombres y la correspondencia con los
// logotipos y el registro de fuentes las comprueba `validate:brands`. Aquí sólo
// se miran los contratos de datos de cada registro.
assert(
  brandsSrc.includes("brandsWallHeading = 'Marcas con las que trabajamos'"),
  'brands.ts: el encabezado del muro debe repetir literalmente el texto verificado de la firma',
);

const registrySrc = read('src/data/sourceRegistry.ts');

for (const block of brandBlocks) {
  const id = block.match(/id: '([^']+)'/)?.[1];
  const name = block.match(/name: '([^']+)'/)?.[1];
  assert(id, 'brands.ts: bloque de marca sin id');
  if (!id) continue;

  // Dos ejes, no uno: página propia y alcance comercial confirmado.
  assert(
    /editorialPublished: (true|false)/.test(block),
    `${id}: falta editorialPublished (habilita /marcas/{slug}/)`,
  );
  assert(
    /commercialScopeConfirmed: (true|false)/.test(block),
    `${id}: falta commercialScopeConfirmed (habilita commercialNote)`,
  );
  assert(/layout: '[a-z]+'/.test(block), `${id}: falta layout (una disposición por marca)`);
  const editorial = block.includes('editorialPublished: true');
  const scopeConfirmed = block.includes('commercialScopeConfirmed: true');

  const logoPath = block.match(/logoPath: '([^']+)'/)?.[1];
  assert(logoPath, `${id}: falta logoPath`);
  if (logoPath) {
    assert(existsSync(join(root, 'public', logoPath)), `${id}: falta el archivo public${logoPath}`);
  }
  assert(/logoWidth: \d+/.test(block), `${id}: falta logoWidth (evita CLS)`);
  assert(/logoHeight: \d+/.test(block), `${id}: falta logoHeight (evita CLS)`);
  assert(/logoDisplayHeight: \d+/.test(block), `${id}: falta logoDisplayHeight (equilibrio óptico del muro)`);
  // La procedencia del logotipo vive ahora en el registro de fuentes, junto con
  // la base por la que puede publicarse; tenerla en dos sitios la dejaba
  // desincronizada.
  assert(
    new RegExp(`brandId: '${id}'`).test(registrySrc),
    `${id}: sin fila en src/data/sourceRegistry.ts (procedencia del logotipo y de la imagen)`,
  );
  // El enlace al fabricante va por https salvo excepción declarada.
  assert(
    /websiteUrl: 'https:/.test(block) || /websiteInsecure: true/.test(block),
    `${id}: websiteUrl no es https y no declara websiteInsecure`,
  );

  // Qué fabrica cada marca lo confirmó el negocio el 2026-09-06: es lo que
  // permite nombrar la familia junto al logotipo.
  assert(/familyId: '[^']+'/.test(block), `${id}: falta familyId`);
  assert(/listSummary:/.test(block), `${id}: falta listSummary (qué fabrica, en una línea)`);

  // Una página de marca necesita material editorial completo. Las seis lo
  // tienen, y el estándar es el mismo para todas: no hay página de segunda.
  if (editorial) {
    for (const field of [
      'summary:',
      'pageSubtitle:',
      'metaDescription:',
      'applicationAreas:',
      'manufacturerAttribution:',
    ]) {
      assert(block.includes(field), `${id}: marca con página propia necesita ${field}`);
    }
    // Modelos verificados: o ficha propia en products.ts, o entrada en
    // brandModels.ts con la fuente del fabricante. Una página de marca sin
    // ningún equipo nombrado sería un callejón sin salida.
    assert(
      productsSrc.includes(`brandId: '${id}'`) || modelsSrc.includes(`brandId: '${id}'`),
      `${id}: página de marca sin modelos en products.ts ni en brandModels.ts`,
    );
  }

  // El alcance comercial sólo se describe donde el negocio lo confirmó.
  if (scopeConfirmed) {
    assert(/commercialNote:/.test(block), `${id}: alcance confirmado y sin commercialNote`);
  } else {
    assert(
      !/commercialNote:/.test(block),
      `${id}: sin commercialScopeConfirmed no puede declarar commercialNote (ver docs/design/CONTENT_NEEDED.md)`,
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

/**
 * Las seis familias tienen destino. Las cuatro sin ficha propia
 * (`tier: 'documentada'`) publican modelos con la documentación del fabricante,
 * pero siguen sin poder declarar fotografía ni cifra aprobada: eso es lo que no
 * está verificado, y es lo que se comprueba aquí.
 */
const scopeEntries = scopeSrc.split(/\n  \{\n/).slice(1).filter((block) => block.includes('tier:'));
assert(scopeEntries.length === 6, `equipmentScope.ts: se esperan 6 familias, hay ${scopeEntries.length}`);
const documentedEntries = scopeEntries.filter((block) => block.includes("tier: 'documentada'"));
assert(
  documentedEntries.length >= 4,
  'equipmentScope.ts: se esperan al menos 4 familias en el nivel documentada',
);
for (const block of scopeEntries) {
  const id = block.match(/id: '([^']+)'/)?.[1] ?? '(sin id)';
  assert(block.includes('question:'), `equipmentScope.ts ${id}: falta la pregunta que abre la consulta`);
  assert(/href: '\/[^']*\/'/.test(block), `equipmentScope.ts ${id}: falta href con barra final`);
  assert(!block.includes('brandSlug:'), `equipmentScope.ts ${id}: la marca se declara en brands.ts, no aquí`);
}
for (const block of documentedEntries) {
  const id = block.match(/id: '([^']+)'/)?.[1] ?? '(sin id)';
  for (const field of ['countClaimId:', 'imageProductSlug:']) {
    assert(
      !block.includes(field),
      `equipmentScope.ts ${id}: una familia documentada no puede declarar ${field} (sin fotografía y sin cifra aprobada)`,
    );
  }
  // El destino de una familia sin ficha propia es la página de marca.
  const href = block.match(/href: '([^']+)'/)?.[1] ?? '';
  assert(
    href.startsWith('/marcas/'),
    `equipmentScope.ts ${id}: el destino de una familia documentada es /marcas/{slug}/, no ${href}`,
  );
}

/* -- 5d. Registro de modelos del fabricante ------------------------------ */

/**
 * Cada modelo que el sitio describe sin alojar tiene que decir de dónde salió y
 * cuándo se leyó. Sin fuente y sin fecha no es un dato: es una afirmación.
 */
const modelBlocks = modelsSrc.split(/\n  \{\n/).slice(1).filter((block) => block.includes('brandId:'));
assert(modelBlocks.length >= 14, `brandModels.ts: se esperaban al menos 14 entradas, hay ${modelBlocks.length}`);

const modelIds = new Set();
for (const block of modelBlocks) {
  const id = block.match(/id: '([^']+)'/)?.[1] ?? '(sin id)';
  assert(!modelIds.has(id), `brandModels.ts: id duplicado ${id}`);
  modelIds.add(id);

  const brandId = block.match(/brandId: '([^']+)'/)?.[1];
  assert(brandIds.includes(brandId), `brandModels.ts ${id}: brandId fuera de la lista cerrada (${brandId})`);
  const familyId = block.match(/familyId: '([^']+)'/)?.[1];
  assert(
    scopeSrc.includes(`id: '${familyId}'`),
    `brandModels.ts ${id}: familyId desconocido (${familyId})`,
  );

  for (const field of ['does:', 'uses:', 'criteria:', 'officialUrl:', 'officialUrlScope:', 'verifiedOn:']) {
    assert(block.includes(field), `brandModels.ts ${id}: falta ${field}`);
  }
  assert(/scope: '(modelo|familia)'/.test(block), `brandModels.ts ${id}: scope desconocido`);

  // El enlace va por https salvo la excepción declarada de Löser.
  const official = block.match(/officialUrl:\s*\n?\s*'([^']+)'/)?.[1] ?? '';
  if (!official.startsWith('https:')) {
    assert(
      /officialUrlInsecure: true/.test(block),
      `brandModels.ts ${id}: officialUrl no es https y no declara officialUrlInsecure`,
    );
  }
  // Sólo dominios del fabricante: ni revendedores ni catálogos raspados.
  assert(
    !/scribd|slideshare|docplayer|medicalexpo|directindustry|amazon|ebay|alibaba|mercadolibre/i.test(block),
    `brandModels.ts ${id}: fuente que no es del fabricante`,
  );
  // Nada de comercial en este registro: no está confirmado para estas marcas.
  for (const forbidden of ['price', 'precio:', 'stock', 'leadTime', 'plazo:', 'imagePath', 'warranty', 'garantia']) {
    assert(!block.includes(forbidden), `brandModels.ts ${id}: no puede declarar ${forbidden}`);
  }
  // Un enlace de familia tiene que explicar por qué no es del modelo.
  if (/officialUrlScope: 'familia'/.test(block) && /scope: 'modelo'/.test(block)) {
    assert(
      block.includes('sourceNote:'),
      `brandModels.ts ${id}: enlace de familia para un modelo concreto y sin sourceNote que lo explique`,
    );
  }
}

/* -- 5e. Aplicaciones: ninguna sin equipo -------------------------------- */

const applicationBlocks = applicationsSrc.split(/\n  \{\n/).slice(1).filter((block) => block.includes('familyIds:'));
assert(applicationBlocks.length === 6, `applications.ts: se esperan 6 aplicaciones, hay ${applicationBlocks.length}`);
const applicationSlugs = new Set();
for (const block of applicationBlocks) {
  const id = block.match(/id: '([^']+)'/)?.[1] ?? '(sin id)';
  for (const field of ['name:', 'slug:', 'task:', 'detail:', 'bring:', 'familyIds:']) {
    assert(block.includes(field), `applications.ts ${id}: falta ${field}`);
  }
  const slug = block.match(/slug: '([^']+)'/)?.[1] ?? '';
  assert(!applicationSlugs.has(slug), `applications.ts: slug duplicado ${slug}`);
  applicationSlugs.add(slug);

  const families = [...(block.match(/familyIds: \[([\s\S]*?)\]/)?.[1] ?? '').matchAll(/'([^']+)'/g)].map((m) => m[1]);
  assert(families.length > 0, `applications.ts ${id}: sin familia asociada seria un callejon sin salida`);
  for (const familyId of families) {
    assert(
      scopeSrc.includes(`id: '${familyId}'`),
      `applications.ts ${id}: familyId desconocido (${familyId})`,
    );
  }
}
// Toda familia publicada tiene que aparecer en al menos una aplicación: si no,
// hay equipo que ninguna tarea del sitio lleva a consultar.
for (const block of scopeEntries) {
  const familyId = block.match(/id: '([^']+)'/)?.[1] ?? '';
  assert(
    applicationsSrc.includes(`'${familyId}'`),
    `applications.ts: la familia ${familyId} no la reclama ninguna aplicación`,
  );
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
