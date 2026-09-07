/**
 * Alcance de equipamiento: qué familias puede cotizar OrigenLab.
 *
 * Seis familias, una por marca aprobada. Es distinto de `productFamilies.ts`,
 * que sólo describe las familias con ficha y fotografía propias en el sitio.
 * Aquí se declara el alcance completo, con la procedencia de cada nivel
 * explícita, porque el sitio publica cosas distintas en cada uno:
 *
 * - `catalogo` — hay productos en `products.ts` con ficha propia en
 *   origenlab.cl: fotografía con procedencia, especificaciones agrupadas y PDF
 *   del fabricante. Se puede enlazar a una página de detalle interna.
 * - `documentada` — hay modelos verificados en `brandModels.ts`: qué hace el
 *   equipo, para qué se usa, qué decide la elección y el enlace a la página y
 *   al PDF del fabricante. **No** hay fotografía con permiso ni ficha interna,
 *   y por eso el destino es la página de marca y no una ficha de producto.
 *
 * El nivel anterior se llamaba `consulta` y no podía nombrar un solo modelo,
 * porque en su momento no había ninguna fuente verificada. Ahora la hay: los
 * dieciocho modelos y familias de `brandModels.ts` se leyeron de la página o del
 * PDF del propio fabricante el 2026-09-07, con la URL comprobada. Lo que sigue
 * prohibido en este nivel, y `validate:catalog` comprueba, es lo que de verdad
 * no está confirmado: fotografía de producto, cifra comercial sin aprobar y
 * condición de venta.
 *
 * Procedencia de la correspondencia marca-familia: revisión de marca y portada
 * del 2026-09-06. Vive en `brands.ts` (`familyId`), en una sola dirección: si
 * `equipmentScope.ts` nombrara marcas habría dos listas que sincronizar.
 */

export type ScopeTier = 'catalogo' | 'documentada';

export interface EquipmentScopeEntry {
  id: string;
  /** Nombre de la familia, en el lenguaje del laboratorio. */
  name: string;
  tier: ScopeTier;
  /** Qué resuelve la familia. Genérico a propósito: sin modelos ni cifras. */
  purpose: string;
  /**
   * La pregunta que el cliente suele traer. Es lo que abre la conversación
   * técnica y ordena la ficha de cada modelo.
   */
  question: string;
  /**
   * Tipo de trabajo, no etapa de un proceso. Agrupa la portada sin sugerir
   * que exista una secuencia única: un laboratorio puede usar sólo una de las
   * seis, o combinarlas en el orden que pida su método.
   */
  workType: string;
  /** Destino de la familia: ficha interna en `catalogo`, marca en el resto. */
  href: string;
  /** Sólo en `catalogo`: id de la afirmación con el número de referencias. */
  countClaimId?: string;
  /** Sólo en `catalogo`: slug del producto cuya fotografía ilustra la familia. */
  imageProductSlug?: string;
}

export const equipmentScope: readonly EquipmentScopeEntry[] = [
  {
    id: 'pesaje-humedad',
    name: 'Pesaje y análisis de humedad',
    tier: 'documentada',
    workType: 'Medición',
    purpose:
      'Determinación de masa y de contenido de humedad en control de calidad y en análisis de rutina.',
    question: '¿Qué precisión y qué resolución exige el método que aplico?',
    href: '/marcas/adam-equipment/',
  },
  {
    id: 'dispersion-homogeneizacion',
    name: 'Dispersión y homogeneización',
    tier: 'documentada',
    workType: 'Preparación de muestra',
    purpose:
      'Mezcla, dispersión y homogeneización de muestras y preparaciones, en laboratorio y en trabajo de proceso.',
    question: '¿Qué equipo se adapta a mi flujo de trabajo y a mi volumen por lote?',
    href: '/marcas/ika/',
  },
  {
    id: 'sonicacion',
    name: 'Sonicación y procesamiento ultrasónico',
    tier: 'documentada',
    workType: 'Preparación de muestra',
    purpose:
      'Lisis, extracción, desgasificación y procesamiento de muestras por ultrasonido.',
    question: '¿Qué sonda corresponde a mi volumen y a mi tipo de muestra?',
    href: '/marcas/hielscher/',
  },
  {
    id: 'centrifugacion',
    name: 'Centrifugación y separación',
    tier: 'catalogo',
    workType: 'Separación',
    purpose:
      'Separación y preparación de muestras en microtubos, tubos cónicos, microplacas y volúmenes mayores, con versiones ventiladas y refrigeradas.',
    question: '¿Qué capacidad y qué rotor corresponden a mi volumen de trabajo?',
    href: '/productos/centrifugas/',
    countClaimId: 'modelos-centrifuga-publicados',
    imageProductSlug: 'digicen-22-r',
  },
  {
    id: 'osmometria',
    name: 'Osmometría',
    tier: 'documentada',
    workType: 'Medición',
    purpose:
      'Determinación de osmolalidad por descenso crioscópico en laboratorio clínico y de investigación.',
    question: '¿Qué volumen de muestra puedo destinar a cada medición?',
    href: '/marcas/loeser-messtechnik/',
  },
  {
    id: 'electroforesis',
    name: 'Electroforesis',
    tier: 'catalogo',
    workType: 'Análisis',
    purpose:
      'Cubetas, fuentes de alimentación, reactivos e insumos para electroforesis y para la preparación y el tratamiento de muestras.',
    question: '¿Qué formato de gel y qué fuente corresponden a mi protocolo?',
    href: '/marcas/serva-electrophoresis/',
    countClaimId: 'equipos-serva-documentados',
  },
];

export function scopeByTier(tier: ScopeTier): EquipmentScopeEntry[] {
  return equipmentScope.filter((entry) => entry.tier === tier);
}

export function scopeById(id: string): EquipmentScopeEntry | undefined {
  return equipmentScope.find((entry) => entry.id === id);
}

/**
 * Orden público de las seis familias en `/productos/`.
 *
 * No es el orden de este archivo ni el alfabético: es el que evita que
 * centrifugación, la única con fotografía, se coma la página. Abre con
 * preparación de muestra, deja separación en el centro y cierra con análisis.
 */
export const productsFamilyOrder = [
  'sonicacion',
  'dispersion-homogeneizacion',
  'centrifugacion',
  'pesaje-humedad',
  'osmometria',
  'electroforesis',
] as const;

/** Las seis familias en el orden de `/productos/`. */
export function familiesInProductOrder(): EquipmentScopeEntry[] {
  return productsFamilyOrder
    .map((id) => scopeById(id))
    .filter((entry): entry is EquipmentScopeEntry => entry !== undefined);
}

/**
 * Frase de alcance para el hero y para metadatos. Se deriva de los datos para
 * que ampliar el alcance no exija reescribir la portada a mano.
 */
export function scopeSummary(): string {
  return equipmentScope.map((entry) => entry.name.toLowerCase()).join(', ');
}
