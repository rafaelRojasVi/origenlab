import type { Product } from '../data/products';
import {
  PRODUCT_IMAGE_WIDTHS,
  imageForProduct,
  type ProductImageRecord,
} from '../data/productImages';

export { PRODUCT_IMAGE_WIDTHS };

export interface ProductImageSources {
  /** Original del fabricante; respaldo del elemento <img>. */
  src: string;
  avifSrcset: string;
  webpSrcset: string;
  alt: string;
  /** Dimensiones intrínsecas del derivado mayor: lo que declara `<img>`. */
  width: number;
  height: number;
  /** Modelo o familia representativa. Decide el rótulo. */
  scope: ProductImageRecord['scope'];
  model: string;
  record: ProductImageRecord;
}

function largestDerivative(record: ProductImageRecord) {
  const widest = Math.max(...PRODUCT_IMAGE_WIDTHS);
  return record.derivatives?.[widest] ?? null;
}

/**
 * Fuentes responsivas de una fila del registro de imágenes. Sólo devuelve algo
 * para filas `VERIFIED` con original y derivados declarados: cualquier otra
 * cosa no es una imagen publicable y la plantilla compone sin ella.
 */
export function imageSourcesFor(record: ProductImageRecord | undefined): ProductImageSources | undefined {
  if (!record || record.status !== 'VERIFIED' || !record.masterPath) return undefined;
  const largest = largestDerivative(record);
  if (!largest) return undefined;
  const base = record.masterPath.replace(/\.(avif|jpe?g|png|webp)$/, '');
  const build = (ext: 'avif' | 'webp') =>
    PRODUCT_IMAGE_WIDTHS.map((width) => `${base}-${width}.${ext} ${width}w`).join(', ');
  return {
    src: record.masterPath,
    avifSrcset: build('avif'),
    webpSrcset: build('webp'),
    alt: record.alt,
    width: largest.width,
    height: largest.height,
    scope: record.scope,
    model: record.model,
    record,
  };
}

/**
 * Fuentes responsivas de la fotografía de un producto con ficha propia. La
 * imagen sale del registro, no de `product.imagePath`: así un producto no
 * puede publicar una fotografía que el registro no respalda.
 */
export function productImageSources(product: Product): ProductImageSources | undefined {
  return imageSourcesFor(imageForProduct(product.id));
}
