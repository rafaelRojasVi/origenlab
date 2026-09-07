/**
 * Alcance de equipamiento: qué familias puede cotizar OrigenLab.
 *
 * Seis familias, una por marca aprobada. Es distinto de `productFamilies.ts`,
 * que sólo describe las familias con página y catálogo propios en el sitio.
 * Aquí se declara el alcance comercial completo, con la procedencia de cada
 * nivel explícita, porque el sitio publica cosas muy distintas en cada uno:
 *
 * - `catalogo`: hay productos en `products.ts` con ficha, imagen y
 *   documentación del fabricante. Se puede enlazar a una página de detalle.
 * - `consulta`: el negocio cotiza la familia y se sabe qué marca la fabrica,
 *   pero el repositorio no tiene modelos, especificaciones ni imágenes
 *   aprobadas. Se nombra la familia, se nombra la marca y se invita a describir
 *   la aplicación. **No** se listan modelos, capacidades ni rangos.
 *
 * Procedencia: revisión de marca y portada del 2026-09-06, en la que el negocio
 * fijó las seis marcas aprobadas y qué fabrica cada una. Hasta esa revisión el
 * repositorio tenía prohibido deducir que Hielscher hace ultrasonido o IKA
 * dispersión, porque nadie lo había confirmado; ahora está confirmado y la
 * asociación vive en `brands.ts` (`familyId`), en una sola dirección.
 *
 * Lo que sigue prohibido en el nivel `consulta`, y `validate:catalog` comprueba:
 * ninguna de estas familias puede declarar modelo, cifra ni fotografía.
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
  /**
   * Tipo de trabajo, no etapa de un proceso. Agrupa la portada sin sugerir
   * que exista una secuencia única: un laboratorio puede usar sólo una de las
   * seis, o combinarlas en el orden que pida su método.
   */
  workType: string;
  /** Sólo en `catalogo`: destino de la familia publicada. */
  href?: string;
  /** Sólo en `catalogo`: id de la afirmación con el número de referencias. */
  countClaimId?: string;
  /** Sólo en `catalogo`: slug del producto cuya fotografía ilustra la familia. */
  imageProductSlug?: string;
}

export const equipmentScope: readonly EquipmentScopeEntry[] = [
  {
    id: 'pesaje-humedad',
    name: 'Pesaje y análisis de humedad',
    tier: 'consulta',
    workType: 'Medición',
    purpose:
      'Determinación de masa y de contenido de humedad en control de calidad y en análisis de rutina.',
    question: '¿Qué precisión y qué resolución exige el método que aplico?',
  },
  {
    id: 'dispersion-homogeneizacion',
    name: 'Dispersión y homogeneización',
    tier: 'consulta',
    workType: 'Preparación de muestra',
    purpose:
      'Mezcla, dispersión y homogeneización de muestras y preparaciones, en laboratorio y en trabajo de proceso.',
    question: '¿Qué equipo se adapta a mi flujo de trabajo y a mi volumen por lote?',
  },
  {
    id: 'sonicacion',
    name: 'Sonicación y procesamiento ultrasónico',
    tier: 'consulta',
    workType: 'Preparación de muestra',
    purpose:
      'Lisis, extracción, desgasificación y procesamiento de muestras por ultrasonido.',
    question: '¿Qué sonda corresponde a mi volumen y a mi tipo de muestra?',
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
    tier: 'consulta',
    workType: 'Medición',
    purpose:
      'Determinación de osmolalidad por descenso crioscópico en laboratorio clínico y de investigación.',
    question: '¿Qué volumen de muestra puedo destinar a cada medición?',
  },
  {
    id: 'electroforesis',
    name: 'Electroforesis',
    tier: 'catalogo',
    workType: 'Análisis',
    purpose:
      'Reactivos, insumos y equipos para electroforesis y para la preparación y el tratamiento de muestras en laboratorio.',
    question: '¿Qué reactivo corresponde a mi protocolo y en qué presentación?',
    href: '/marcas/serva-electrophoresis/',
    countClaimId: 'referencias-serva-publicadas',
  },
];

export function scopeByTier(tier: ScopeTier): EquipmentScopeEntry[] {
  return equipmentScope.filter((entry) => entry.tier === tier);
}

export function scopeById(id: string): EquipmentScopeEntry | undefined {
  return equipmentScope.find((entry) => entry.id === id);
}

/**
 * Frase de alcance para el hero y para metadatos. Se deriva de los datos para
 * que ampliar el alcance no exija reescribir la portada a mano.
 */
export function scopeSummary(): string {
  return equipmentScope.map((entry) => entry.name.toLowerCase()).join(', ');
}
