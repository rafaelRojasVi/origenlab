/**
 * Orden comercial del sitio (dueño, 2026-11-08): primero los modelos con ficha 3D, después el
 * resto según cuánto se cotizan; IKA al final en todas partes. Solo orden, sin cifras: la
 * demanda real vive fuera del repositorio. Todo listado de modelos, marcas o familias (catálogo,
 * familias, marcas, aplicaciones, menú, relacionados, portada) pasa por estas funciones.
 * Lo que no está en una lista va después, en su orden original.
 */
export const MODEL_ORDER = [
  // Con ficha 3D
  'hielscher-up400st',
  'hielscher-up200st',
  'hielscher-up100h',
  'ohaus-pioneer-px',
  'ohaus-mb120',
  'ohaus-mb32',
  'loeser-i-osmometer',
  'ortoalresa-digicen-22-r',
  'adam-highland',
  'serva-bluevertical-prime',
  // Sin 3D, por demanda
  'hielscher-uip2000hdt',
  'adam-pmb',
  'loeser-i-osmometer-basic',
  'loeser-osmometer-basic',
  'loeser-i-cryometer',
  'ortoalresa-digicen-22',
  'ortoalresa-biocen-22-r',
  'ortoalresa-biocen-22',
  'ortoalresa-consul-22',
  'ohaus-ranger-count-3000',
  'adam-solis',
  'serva-bluepower',
  'serva-bluemarine-100',
  'serva-blueslick-42500',
  'serva-hpe-bluehorizon',
  // IKA al final
  'ika-t25-digital',
  'ika-t10-basic',
  'ika-t18-digital',
] as const;

export const BRAND_ORDER = [
  'hielscher',
  'ohaus',
  'ortoalresa',
  'loeser-messtechnik',
  'adam-equipment',
  'serva-electrophoresis',
  'ika',
] as const;

/** Familias por su segmento de URL en /productos/<familia>/ (dispersión, IKA, al final). */
export const FAMILY_ORDER = [
  'sonicacion',
  'pesaje-humedad',
  'centrifugas',
  'osmometria',
  'electroforesis',
  'dispersion',
] as const;

function byList<T>(list: readonly string[], key: (item: T) => string) {
  return (a: T, b: T): number => {
    const ia = list.indexOf(key(a));
    const ib = list.indexOf(key(b));
    return (ia === -1 ? list.length : ia) - (ib === -1 ? list.length : ib);
  };
}

/** Copia ordenada (sort estable: lo que no está en la lista conserva su orden). */
export function rankModels<T extends { id: string }>(items: readonly T[]): T[] {
  return [...items].sort(byList<T>(MODEL_ORDER, (p) => p.id));
}

export function rankBrands<T extends { slug: string }>(items: readonly T[]): T[] {
  return [...items].sort(byList<T>(BRAND_ORDER, (b) => b.slug));
}

export function rankFamilies<T>(items: readonly T[], urlSegment: (f: T) => string): T[] {
  return [...items].sort(byList<T>(FAMILY_ORDER, urlSegment));
}
