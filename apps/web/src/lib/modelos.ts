/**
 * Lo que las páginas de modelo y de familia leen de la colección `modelos`
 * (`src/content/modelos/*.json`), en un solo lugar: a qué familia pertenece
 * cada equipo, dónde vive su página, qué fotografía la ilustra y qué modelos
 * se le muestran al lado.
 *
 * Centrifugación es la excepción deliberada. Sus cinco modelos Ortoalresa ya
 * tienen fichas propias, más completas, en `/productos/centrifugas/` (la URL
 * que el plan del sitio conserva), así que la ruta `[familia]/[modelo]` no las
 * duplica: `modelHref` y `familyHref` apuntan ahí y las páginas de colección
 * se generan para las otras cinco familias.
 */
import type { Modelo } from '../content.config';
import { equipmentScope } from '../data/equipmentScope';
import { products } from '../data/products';
import { imageForModel, imageForProduct, type ProductImageRecord } from '../data/productImages';
import { productPageHref } from './catalog';

export type FamilySlug = Modelo['family'];

export interface FamilyInfo {
  slug: FamilySlug;
  /** Id de `equipmentScope.ts`, el mismo que `brand.familyId`. */
  scopeId: string;
  /** Nombre completo de la familia (`equipmentScope.ts`). */
  name: string;
  /** Forma corta, para el supratítulo y el título de la página. */
  short: string;
  purpose: string;
  question: string;
}

const SHORT: Record<FamilySlug, { scopeId: string; short: string }> = {
  sonicacion: { scopeId: 'sonicacion', short: 'Sonicación' },
  dispersion: { scopeId: 'dispersion-homogeneizacion', short: 'Dispersión' },
  'pesaje-humedad': { scopeId: 'pesaje-humedad', short: 'Pesaje y humedad' },
  osmometria: { scopeId: 'osmometria', short: 'Osmometría' },
  electroforesis: { scopeId: 'electroforesis', short: 'Electroforesis' },
  centrifugacion: { scopeId: 'centrifugacion', short: 'Centrifugación' },
};

export function familyInfo(slug: FamilySlug): FamilyInfo {
  const { scopeId, short } = SHORT[slug];
  const scope = equipmentScope.find((entry) => entry.id === scopeId);
  if (!scope) throw new Error(`modelos: la familia «${slug}» no tiene alcance en equipmentScope.ts (${scopeId})`);
  return { slug, scopeId, name: scope.name, short, purpose: scope.purpose, question: scope.question };
}

/** Slug de familia de la colección para un id de `equipmentScope.ts`. */
export function familyForScope(scopeId: string): FamilySlug | undefined {
  return (Object.keys(SHORT) as FamilySlug[]).find((slug) => SHORT[slug].scopeId === scopeId);
}

/** Familias con páginas generadas desde la colección (todas menos centrifugación). */
export const hasCollectionPages = (slug: FamilySlug) => slug !== 'centrifugacion';

/** La ficha existente de una centrífuga Ortoalresa (`products.ts`), por id. */
function legacyProduct(entry: Modelo) {
  return products.find((product) => product.id === entry.slug);
}

export function familyHref(slug: FamilySlug): string {
  return hasCollectionPages(slug) ? `/productos/${slug}/` : '/productos/centrifugas/';
}

/** Página del modelo, con barra final. */
export function modelHref(entry: Modelo): string {
  if (!hasCollectionPages(entry.family)) {
    const product = legacyProduct(entry);
    const href = product && productPageHref(product);
    if (href) return href;
    return familyHref(entry.family);
  }
  return `/productos/${entry.family}/${entry.slug}/`;
}

/**
 * Página de cada modelo de una marca, por slug de la ficha (el mismo id que en
 * `brandModels.ts` o en `products.ts`), para enlazarla desde `/marcas/…`.
 */
export function modelHrefsForBrand(brandId: string, all: readonly Modelo[]): Record<string, string> {
  return Object.fromEntries(all.filter((entry) => entry.brand === brandId).map((entry) => [entry.slug, modelHref(entry)]));
}

/**
 * Fotografía VERIFIED del modelo. `image_id` es el id de la fila del registro;
 * los modelos documentados la tienen como `modelId` y las centrífugas y el
 * pulverizador SERVA, que tienen ficha en `products.ts`, como `productId`.
 */
export function imageForEntry(entry: Modelo): ProductImageRecord | undefined {
  const id = entry.image_id ?? entry.slug;
  return imageForModel(id) ?? imageForProduct(id);
}

/**
 * Modelos que se muestran junto a uno: primero los que la ficha nombra en
 * `related`, después los de su familia, hasta `limit`. El relleno de familia
 * empieza en el modelo que sigue a éste y da la vuelta, para que cada modelo
 * reciba al menos un enlace aunque la familia tenga más de `limit + 1`.
 */
export function relatedFor(entry: Modelo, all: readonly Modelo[], limit = 4): Modelo[] {
  const named = entry.related
    .map((slug) => all.find((other) => other.slug === slug))
    .filter((other): other is Modelo => Boolean(other));
  const members = all.filter((other) => other.family === entry.family);
  const at = members.findIndex((other) => other.slug === entry.slug);
  const family = [...members.slice(at + 1), ...members.slice(0, Math.max(at, 0))].filter((other) => !named.includes(other));
  return [...named, ...family].slice(0, limit);
}

/**
 * Cifras de cabecera: las primeras especificaciones con un número, en el orden
 * de la ficha. Son datos del fabricante con fuente, no cifras comerciales.
 */
export function keyStats(entry: Modelo, count = 3): Modelo['specs'] {
  return entry.specs.filter((spec) => /\d/.test(spec.value)).slice(0, count);
}

/** Orden estable de una lista de modelos: marca y nombre. */
export function byBrandAndName(a: Modelo, b: Modelo): number {
  return a.brand.localeCompare(b.brand) || a.name.localeCompare(b.name, 'es', { numeric: true });
}
