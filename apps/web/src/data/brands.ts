/**
 * Marcas publicadas. Lista cerrada de seis.
 *
 * `APPROVED_BRAND_IDS` es la única lista de marcas que el sitio público puede
 * mostrar. La comparte todo: portada, páginas de marca, navegación, catálogo,
 * buscador, sitemap, datos estructurados y pie. `validate:brands` comprueba que
 * este archivo, los logotipos de `public/brands/`, el registro de fuentes y el
 * HTML construido coincidan exactamente con ella, de modo que una marca
 * retirada no pueda reaparecer sin que falle la validación.
 *
 * Procedencia de la lista y de la familia de cada marca: revisión de marca y
 * portada del 2026-09-06, en la que el negocio fijó las seis marcas aprobadas y
 * qué fabrica cada una. Esa confirmación resuelve la pregunta que quedaba
 * abierta en `docs/design/CONTENT_NEEDED.md` («qué familia fabrica cada marca
 * sin catálogo») y es lo que permite, por primera vez, asociar una familia de
 * equipo a una marca concreta.
 *
 * Lo que esa confirmación **no** cubre, y por tanto sigue sin publicarse:
 *
 * - El alcance comercial por marca. Ninguna marca se describe como
 *   representación oficial, distribución exclusiva, territorial ni
 *   certificación. `validate:catalog` bloquea esa redacción.
 * - Modelos, especificaciones y precios de las marcas sin catálogo propio.
 * - Fotografía de producto sin procedencia y permiso documentados: ver
 *   `src/data/sourceRegistry.ts`.
 *
 * Dos niveles, como antes:
 *
 * 1. `catalogPublished: true` — hay productos en `products.ts` con ficha,
 *    imagen y documentación del fabricante. Tienen página `/marcas/{slug}/`.
 * 2. `catalogPublished: false` — el negocio cotiza la marca, pero el
 *    repositorio no tiene modelos ni imágenes aprobadas. Se publica el
 *    logotipo, la familia que fabrica y el enlace al fabricante. **No** llevan
 *    `commercialNote`: el alcance comercial por marca sigue sin confirmar.
 */

/** Lista cerrada. Cambiarla exige confirmación escrita del negocio. */
export const APPROVED_BRAND_IDS = [
  'hielscher',
  'ortoalresa',
  'ika',
  'adam-equipment',
  'loeser',
  'serva',
] as const;

export type ApprovedBrandId = (typeof APPROVED_BRAND_IDS)[number];

export interface Brand {
  id: ApprovedBrandId;
  /** Nombre público exacto. No abreviar ni castellanizar. */
  name: string;
  slug: string;
  /** Razón social u otro nombre legal cuando aplique */
  legalName?: string;
  websiteUrl: string;
  /**
   * El sitio del fabricante no ofrece HTTPS. Sólo Löser: su sitio es de 2005 y
   * el servidor rechaza el saludo TLS. Se enlaza igualmente porque es la fuente
   * oficial, y la excepción queda declarada aquí para que `validate:brands` la
   * distinga de un descuido. Registrado en `docs/design/CONTENT_NEEDED.md`.
   */
  websiteInsecure?: true;
  logoPath: string;
  logoAlt: string;
  /** Dimensiones intrínsecas del archivo en public/ (evita CLS) */
  logoWidth: number;
  logoHeight: number;
  /**
   * Altura óptica de presentación en px. Los lockups anchos necesitan menos
   * altura que los cuadrados para pesar lo mismo en el muro.
   */
  logoDisplayHeight: number;
  /** Familia de equipo que fabrica. Id de `equipmentScope.ts`. */
  familyId: string;
  /** Qué fabrica, en una línea. Confirmado por el negocio el 2026-09-06. */
  listSummary: string;
  /** Catálogo publicado en el sitio: habilita /marcas/{slug}/ */
  catalogPublished: boolean;
  /** Párrafo de resumen en la página de marca. Sólo con catálogo publicado. */
  summary?: string;
  /** Párrafo introductorio adicional en página de marca */
  brandIntro?: string;
  applicationAreas?: readonly string[];
  /** Condiciones comerciales. Sólo donde el negocio las ha confirmado. */
  commercialNote?: string;
  /** Subtítulo para página de marca */
  pageSubtitle?: string;
  /** Descripción SEO de la página de marca (máx. 158 caracteres). */
  metaDescription?: string;
  manufacturerAttribution?: string;
}

export const brands: Brand[] = [
  {
    id: 'hielscher',
    name: 'Hielscher Ultrasonics',
    slug: 'hielscher',
    legalName: 'Hielscher Ultrasonics GmbH',
    websiteUrl: 'https://www.hielscher.com/',
    logoPath: '/brands/hielscher-logo.png',
    logoAlt: 'Logotipo de Hielscher Ultrasonics',
    logoWidth: 314,
    logoHeight: 144,
    logoDisplayHeight: 32,
    familyId: 'sonicacion',
    listSummary: 'Sonicadores y procesadores ultrasónicos para laboratorio y proceso.',
    catalogPublished: false,
  },
  {
    id: 'ortoalresa',
    name: 'Ortoalresa',
    legalName: 'Álvarez Redondo S.A.',
    slug: 'ortoalresa',
    websiteUrl: 'https://ortoalresa.com/',
    logoPath: '/brands/ortoalresa-logo.png',
    logoAlt: 'Logotipo de Ortoalresa',
    logoWidth: 429,
    logoHeight: 144,
    logoDisplayHeight: 36,
    familyId: 'centrifugacion',
    listSummary: 'Centrífugas de laboratorio, microcentrífugas y equipos refrigerados.',
    catalogPublished: true,
    summary:
      'Ortoalresa, fabricante europeo de centrífugas de laboratorio, ofrece equipos para aplicaciones generales y necesidades específicas de laboratorio.',
    manufacturerAttribution:
      'Información de producto según documentación pública del fabricante. OrigenLab no fabrica estos equipos.',
    pageSubtitle: 'Centrífugas de laboratorio para aplicaciones generales y especializadas',
    metaDescription:
      'Centrífugas Ortoalresa disponibles para cotización a través de OrigenLab: cinco modelos de catálogo con ficha técnica del fabricante.',
    applicationAreas: [
      'Preparación de muestras',
      'Microtubos y tubos cónicos',
      'Bioprocesos',
      'Laboratorio clínico y control de calidad',
    ],
    commercialNote:
      'Equipos Ortoalresa disponibles para evaluación técnica y cotización a través de OrigenLab. Disponible bajo cotización; configuración, accesorios y condiciones comerciales se confirman al cotizar.',
  },
  {
    id: 'ika',
    name: 'IKA',
    slug: 'ika',
    websiteUrl: 'https://www.ika.com/',
    logoPath: '/brands/ika-logo.png',
    logoAlt: 'Logotipo de IKA',
    logoWidth: 358,
    logoHeight: 144,
    logoDisplayHeight: 23,
    familyId: 'dispersion-homogeneizacion',
    listSummary: 'Dispersores y equipos de homogeneización para laboratorio y proceso.',
    catalogPublished: false,
  },
  {
    id: 'adam-equipment',
    name: 'Adam Equipment',
    slug: 'adam-equipment',
    websiteUrl: 'https://adamequipment.com/',
    logoPath: '/brands/adam-equipment-logo.png',
    logoAlt: 'Logotipo de Adam Equipment',
    logoWidth: 346,
    logoHeight: 144,
    logoDisplayHeight: 26,
    familyId: 'pesaje-humedad',
    listSummary: 'Balanzas de laboratorio y analizadores de humedad.',
    catalogPublished: false,
  },
  {
    id: 'loeser',
    name: 'Löser Messtechnik',
    slug: 'loeser-messtechnik',
    websiteUrl: 'http://www.loeser-osmometer.de/',
    websiteInsecure: true,
    logoPath: '/brands/loeser-logo.png',
    logoAlt: 'Logotipo de Löser Messtechnik',
    logoWidth: 122,
    logoHeight: 84,
    logoDisplayHeight: 26,
    familyId: 'osmometria',
    listSummary: 'Osmómetros y criómetros para laboratorio clínico y de investigación.',
    catalogPublished: false,
  },
  {
    id: 'serva',
    name: 'SERVA Electrophoresis',
    legalName: 'SERVA Electrophoresis GmbH',
    slug: 'serva-electrophoresis',
    websiteUrl: 'https://www.serva.de/deDE/index.html',
    logoPath: '/brands/serva-logo.png',
    logoAlt: 'Logotipo de SERVA Electrophoresis',
    logoWidth: 481,
    logoHeight: 144,
    logoDisplayHeight: 30,
    familyId: 'electroforesis',
    listSummary:
      'Reactivos, insumos y equipos verificados para electroforesis y preparación de muestras.',
    catalogPublished: true,
    summary:
      'Proveedor internacional de reactivos e insumos para aplicaciones de electroforesis y laboratorio.',
    brandIntro:
      'Línea disponible para consulta y cotización en aplicaciones de electroforesis.',
    pageSubtitle: 'Reactivos e insumos para electroforesis y trabajo técnico de laboratorio',
    metaDescription:
      'Reactivos e insumos SERVA para electroforesis, disponibles por pedido a través de OrigenLab. Condiciones y documentación se confirman al cotizar.',
    applicationAreas: [
      'Electroforesis',
      'Preparación y tratamiento de muestras',
      'Flujos técnicos de laboratorio',
    ],
    commercialNote:
      'OrigenLab puede gestionar pedidos directos con SERVA según confirmación comercial. La modalidad de prepago aplica y la disponibilidad, documentación técnica y condiciones se confirman al cotizar.',
  },
];

/**
 * Encabezado verificado para el muro de marcas. Texto idéntico al usado en la
 * firma corporativa; no reemplazar por lenguaje de representación oficial.
 */
export const brandsWallHeading = 'Marcas con las que trabajamos' as const;
