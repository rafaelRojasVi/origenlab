/**
 * Alcance de equipamiento: qué familias puede cotizar OrigenLab.
 *
 * Es distinto de `productFamilies.ts`, que sólo describe las familias con
 * página y catálogo propios en el sitio. Aquí se declara el alcance comercial
 * completo, con la procedencia de cada nivel explícita, porque el sitio publica
 * cosas muy distintas en cada uno:
 *
 * - `catalogo`: hay productos en `products.ts` con ficha, imagen y
 *   documentación del fabricante. Se puede enlazar a una página de detalle.
 * - `consulta`: el negocio cotiza la familia, pero el repositorio no tiene
 *   modelos, especificaciones ni imágenes aprobadas. Se nombra la familia y se
 *   invita a describir la aplicación. **No** se listan modelos, marcas,
 *   capacidades ni rangos.
 *
 * Procedencia del nivel `consulta`: confirmado por el negocio en la revisión
 * de portada del 2026-09-06, como alcance de cotización. Esa confirmación no
 * alcanza para asociar una familia a una marca concreta: qué fabrica IKA,
 * Hielscher, Ollital o CRTOP sigue pendiente en
 * `docs/design/CONTENT_NEEDED.md` y ninguna plantilla debe deducirlo.
 */

export type ScopeTier = 'catalogo' | 'consulta';

export interface EquipmentScopeEntry {
  id: string;
  /** Nombre de la familia, en el lenguaje del laboratorio. */
  name: string;
  tier: ScopeTier;
  /** Qué resuelve la familia. Genérico a propósito: sin modelos ni cifras. */
  purpose: string;
  /**
   * La pregunta que el cliente suele traer. Es lo que abre la conversación
   * técnica y sustituye a la especificación que aquí no podemos publicar.
   */
  question: string;
  /** Sólo en `catalogo`: destino de la familia publicada. */
  href?: string;
  /** Sólo en `catalogo`: id de la afirmación con el número de referencias. */
  countClaimId?: string;
  /** Sólo en `catalogo`: slug del producto cuya fotografía ilustra la familia. */
  imageProductSlug?: string;
  /** Sólo en `catalogo`: marca cuyo logotipo acompaña a la familia. */
  brandSlug?: string;
}

export const equipmentScope: readonly EquipmentScopeEntry[] = [
  {
    id: 'centrifugacion',
    name: 'Centrifugación',
    tier: 'catalogo',
    purpose:
      'Separación y preparación de muestras en microtubos, tubos cónicos, microplacas y volúmenes mayores, con versiones ventiladas y refrigeradas.',
    question: '¿Qué capacidad y qué rotor corresponden a mi volumen de trabajo?',
    href: '/productos/centrifugas/',
    countClaimId: 'modelos-centrifuga-publicados',
    imageProductSlug: 'digicen-22-r',
    brandSlug: 'ortoalresa',
  },
  {
    id: 'electroforesis',
    name: 'Electroforesis',
    tier: 'catalogo',
    purpose:
      'Reactivos e insumos para electroforesis y para la preparación y el tratamiento de muestras en laboratorio.',
    question: '¿Qué reactivo corresponde a mi protocolo y en qué presentación?',
    href: '/marcas/serva-electrophoresis/',
    countClaimId: 'referencias-serva-publicadas',
    brandSlug: 'serva-electrophoresis',
  },
  {
    id: 'sonicacion',
    name: 'Sonicación y procesamiento ultrasónico',
    tier: 'consulta',
    purpose:
      'Lisis, extracción, desgasificación y procesamiento de muestras por ultrasonido.',
    question: '¿Qué sonda corresponde a mi volumen y a mi tipo de muestra?',
  },
  {
    id: 'dispersion-homogeneizacion',
    name: 'Dispersión y homogeneización',
    tier: 'consulta',
    purpose:
      'Mezcla, dispersión y homogeneización de muestras y preparaciones, en laboratorio y en trabajo de proceso.',
    question: '¿Qué equipo se adapta a mi flujo de trabajo y a mi volumen por lote?',
  },
  {
    id: 'pesaje-humedad',
    name: 'Pesaje y análisis de humedad',
    tier: 'consulta',
    purpose:
      'Determinación de masa y de contenido de humedad en control de calidad y en análisis de rutina.',
    question: '¿Qué precisión y qué resolución exige el método que aplico?',
  },
];

export function scopeByTier(tier: ScopeTier): EquipmentScopeEntry[] {
  return equipmentScope.filter((entry) => entry.tier === tier);
}

/**
 * Frase de alcance para el hero y para metadatos. Se deriva de los datos para
 * que ampliar el alcance no exija reescribir la portada a mano.
 */
export function scopeSummary(): string {
  return equipmentScope.map((entry) => entry.name.toLowerCase()).join(', ');
}
