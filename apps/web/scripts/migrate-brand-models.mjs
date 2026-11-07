#!/usr/bin/env node
/**
 * Arranque único de `src/content/modelos/`.
 *
 * Lee `src/data/brandModels.ts` (22 modelos documentados) y `src/data/products.ts`
 * (5 centrífugas Ortoalresa y BlueSlick, los modelos con ficha propia) y escribe
 * un JSON por modelo con la forma de `src/content/modelos.schema.json`.
 *
 * Desde que existen, los JSON son la fuente de verdad de las páginas de modelo:
 * este script sólo los arranca. Por eso no sobrescribe un archivo que ya existe
 * (usar `--force` lo rehace desde los registros y pierde lo que se editó).
 *
 * No inventa nada. `headline` y `lead` salen de lo que el registro ya dice;
 * `accessories`, `related` y `faqs` quedan vacías hasta que alguien con la
 * fuente delante las escriba. Las cifras traen como `source` la página oficial
 * del modelo, la misma que el registro declara.
 *
 * Los registros se importan como módulos (Node 22.18+ y 24 eliminan los tipos
 * al cargar un `.ts`) en vez de leerse con expresiones regulares: así los
 * valores son los que el sitio usa, no una lectura aproximada del texto.
 *
 * Ejecutar: node scripts/migrate-brand-models.mjs [--force]
 */
import { existsSync, mkdirSync, readdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const outDir = join(root, 'src/content/modelos');
const showroomDir = join(root, 'src/lib/showroom/content');
const force = process.argv.includes('--force');

const { brands } = await import('../src/data/brands.ts');
const { brandModels } = await import('../src/data/brandModels.ts');
const { products } = await import('../src/data/products.ts');
const { productImages } = await import('../src/data/productImages.ts');
const { equipmentScope } = await import('../src/data/equipmentScope.ts');

/** `equipmentScope.ts` llama `dispersion-homogeneizacion` a lo que el contenido llama `dispersion`. */
const FAMILY = { 'dispersion-homogeneizacion': 'dispersion' };
const family = (id) => FAMILY[id] ?? id;
const familyName = (id) => equipmentScope.find((entry) => entry.id === id).name;
const brandName = (id) => brands.find((brand) => brand.id === id).name;

const showroomIds = new Set(
  readdirSync(showroomDir)
    .filter((file) => file.endsWith('.ts') && !['sources.ts', 'types.ts'].includes(file))
    .map((file) => file.replace(/\.ts$/, '')),
);
const verifiedImage = (id) => productImages.find((row) => row.id === id && row.status === 'VERIFIED');

/** Corta en el último punto que cabe, para no dejar una frase a medias. */
function fit(text, max) {
  if (text.length <= max) return text;
  const cut = text.slice(0, max);
  const stop = cut.lastIndexOf('. ');
  if (stop === -1) throw new Error(`no se puede acortar a ${max} caracteres: ${text}`);
  return cut.slice(0, stop + 1);
}

const stubs = [];

for (const m of brandModels) {
  stubs.push({
    slug: m.id,
    family: family(m.familyId),
    brand: m.brandId,
    name: m.name,
    headline: fit(`${brandName(m.brandId)} ${m.name}: ${familyName(m.familyId).toLowerCase()}`, 120),
    lead: fit(m.does, 300),
    applications: [...m.uses],
    specs: m.criteria.map(({ label, value }) => ({ label, value, source: m.officialUrl })),
    accessories: [],
    related: [],
    faqs: [],
    official_url: m.officialUrl,
    ...(m.officialPdfUrl ? { official_pdf_url: m.officialPdfUrl } : {}),
    ...(verifiedImage(m.id) ? { image_id: m.id } : {}),
    ...(showroomIds.has(m.id) ? { showroom_id: m.id } : {}),
    verified_on: m.verifiedOn,
  });
}

/** Familia editorial de cada producto con ficha propia (products.ts no la declara por id). */
const PRODUCT_FAMILY = { ortoalresa: 'centrifugacion', serva: 'electroforesis' };

for (const p of products) {
  const image = verifiedImage(p.id);
  stubs.push({
    slug: p.id,
    family: PRODUCT_FAMILY[p.brandId],
    brand: p.brandId,
    name: p.name,
    headline: fit(`${brandName(p.brandId)} ${p.name}: ${p.equipmentType.toLowerCase()}`, 120),
    lead: fit(p.summary, 300),
    applications: [...(p.applications ?? [p.application])],
    specs: (p.keySpecs ?? []).map(({ label, value }) => ({ label, value, source: p.manufacturerUrl })),
    accessories: [],
    related: [],
    faqs: [],
    official_url: p.manufacturerUrl,
    ...(p.datasheetUrl ? { official_pdf_url: p.datasheetUrl } : {}),
    ...(image ? { image_id: p.id } : {}),
    ...(showroomIds.has(p.id) ? { showroom_id: p.id } : {}),
    verified_on: image.verifiedOn,
  });
}

mkdirSync(outDir, { recursive: true });
let written = 0;
let kept = 0;
for (const stub of stubs) {
  const file = join(outDir, `${stub.slug}.json`);
  if (existsSync(file) && !force) {
    kept += 1;
    continue;
  }
  writeFileSync(file, `${JSON.stringify(stub, null, 2)}\n`);
  written += 1;
}
console.log(
  `migrate-brand-models: ${written} escritos, ${kept} ya existían (${brandModels.length} de brandModels.ts + ${products.length} de products.ts)`,
);
