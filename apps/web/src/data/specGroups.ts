/**
 * Agrupación de especificaciones para las fichas de producto.
 *
 * Las especificaciones vienen de la documentación del fabricante
 * (`products.ts` → `keySpecs`). Este archivo sólo decide **en qué grupo se
 * muestra cada etiqueta**: no crea, traduce ni reinterpreta valores.
 *
 * Regla: toda etiqueta presente en `products.ts` debe tener grupo aquí.
 * `npm run validate:catalog` falla si aparece una etiqueta sin clasificar, de
 * modo que una ficha nueva no puede publicarse con especificaciones sueltas.
 */
export type SpecGroupId = 'rendimiento' | 'construccion' | 'instalacion';

export const specGroupOrder: readonly SpecGroupId[] = [
  'rendimiento',
  'construccion',
  'instalacion',
];

export const specGroupNames: Record<SpecGroupId, string> = {
  rendimiento: 'Rendimiento',
  construccion: 'Construcción y control',
  instalacion: 'Instalación',
};

/**
 * Etiqueta exacta de `keySpecs` → grupo. Las etiquetas con paréntesis variables
 * (códigos CE por modelo) se resuelven por prefijo en `specGroupForLabel`.
 */
const EXACT: Record<string, SpecGroupId> = {
  'Capacidad máxima': 'rendimiento',
  'Velocidad máxima': 'rendimiento',
  Versión: 'rendimiento',
  Temperatura: 'rendimiento',
  'Temperatura a máximas RPM': 'rendimiento',
  'Pre-enfriamiento': 'rendimiento',
  Rotores: 'rendimiento',

  Pantalla: 'construccion',
  Cámara: 'construccion',
  Motor: 'construccion',
  Rotor: 'construccion',
  Control: 'construccion',
  Seguridad: 'construccion',
  Sensor: 'construccion',
  Refrigerante: 'construccion',
  Accesorios: 'construccion',
  Conectividad: 'construccion',
  'Nivel de ruido': 'construccion',
  'Aceleración / frenado': 'construccion',

  'Peso neto': 'instalacion',

  /*
   * Consumibles. Un reactivo no tiene rendimiento ni instalación en el sentido
   * de un equipo, pero sí presentación, dosis y condiciones de guarda, y eso es
   * lo que decide una compra. Se reparten entre los mismos tres grupos en vez
   * de abrir un cuarto: tres grupos ya ordenan la lectura y un cuarto obligaría
   * a rehacer el orden de todas las fichas.
   */
  Presentación: 'construccion',
  Dosis: 'rendimiento',
  'Duración del tratamiento': 'rendimiento',
  'Temperatura de almacenamiento': 'instalacion',
};

/** Prefijos para etiquetas que incluyen el código de modelo del fabricante. */
const PREFIXES: readonly (readonly [string, SpecGroupId])[] = [
  ['Dimensiones', 'instalacion'],
  ['Alimentación', 'instalacion'],
];

export function specGroupForLabel(label: string): SpecGroupId | undefined {
  const exact = EXACT[label];
  if (exact) return exact;
  for (const [prefix, group] of PREFIXES) {
    if (label.startsWith(prefix)) return group;
  }
  return undefined;
}
