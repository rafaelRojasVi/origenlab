import type { Modelo } from '../content.config';
import { modelsForBrand } from '../data/brandModels';
import { getProductsByBrandId } from './catalog';
import { turntableFor } from './turntable';

/**
 * Modelos de una marca para `/marcas/` y `/marcas/<slug>/`, desde la colección
 * `modelos` (cada entrada tiene ficha). Orden: el de `brandModels.ts` (la
 * escalera de Hielscher, la tarea de OHAUS…) y luego el de `products.ts`
 * (centrífugas, consumible SERVA); lo que no esté en ninguno, por nombre.
 */
export function brandEntries(brandId: string, all: readonly Modelo[]): Modelo[] {
  const order = [...modelsForBrand(brandId).map((model) => model.id), ...getProductsByBrandId(brandId).map((product) => product.id)];
  const rank = (entry: Modelo) => {
    const at = order.indexOf(entry.slug);
    return at < 0 ? order.length : at;
  };
  return all
    .filter((entry) => entry.brand === brandId)
    .sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name, 'es', { numeric: true }));
}

/** El modelo tiene póster 3D (y, si existe, tira de giro). */
export const has3d = (entry: Modelo) => turntableFor(entry.slug) !== null;

/** Los `count` que encabezan una marca en el índice: primero los que giran en 3D. */
export function featuredEntries(entries: readonly Modelo[], count = 3): Modelo[] {
  return [...entries.filter(has3d), ...entries.filter((entry) => !has3d(entry))].slice(0, count);
}
