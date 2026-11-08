import type { Modelo } from '../content.config';
import { turntableFor } from './turntable';
import { rankModels } from './ranking';

/** Modelos de la colección en el orden comercial del sitio (`ranking.ts`). */
export function rankEntries(entries: readonly Modelo[]): Modelo[] {
  return rankModels(entries.map((entry) => ({ id: entry.slug, entry }))).map(({ entry }) => entry);
}

/**
 * Modelos de una marca para `/marcas/`, `/marcas/<slug>/` y el menú, desde la
 * colección `modelos` (cada entrada tiene ficha), en el orden comercial: los
 * que tienen ficha 3D primero, luego por demanda (`rankModels`).
 */
export function brandEntries(brandId: string, all: readonly Modelo[]): Modelo[] {
  return rankEntries(all.filter((entry) => entry.brand === brandId));
}

/** El modelo tiene póster 3D (y, si existe, tira de giro). */
export const has3d = (entry: Modelo) => turntableFor(entry.slug) !== null;

/** Los `count` que encabezan una marca en el índice: primero los que giran en 3D. */
export function featuredEntries(entries: readonly Modelo[], count = 3): Modelo[] {
  return [...entries.filter(has3d), ...entries.filter((entry) => !has3d(entry))].slice(0, count);
}
