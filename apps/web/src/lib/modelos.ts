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
import { isShownOnHome } from '../data/brands';
import { equipmentScope } from '../data/equipmentScope';
import { products } from '../data/products';
import { imageForModel, imageForProduct, type ProductImageRecord } from '../data/productImages';
import { buyingFaqs } from '../data/faq';
import { productPageHref } from './catalog';
import type { ProductStoryConfig } from './showroom/productStory';

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

/**
 * `verb` y `cardLine` son el texto de las tarjetas de la portada (tablero HomeA):
 * lo que el laboratorio hace con la muestra, no lo que el equipo es.
 */
const SHORT: Record<FamilySlug, { scopeId: string; short: string; verb: string; cardLine: string }> = {
  sonicacion: {
    scopeId: 'sonicacion',
    short: 'Sonicación',
    verb: 'Sonicar',
    cardLine: 'Rompe células y extrae compuestos con ultrasonido de potencia controlada.',
  },
  dispersion: {
    scopeId: 'dispersion-homogeneizacion',
    short: 'Dispersión',
    verb: 'Dispersar',
    cardLine: 'Homogeneiza y emulsiona la muestra antes del análisis.',
  },
  'pesaje-humedad': {
    scopeId: 'pesaje-humedad',
    short: 'Pesaje y humedad',
    verb: 'Pesar',
    cardLine: 'Masa y humedad de la muestra, de la balanza a la termobalanza.',
  },
  osmometria: {
    scopeId: 'osmometria',
    short: 'Osmometría',
    verb: 'Medir osmolalidad',
    cardLine: 'Cuantifica la concentración de solutos de una muestra líquida.',
  },
  electroforesis: {
    scopeId: 'electroforesis',
    short: 'Electroforesis',
    verb: 'Separar',
    cardLine: 'Separa proteínas y ácidos nucleicos por tamaño y carga.',
  },
  centrifugacion: {
    scopeId: 'centrifugacion',
    short: 'Centrifugación',
    verb: 'Centrifugar',
    cardLine: 'Separa fases y sedimenta sólidos con rotor y velocidad a medida.',
  },
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

export interface HomeFamilyCard {
  slug: FamilySlug;
  short: string;
  verb: string;
  cardLine: string;
  /** Nombre de la propiedad CSS con la tinta de la familia (`--color-family-*`). */
  colorVar: string;
  /** Índice de dos cifras, en el orden de `equipmentScope`. */
  numeral: string;
  href: string;
  /** Hasta dos nombres de modelo, tal como están en la colección. */
  models: string[];
}

/**
 * Tarjetas de familia de la portada, en el orden de `equipmentScope`. Los
 * modelos que nombra cada una salen de la colección: los de marcas ocultas en
 * la portada no cuentan, y los que tienen sala 3D (`showroom_id`) van primero.
 */
export function homeFamilyCards(all: readonly Modelo[]): HomeFamilyCard[] {
  return equipmentScope.map((scope, index) => {
    const slug = familyForScope(scope.id);
    if (!slug) throw new Error(`modelos: el alcance «${scope.id}» no tiene familia en la colección`);
    const { short, verb, cardLine } = SHORT[slug];
    const models = all
      .filter((entry) => entry.family === slug && isShownOnHome(entry.brand))
      .sort((a, b) => Number(Boolean(b.showroom_id)) - Number(Boolean(a.showroom_id)) || byBrandAndName(a, b))
      .slice(0, 2)
      .map((entry) => entry.name);
    return {
      slug,
      short,
      verb,
      cardLine,
      colorVar: `--color-family-${slug === 'pesaje-humedad' ? 'pesaje' : slug}`,
      numeral: String(index + 1).padStart(2, '0'),
      href: familyHref(slug),
      models,
    };
  });
}

/**
 * Aplicaciones, accesorios y preguntas de una página de modelo. Los de la
 * historia por scroll mandan cuando traen contenido; si no, los de la ficha.
 * Las preguntas son las del modelo, las de la historia y las de compra
 * (`buyingFaqs`): la misma lista para la sección y para el JSON-LD.
 */
export function modelSections(
  entry: Modelo,
  story?: Pick<ProductStoryConfig, 'applications' | 'accessories' | 'faqs'>,
): { applications: string[]; accessories: { name: string; note?: string }[]; faqs: { q: string; a: string }[] } {
  return {
    applications: story?.applications.length ? story.applications : entry.applications,
    accessories: story?.accessories.length ? story.accessories : entry.accessories.map((name) => ({ name })),
    faqs: [...entry.faqs, ...(story?.faqs ?? []), ...buyingFaqs()],
  };
}

/** Cifra de la ficha que ordena la escala de cada familia; las demás no tienen escala. */
export const SCALE_SPEC: Partial<Record<FamilySlug, string>> = {
  sonicacion: 'Potencia',
  dispersion: 'Potencia',
  centrifugacion: 'Velocidad máxima',
};

/**
 * Primera cifra de un valor de la ficha: `n` es el número (puntos de millar
 * fuera, coma decimal como punto) y `display` el número con su unidad tal como
 * está escrito. `'125 W de entrada, 75 W de salida'` da `125 W`.
 */
export function parseSpecNumber(value: string): { n: number; display: string } | null {
  const match = value.match(/(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d+))?\s*([^\s,;/]+)?/);
  if (!match) return null;
  const n = Number(`${match[1].replace(/\./g, '')}${match[2] ? `.${match[2]}` : ''}`);
  return { n, display: match[0].trim() };
}

export interface FamilyScalePoint {
  slug: string;
  name: string;
  display: string;
  /** Posición 0–1 sobre la escala. */
  x: number;
}

/**
 * Escala de comparación de una familia, a partir de la cifra `SCALE_SPEC` de sus
 * modelos. `null` si la familia no tiene escala o menos de tres modelos traen la
 * cifra. La escala es logarítmica cuando el rango abarca más de 10 veces (100 W a
 * 2.000 W no cabría en una recta) y lineal si no.
 */
export function familyScale(
  models: readonly Modelo[],
  slug: FamilySlug,
): { label: string; points: FamilyScalePoint[] } | null {
  const label = SCALE_SPEC[slug];
  if (!label) return null;
  const read = models
    .map((model) => {
      const spec = model.specs.find((item) => item.label === label);
      const parsed = spec && parseSpecNumber(spec.value);
      return parsed ? { model, ...parsed } : null;
    })
    .filter((item): item is NonNullable<typeof item> => item !== null)
    .sort((a, b) => a.n - b.n);
  if (read.length < 3) return null;
  const min = read[0].n;
  const max = read[read.length - 1].n;
  const log = min > 0 && max / min > 10;
  const at = (n: number) => (log ? Math.log(n) - Math.log(min) : n - min) / (log ? Math.log(max) - Math.log(min) : max - min);
  return {
    label,
    points: read.map(({ model, n, display }) => ({
      slug: model.slug,
      name: model.name,
      display,
      x: max === min ? 0.5 : at(n),
    })),
  };
}
