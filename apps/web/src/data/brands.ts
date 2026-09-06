/**
 * Marcas — confirmar alcance comercial antes de ampliar claims públicos.
 *
 * Dos niveles, ambos verificables en el repositorio:
 *
 * 1. `catalogPublished: true` — marcas con catálogo publicado en el sitio
 *    (productos en `products.ts`, imágenes y fichas propias). Tienen página
 *    `/marcas/{slug}/`.
 * 2. `catalogPublished: false` — marcas con las que OrigenLab trabaja, listadas
 *    públicamente en la firma de correo corporativa
 *    (`public/email/origenlab-contacto-signature.html`, texto verificado
 *    «Marcas con las que trabajamos»). Se muestran como logotipo con enlace al
 *    fabricante. **No** llevan descripción propia hasta que el negocio confirme
 *    una: ver `docs/design/CONTENT_NEEDED.md`.
 *
 * Ninguna marca puede describirse como representación oficial, distribución
 * exclusiva ni certificación sin confirmación comercial escrita.
 */
export interface Brand {
  id: string;
  name: string;
  slug: string;
  /** Razón social u otro nombre legal cuando aplique */
  legalName?: string;
  websiteUrl?: string;
  logoPath?: string;
  logoAlt?: string;
  /** Dimensiones intrínsecas del archivo en public/ (evita CLS) */
  logoWidth?: number;
  logoHeight?: number;
  /**
   * Altura óptica de presentación en px. Los lockups anchos (CRTOP) necesitan
   * menos altura que los cuadrados (Ortoalresa) para pesar lo mismo en el muro.
   */
  logoDisplayHeight?: number;
  logoSourceUrl?: string;
  /** Catálogo publicado en el sitio: habilita /marcas/{slug}/ */
  catalogPublished: boolean;
  /** Una línea para filas de índice y vitrinas */
  listSummary?: string;
  /** Párrafo de resumen en la página de marca */
  summary?: string;
  /** Párrafo introductorio adicional en página de marca */
  brandIntro?: string;
  applicationAreas?: readonly string[];
  commercialNote?: string;
  /** Subtítulo para página de marca */
  pageSubtitle?: string;
  /** Descripción SEO de la página de marca (máx. 158 caracteres). */
  metaDescription?: string;
  manufacturerAttribution?: string;
}

export const brands: Brand[] = [
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
    logoSourceUrl:
      'https://ortoalresa.com/static/images/logo-header-normal-1c27f117243d1215a0b668f0ee824e57.svg',
    catalogPublished: true,
    listSummary:
      'Centrífugas, microcentrífugas y equipos refrigerados para preparación de muestras y análisis.',
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
    id: 'serva',
    name: 'SERVA Electrophoresis GmbH',
    slug: 'serva-electrophoresis',
    websiteUrl: 'https://www.serva.de/deDE/index.html',
    logoPath: '/brands/serva-logo.png',
    logoAlt: 'Logotipo de SERVA Electrophoresis',
    logoWidth: 481,
    logoHeight: 144,
    logoDisplayHeight: 30,
    logoSourceUrl: 'https://www.serva.de/lib/images/serva-logo.png',
    catalogPublished: true,
    listSummary:
      'Reactivos e insumos para electroforesis y preparación de muestras en laboratorio.',
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
    logoSourceUrl: 'https://www.ika.com/ika/images/Logo-IKA-without-Claim.png',
    catalogPublished: false,
  },
  {
    id: 'hielscher',
    name: 'Hielscher',
    slug: 'hielscher',
    websiteUrl: 'https://www.hielscher.com/',
    logoPath: '/brands/hielscher-logo.png',
    logoAlt: 'Logotipo de Hielscher',
    logoWidth: 314,
    logoHeight: 144,
    logoDisplayHeight: 32,
    logoSourceUrl: 'https://www.hielscher.com/wp-content/uploads/hielscher-logo2.svg',
    catalogPublished: false,
  },
  {
    id: 'ollital',
    name: 'Ollital',
    slug: 'ollital',
    websiteUrl: 'https://www.ollital.com/',
    logoPath: '/brands/ollital-logo.png',
    logoAlt: 'Logotipo de Ollital',
    logoWidth: 641,
    logoHeight: 144,
    logoDisplayHeight: 28,
    logoSourceUrl: 'https://www.ollital.com/uploadfile/userimg/9f6fd3332271a26b56dbc789cec01c68.jpeg',
    catalogPublished: false,
  },
  {
    id: 'crtop',
    name: 'CRTOP',
    slug: 'crtop',
    websiteUrl: 'https://www.crtopmachine.com/',
    logoPath: '/brands/crtop-logo.png',
    logoAlt: 'Logotipo de CRTOP',
    logoWidth: 1158,
    logoHeight: 144,
    logoDisplayHeight: 26,
    logoSourceUrl:
      'https://www.crtopmachine.com/uploadfile/userimg/f00993a6a3fa4cec3aae84af3d87d9da.jpg',
    catalogPublished: false,
  },
];

/**
 * Encabezado verificado para el muro de marcas. Texto idéntico al usado en la
 * firma corporativa; no reemplazar por lenguaje de representación oficial.
 */
export const brandsWallHeading = 'Marcas con las que trabajamos' as const;
