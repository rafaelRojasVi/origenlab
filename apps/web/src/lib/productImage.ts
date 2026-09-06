import type { Product } from '../data/products';

/** Anchos derivados por scripts/build-product-images.mjs. */
export const PRODUCT_IMAGE_WIDTHS = [480, 960] as const;

export interface ProductImageSources {
  /** Original del fabricante; respaldo del elemento <img>. */
  src: string;
  avifSrcset: string;
  webpSrcset: string;
  alt: string;
}

/**
 * Construye las fuentes responsivas de una imagen de producto.
 * Las imágenes originales son cuadradas o casi cuadradas (recortes del
 * fabricante); el sitio siempre las presenta dentro de una caja 1:1 sobre papel,
 * de modo que la escala sea idéntica en toda la web.
 */
export function productImageSources(product: Product): ProductImageSources | undefined {
  if (!product.imagePath) return undefined;
  const base = product.imagePath.replace(/\.avif$/, '');
  const build = (ext: 'avif' | 'webp') =>
    PRODUCT_IMAGE_WIDTHS.map((width) => `${base}-${width}.${ext} ${width}w`).join(', ');
  return {
    src: product.imagePath,
    avifSrcset: build('avif'),
    webpSrcset: build('webp'),
    alt: product.imageAlt ?? `${product.name}, ${product.equipmentType ?? 'equipo de laboratorio'}`,
  };
}
