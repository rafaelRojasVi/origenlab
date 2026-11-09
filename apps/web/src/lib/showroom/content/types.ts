/**
 * Lo que la página dice de un equipo modelado: nombre, fotografía de respaldo,
 * vistas, marcadores y el rótulo del botón de marcha.
 *
 * Regla de las cifras: ningún texto de aquí escribe una cifra a mano. Cada
 * valor sale de los criterios con fuente del modelo (`brandModels.ts`) o de la
 * ficha del producto (`products.ts`) mediante `criterion` o `spec`; si el dato
 * no está, el marcador no lo menciona.
 */
export interface ShowroomEntry {
  /** Igual al nombre del archivo en `models/`. */
  id: string;
  brand: string;
  name: string;
  /** Tipo de equipo, para el pie. */
  kind: string;
  /** Fila VERIFIED de `productImages.ts` que se muestra sin 3D. */
  photo: { modelId: string } | { productId: string };
  /** Rótulos del botón de marcha; sin él, el equipo no tiene botón. */
  power?: { on: string; off: string };
  /** La primera es la general. */
  views: { id: string; label: string }[];
  hotspots: { anchor: string; view: string; title: string; body: string[] }[];
}
