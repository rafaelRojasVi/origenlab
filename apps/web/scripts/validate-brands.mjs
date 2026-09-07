#!/usr/bin/env node
/**
 * Lista cerrada de marcas publicadas.
 *
 * El sitio sólo puede mostrar las seis marcas aprobadas. Este script comprueba,
 * en las cuatro capas donde una marca puede colarse, que no haya ninguna más:
 *
 *   1. `src/data/brands.ts`      la lista y los registros coinciden
 *   2. `public/brands/`          hay un logotipo por marca y ninguno de sobra
 *   3. `src/data/sourceRegistry.ts`  una fila de procedencia por marca
 *   4. `src/data/brandModels.ts` ningún modelo de una marca no aprobada
 *   5. `src/data/applications.ts` ninguna aplicación cuelga de una marca ajena
 *   6. `dist/`                   HTML, sitemap, datos estructurados, navegación,
 *                                pie, logotipos y destinos externos
 *
 * La comprobación sobre `dist/` es la que importa de verdad: las otras tres
 * miran código, y el código puede tener una marca retirada en un dato muerto
 * sin que se publique. Esta mira lo que ve el visitante.
 *
 * Se ejecuta dentro de `npm run validate`, después del build. Sin `dist/`
 * comprueba sólo las tres primeras capas y lo dice.
 *
 * Ejecutar: npm run validate:brands
 */
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
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
  if (!existsSync(dir)) return acc;
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) walk(full, ext, acc);
    else if (entry.name.endsWith(ext)) acc.push(full);
  }
  return acc;
}

const brandsSrc = read('src/data/brands.ts');
const registrySrc = read('src/data/sourceRegistry.ts');
const modelsSrc = read('src/data/brandModels.ts');
const applicationsSrc = read('src/data/applications.ts');

/* -- 0. La lista aprobada ------------------------------------------------- */

const listMatch = brandsSrc.match(/APPROVED_BRAND_IDS = \[([\s\S]*?)\] as const/);
assert(listMatch, 'brands.ts: no se encontró APPROVED_BRAND_IDS');
const approved = listMatch
  ? [...listMatch[1].matchAll(/'([^']+)'/g)].map((m) => m[1])
  : [];

assert(approved.length === 6, `APPROVED_BRAND_IDS tiene ${approved.length} marcas, se esperan 6`);
assert(new Set(approved).size === approved.length, 'APPROVED_BRAND_IDS tiene ids repetidos');

/**
 * Nombres públicos exactos. Se comprueba la ortografía porque son marcas
 * registradas de terceros y escribirlas mal en el sitio de un distribuidor es
 * un error visible: no es «Adam's» sino «Adam Equipment», y «Löser» lleva
 * diéresis.
 */
const EXPECTED_NAMES = {
  hielscher: 'Hielscher Ultrasonics',
  ortoalresa: 'Ortoalresa',
  ika: 'IKA',
  'adam-equipment': 'Adam Equipment',
  loeser: 'Löser Messtechnik',
  serva: 'SERVA Electrophoresis',
};

/* -- 1. Los registros de brands.ts ---------------------------------------- */

const brandBlocks = brandsSrc.split(/\n  \{\n/).slice(1);
const declared = brandBlocks.map((block) => block.match(/id: '([^']+)'/)?.[1]).filter(Boolean);

assert(
  JSON.stringify(declared) === JSON.stringify(approved),
  `brands.ts: los registros (${declared.join(', ')}) no coinciden en contenido y orden con APPROVED_BRAND_IDS (${approved.join(', ')})`,
);

for (const block of brandBlocks) {
  const id = block.match(/id: '([^']+)'/)?.[1];
  if (!id) continue;
  const name = block.match(/name: '([^']+)'/)?.[1];
  assert(
    name === EXPECTED_NAMES[id],
    `brands.ts ${id}: el nombre público es "${name}", se espera "${EXPECTED_NAMES[id]}"`,
  );
  assert(/familyId: '[^']+'/.test(block), `brands.ts ${id}: falta familyId`);
  assert(/listSummary:/.test(block), `brands.ts ${id}: falta listSummary`);

  // El enlace al fabricante va por https salvo excepción declarada y explicada.
  const url = block.match(/websiteUrl: '([^']+)'/)?.[1] ?? '';
  if (!url.startsWith('https:')) {
    assert(
      /websiteInsecure: true/.test(block),
      `brands.ts ${id}: websiteUrl no es https y no declara websiteInsecure`,
    );
  } else {
    assert(
      !/websiteInsecure/.test(block),
      `brands.ts ${id}: declara websiteInsecure con una URL https`,
    );
  }
}

/* -- 2. Una familia por marca, sin repetir -------------------------------- */

const familyIds = [...brandsSrc.matchAll(/familyId: '([^']+)'/g)].map((m) => m[1]);
assert(
  new Set(familyIds).size === familyIds.length,
  `brands.ts: dos marcas declaran la misma familia (${familyIds.join(', ')})`,
);
const scopeSrc = read('src/data/equipmentScope.ts');
const scopeIds = [...scopeSrc.matchAll(/^    id: '([^']+)',$/gm)].map((m) => m[1]);
for (const familyId of familyIds) {
  assert(scopeIds.includes(familyId), `brands.ts: familyId desconocido ${familyId}`);
}
for (const scopeId of scopeIds) {
  assert(familyIds.includes(scopeId), `equipmentScope.ts: la familia ${scopeId} no tiene marca`);
}

/* -- 3. Logotipos: uno por marca y ninguno de sobra ------------------------ */

const logoDir = join(root, 'public', 'brands');
const logoFiles = readdirSync(logoDir).filter((name) => name.endsWith('-logo.png'));
const expectedLogos = new Set(
  brandBlocks
    .map((block) => block.match(/logoPath: '\/brands\/([^']+)'/)?.[1])
    .filter(Boolean),
);
assert(expectedLogos.size === approved.length, 'brands.ts: falta algún logoPath');
for (const file of logoFiles) {
  assert(
    expectedLogos.has(file),
    `public/brands/${file}: logotipo de una marca que ya no se publica (borrar o volver a aprobar la marca)`,
  );
}
for (const expected of expectedLogos) {
  assert(existsSync(join(logoDir, expected)), `Falta public/brands/${expected}`);
}

/* -- 4. Registro de procedencia: una fila por marca ----------------------- */

const registryIds = [...registrySrc.matchAll(/brandId: '([^']+)'/g)].map((m) => m[1]);
for (const id of approved) {
  assert(registryIds.includes(id), `sourceRegistry.ts: falta la fila de ${id}`);
}
for (const id of registryIds) {
  assert(approved.includes(id), `sourceRegistry.ts: fila de una marca no aprobada: ${id}`);
}
// Sin fuente oficial no se publica una marca: es la base de todo lo demás.
const registryBlocks = registrySrc.split(/\n  \{\n/).slice(1);
for (const block of registryBlocks) {
  const id = block.match(/brandId: '([^']+)'/)?.[1] ?? '(sin id)';
  assert(/officialUrl: '(https?:)/.test(block), `sourceRegistry.ts ${id}: falta officialUrl`);
  assert(/lastVerified: /.test(block), `sourceRegistry.ts ${id}: falta lastVerified`);
  assert(
    /status: '(VERIFIED|PDF_NOT_FOUND|ASSET_PERMISSION_NEEDED|CONTENT_NEEDED)'/.test(block),
    `sourceRegistry.ts ${id}: status desconocido`,
  );
  // Un PDF sólo se enlaza si lo aloja el propio fabricante.
  const pdf = block.match(/officialPdfUrl:\s*\n?\s*'([^']+)'/)?.[1];
  if (pdf) {
    assert(
      !/scribd|slideshare|docplayer|medicalexpo|directindustry|amazon|ebay|alibaba/i.test(pdf),
      `sourceRegistry.ts ${id}: el PDF no está alojado por el fabricante (${pdf})`,
    );
  }
}

/* -- 4b. Registro de modelos y aplicaciones ------------------------------- */

/**
 * La séptima marca no entra por `brands.ts`: entra por un modelo suelto en
 * `brandModels.ts` o por una aplicación que nombra a un fabricante que nadie
 * aprobó. Las dos puertas se cierran aquí.
 */
const modelBrandIds = [...modelsSrc.matchAll(/brandId: '([^']+)'/g)].map((m) => m[1]);
assert(modelBrandIds.length > 0, 'brandModels.ts: no se encontró ninguna entrada');
for (const id of new Set(modelBrandIds)) {
  assert(approved.includes(id), `brandModels.ts: modelo de una marca no aprobada: ${id}`);
}
// Cada marca aprobada aparece en el registro de modelos o en products.ts: una
// marca publicada sin un solo equipo nombrado no tendría página que sostener.
const productsSrcForBrands = read('src/data/products.ts');
for (const id of approved) {
  assert(
    modelBrandIds.includes(id) || productsSrcForBrands.includes(`brandId: '${id}'`),
    `${id}: marca aprobada sin modelos en brandModels.ts ni en products.ts`,
  );
}
// Las aplicaciones cuelgan de familias, y cada familia de una marca aprobada.
// Si `applications.ts` nombrara una marca directamente habría dos listas.
for (const name of Object.values(EXPECTED_NAMES)) {
  assert(
    !applicationsSrc.includes(name),
    `applications.ts: nombra la marca "${name}"; la correspondencia vive en brands.ts (familyId)`,
  );
}

/* -- 5. El sitio construido no nombra a ninguna marca retirada ------------ */

/**
 * Marcas que estuvieron publicadas y ya no lo están. Se comprueban por nombre
 * sobre el texto visible: una marca retirada puede volver por una plantilla
 * antigua, por un dato olvidado o por una imagen, y aquí se ve.
 */
const RETIRED = ['Ollital', 'CRTOP', 'ollital', 'crtop'];

const dist = join(root, 'dist');
if (!existsSync(dist)) {
  console.log('  (dist/ no existe: no se comprueba el HTML construido)');
} else {
  const pages = walk(dist, '.html').filter((file) => !relative(dist, file).startsWith('email/'));
  assert(pages.length > 0, 'dist: no hay páginas que comprobar');

  for (const file of pages) {
    const rel = relative(dist, file);
    const html = readFileSync(file, 'utf8');
    for (const retired of RETIRED) {
      assert(!html.includes(retired), `dist/${rel}: nombra a la marca retirada "${retired}"`);
    }
  }

  // Ningún activo de una marca retirada llega a producción.
  for (const asset of readdirSync(join(dist, 'brands'))) {
    assert(
      expectedLogos.has(asset),
      `dist/brands/${asset}: activo de una marca que ya no se publica`,
    );
  }

  // Las seis aprobadas sí aparecen: una lista cerrada que además está completa.
  const marcasPage = join(dist, 'marcas', 'index.html');
  if (existsSync(marcasPage)) {
    const html = readFileSync(marcasPage, 'utf8');
    for (const id of approved) {
      assert(
        html.includes(EXPECTED_NAMES[id]),
        `dist/marcas/index.html: no nombra a "${EXPECTED_NAMES[id]}"`,
      );
    }
  }

  /* -- 6. Una página de marca por marca aprobada, y ninguna más ---------- */

  const slugs = brandBlocks
    .map((block) => block.match(/slug: '([^']+)'/)?.[1])
    .filter(Boolean);
  assert(slugs.length === approved.length, 'brands.ts: falta algún slug');
  const brandPageDir = join(dist, 'marcas');
  const builtBrandPages = readdirSync(brandPageDir, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name);
  for (const slug of slugs) {
    assert(
      builtBrandPages.includes(slug),
      `dist/marcas/${slug}/: falta la página de una marca aprobada`,
    );
  }
  for (const built of builtBrandPages) {
    assert(built === '_astro' || slugs.includes(built), `dist/marcas/${built}/: página de una marca que no está en la lista cerrada`);
  }

  /* -- 7. Sitemap: exactamente seis rutas de marca ----------------------- */

  const sitemapFiles = walk(dist, '.xml').filter((file) => /sitemap-\d+\.xml$/.test(file));
  const sitemapUrls = sitemapFiles.flatMap((file) =>
    [...readFileSync(file, 'utf8').matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => m[1]),
  );
  if (sitemapUrls.length > 0) {
    const brandRoutes = sitemapUrls.filter((url) => /\/marcas\/[^/]+\//.test(url));
    assert(
      brandRoutes.length === approved.length,
      `sitemap: ${brandRoutes.length} rutas de marca frente a ${approved.length} marcas aprobadas`,
    );
    for (const slug of slugs) {
      assert(
        brandRoutes.some((url) => url.endsWith(`/marcas/${slug}/`)),
        `sitemap: falta /marcas/${slug}/`,
      );
    }
  }

  /* -- 8. Destinos externos: sólo los seis fabricantes ------------------- */

  /**
   * `qa:interaction` ya lo comprueba en navegador, pero depende de Playwright.
   * Aquí se comprueba sobre el HTML, sin navegador, para que la puerta de la
   * lista cerrada no dependa de que el arnés visual esté disponible.
   */
  const ALLOWED_HOSTS = new Set([
    'www.hielscher.com',
    'ortoalresa.com',
    'www.ika.com',
    'adamequipment.com',
    // Löser no ofrece HTTPS: su servidor de 2005 rechaza el saludo TLS.
    'www.loeser-osmometer.de',
    'www.serva.de',
    'wa.me',
    'origenlab.cl',
    'www.w3.org',
  ]);
  const seenHosts = new Set();
  for (const file of pages) {
    const html = readFileSync(file, 'utf8');
    for (const match of html.matchAll(/(?:href|src)="(https?:\/\/[^"]+)"/g)) {
      try {
        seenHosts.add(new URL(match[1]).host);
      } catch {
        assert(false, `${relative(dist, file)}: URL externa mal formada ${match[1]}`);
      }
    }
  }
  for (const host of seenHosts) {
    assert(ALLOWED_HOSTS.has(host), `dist: destino externo fuera de la lista cerrada: ${host}`);
  }

  /* -- 9. Datos estructurados: ninguna marca ajena ----------------------- */

  for (const file of pages) {
    const rel = relative(dist, file);
    const html = readFileSync(file, 'utf8');
    for (const match of html.matchAll(
      /<script type="application\/ld\+json">([\s\S]*?)<\/script>/g,
    )) {
      for (const retired of RETIRED) {
        assert(!match[1].includes(retired), `dist/${rel}: datos estructurados nombran a "${retired}"`);
      }
    }
  }
}

if (failed) {
  console.error('\nvalidate:brands FALLÓ');
  process.exit(1);
}
console.log(`validate:brands OK (${approved.length} marcas: ${approved.join(', ')})`);
