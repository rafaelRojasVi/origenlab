import { brands, type Brand } from '../data/brands';
import { products, type Product, type ProductSpec } from '../data/products';
import { getProductFamilyBySlug, ortoalresaCentrifugeSlugs } from '../data/productFamilies';
import {
  specGroupForLabel,
  specGroupNames,
  specGroupOrder,
  type SpecGroupId,
} from '../data/specGroups';

export function getBrandById(id: string): Brand | undefined {
  return brands.find((brand) => brand.id === id);
}

export function getBrandBySlug(slug: string): Brand | undefined {
  return brands.find((brand) => brand.slug === slug);
}

export function getProductBySlug(slug: string): Product | undefined {
  return products.find((product) => product.slug === slug);
}

export function getProductsByBrandId(brandId: string): Product[] {
  return products
    .filter((product) => product.brandId === brandId)
    .sort((a, b) => (a.catalogSortOrder ?? 999) - (b.catalogSortOrder ?? 999));
}

export function getProductsByFamilySlug(familySlug: string): Product[] {
  if (familySlug === 'centrifugas') {
    return getOrtoalresaCentrifuges();
  }
  return products
    .filter((product) => product.productFamilySlug === familySlug)
    .sort((a, b) => (a.catalogSortOrder ?? 999) - (b.catalogSortOrder ?? 999));
}

function centrifugesFromSlugs(slugs: readonly string[]): Product[] {
  return slugs
    .map((slug) => getProductByFamilyAndSlug('centrifugas', slug))
    .filter((product): product is Product => product !== undefined);
}

export function getOrtoalresaCentrifuges(): Product[] {
  return centrifugesFromSlugs(ortoalresaCentrifugeSlugs);
}

/** Alias del orden canónico (mismo que familia/marca). */
export function getOrtoalresaCentrifugesForHome(): Product[] {
  return getOrtoalresaCentrifuges();
}

export function getProductByFamilyAndSlug(familySlug: string, productSlug: string): Product | undefined {
  return products.find(
    (product) => product.productFamilySlug === familySlug && product.slug === productSlug,
  );
}

/** Con barra final, coherente con `trailingSlash: 'always'` y con la canónica. */
export function productPageHref(product: Product): string | undefined {
  if (!product.productFamilySlug) return undefined;
  return `/productos/${product.productFamilySlug}/${product.slug}/`;
}

export function productsForCategory(categorySlug: string): Product[] {
  return products.filter(
    (product) => product.categorySlugs?.includes(categorySlug) && product.showOnProductsPage,
  );
}

/** Equipos relacionados por categoría (evita duplicar las mismas fichas en todas las categorías). */
export function getCategoryRelatedProducts(categorySlug: string): Product[] {
  if (categorySlug === 'laboratorio-clinico') {
    return [];
  }
  if (categorySlug === 'control-de-calidad') {
    return getOrtoalresaCentrifuges().filter((product) => product.showOnProductsPage);
  }
  return productsForCategory(categorySlug);
}

export function validateProductFamilySlugs(): void {
  for (const product of products) {
    if (product.productFamilySlug && !getProductFamilyBySlug(product.productFamilySlug)) {
      throw new Error(
        `Product "${product.id}" references unknown family "${product.productFamilySlug}"`,
      );
    }
  }
}

/* -------------------------------------------------------------------------- */
/* Agrupación de especificaciones                                             */
/* -------------------------------------------------------------------------- */

export interface SpecGroup {
  id: SpecGroupId;
  name: string;
  specs: readonly ProductSpec[];
}

/**
 * Ordena `keySpecs` en los grupos declarados en `specGroups.ts`, preservando el
 * orden del fabricante dentro de cada grupo. Las etiquetas sin grupo se dejan
 * fuera y `validate:catalog` las bloquea antes de llegar a producción.
 */
export function groupProductSpecs(specs: readonly ProductSpec[]): SpecGroup[] {
  const buckets = new Map<SpecGroupId, ProductSpec[]>();
  for (const spec of specs) {
    const groupId = specGroupForLabel(spec.label);
    if (!groupId) continue;
    const bucket = buckets.get(groupId);
    if (bucket) bucket.push(spec);
    else buckets.set(groupId, [spec]);
  }
  return specGroupOrder
    .filter((id) => (buckets.get(id)?.length ?? 0) > 0)
    .map((id) => ({ id, name: specGroupNames[id], specs: buckets.get(id) ?? [] }));
}

/**
 * Datos de cabecera de una ficha: pocas cifras, en el orden en que se leen.
 *
 * No incluye «Versión» a propósito: repite palabra por palabra el tipo de equipo
 * que ya aparece junto al nombre («Microcentrífuga ventilada» / «Ventilada»).
 * Un modelo sin control de temperatura muestra dos cifras, no tres rellenas.
 */
export function keyFactsFor(product: Product): ProductSpec[] {
  const wanted = ['Capacidad máxima', 'Velocidad máxima', 'Temperatura', 'Pantalla'];
  const found = wanted
    .map((label) => product.keySpecs?.find((spec) => spec.label === label))
    .filter((spec): spec is ProductSpec => spec !== undefined);
  return found.slice(0, 4);
}

/** Marcas con catálogo publicado (tienen página propia). */
export function getCatalogBrands(): Brand[] {
  return brands.filter((brand) => brand.catalogPublished);
}

/** Marcas sin catálogo publicado: logotipo, familia y enlace al fabricante. */
export function getWorkingBrands(): Brand[] {
  return brands.filter((brand) => !brand.catalogPublished);
}

/**
 * Marca que fabrica una familia de equipo.
 *
 * La correspondencia se declara una sola vez, en `brands.ts` (`familyId`), y en
 * una sola dirección. `equipmentScope.ts` no nombra marcas: si lo hiciera,
 * habría dos listas que mantener sincronizadas y la portada podría atribuir una
 * familia a una marca que el negocio no ha confirmado.
 */
export function getBrandByFamilyId(familyId: string): Brand | undefined {
  return brands.find((brand) => brand.familyId === familyId);
}
