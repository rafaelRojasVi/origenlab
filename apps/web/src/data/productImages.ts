/**
 * Registro de fotografías de producto. Una fila por imagen publicada o
 * candidata, con su procedencia y su base de permiso.
 *
 * `sourceRegistry.ts` responde por marca: cuál es la página oficial de la
 * familia y qué base de permiso tiene la marca en conjunto. Este archivo
 * responde por imagen: de qué modelo exacto es cada fotografía, de dónde salió,
 * por qué se puede publicar, dónde está el original y qué derivados sirve el
 * sitio. Las dos preguntas se separan porque una marca puede tener una
 * fotografía autorizada y otra no, y un registro por marca no puede decirlo.
 *
 * # Qué puede entrar aquí
 *
 * Sólo imágenes con una de estas seis bases, y la fila dice cuál:
 *
 *   `autorizacion-fabricante` autorización previa del fabricante en poder de
 *                             OrigenLab, confirmada por el titular del negocio
 *   `activo-origenlab`        ya en poder de OrigenLab, con procedencia registrada
 *   `recurso-distribuidor`    portal o paquete del fabricante para distribuidores
 *   `portal-prensa`           prensa o medios del fabricante con términos de reuso
 *   `entrega-fabricante`      enviada por el fabricante para uso comercial
 *   `licencia-oficial`        activo oficial con licencia expresa
 *
 * Que una imagen sea visible en la web del fabricante no es ninguna de las
 * seis. Sin base documentada, la fila queda en `ASSET_PERMISSION_NEEDED`, el
 * sitio **no** la incrusta y `note` dice qué habría que pedir y a quién.
 *
 * # Permiso y procedencia son dos cosas
 *
 * La base de permiso de las seis marcas es hoy la misma: el titular del negocio
 * confirmó el 2026-09-07 que OrigenLab cuenta con autorización previa de cada
 * fabricante para publicar su fotografía oficial de producto en origenlab.cl
 * (`MANUFACTURER_AUTHORIZATION_BASIS`). Esa autorización no está publicada en
 * los sitios de los fabricantes, y este registro no afirma nada que el negocio
 * no haya comunicado: ni licencia pública, ni documento, ni cláusula, ni
 * vigencia, ni exclusividad. La procedencia, en cambio, es por imagen:
 * `imageSourceUrl` es la página oficial, el folleto oficial o el activo
 * oficial exacto del que salió cada fotografía, y `note` dice de qué página o
 * de qué pliego cuando el origen es un PDF.
 *
 * # Lo que el sitio hace con cada fila
 *
 * Sólo las filas `VERIFIED` se renderizan como fotografía. Una fila
 * `familia-representativa` se rotula siempre como imagen representativa de la
 * familia y nunca como fotografía de un modelo concreto. `validate:images`
 * comprueba las dos cosas contra el HTML construido, y además que cada imagen
 * local bajo `public/products/` tenga fila, que ninguna apunte a un host
 * remoto, que marca y modelo existan en el catálogo, que el original y los
 * derivados existan con las dimensiones declaradas y que el alt esté escrito.
 *
 * Los derivados los genera `npm run build:product-images` a los anchos de
 * `PRODUCT_IMAGE_WIDTHS`, en AVIF y WebP, junto al original, y nunca amplía:
 * un original de 400 px produce derivados de 400 px. Las dimensiones de cada
 * derivado se declaran aquí porque el HTML las necesita (`width` y `height`
 * evitan el salto de maquetación), porque `ModelPhoto` las usa para no mostrar
 * ninguna fotografía por encima de su tamaño real, y porque así la validación
 * puede comprobar que el archivo en disco es el que el registro describe.
 */
import type { ApprovedBrandId } from './brands';

export type ProductImageStatus =
  /** Procedencia y base de permiso documentadas. Se publica. */
  | 'VERIFIED'
  /** Imagen identificada en la fuente oficial; falta la base de permiso. No se publica. */
  | 'ASSET_PERMISSION_NEEDED'
  /** El fabricante no publica fotografía identificable del modelo. No se publica. */
  | 'CONTENT_NEEDED'
  /** Candidata descartada. Queda registrada para que no vuelva a proponerse. */
  | 'REJECTED';

export type ProductImageSourceType =
  | 'autorizacion-fabricante'
  | 'activo-origenlab'
  | 'recurso-distribuidor'
  | 'portal-prensa'
  | 'entrega-fabricante'
  | 'licencia-oficial';

export type ProductImageScope = 'modelo-exacto' | 'familia-representativa';

export interface ProductImageDerivative {
  width: number;
  height: number;
}

export interface ProductImageRecord {
  /** `{marca}-{slug}`. Único. */
  id: string;
  brandId: ApprovedBrandId;
  /** Id de `products.ts` cuando el modelo tiene ficha propia. */
  productId?: string;
  /** Id de `brandModels.ts` cuando el modelo se describe y enlaza. */
  modelId?: string;
  /** Nombre exacto del modelo o de la familia, tal como lo escribe el fabricante. */
  model: string;
  /** Un modelo concreto, o una imagen que representa a una familia. */
  scope: ProductImageScope;
  /** Página oficial del producto. */
  officialUrl: string;
  /** De dónde se obtuvo la imagen original: página, folleto o activo oficial exacto. */
  imageSourceUrl: string;
  sourceType: ProductImageSourceType;
  /** Por qué se puede publicar. Texto completo, sin abreviar. */
  permissionBasis: string;
  /** Dónde está la prueba: ruta del repositorio, correo archivado, URL de términos. */
  permissionEvidence: string;
  /** Original en `public/products/`, o `null` si no hay archivo local. */
  masterPath: string | null;
  masterWidth: number | null;
  masterHeight: number | null;
  /** Dimensiones de cada derivado, indexadas por ancho de `PRODUCT_IMAGE_WIDTHS`. */
  derivatives: Readonly<Record<number, ProductImageDerivative>> | null;
  /** Fecha de la última comprobación de procedencia y archivos (ISO). */
  verifiedOn: string;
  /** Texto alternativo en español. Nombra fabricante y modelo y describe el equipo. */
  alt: string;
  status: ProductImageStatus;
  /** Qué falta, qué se rechazó, de qué página del PDF salió o qué conviene pedir. */
  note?: string;
}

/** Anchos derivados por `scripts/build-product-images.mjs`. */
export const PRODUCT_IMAGE_WIDTHS = [480, 960] as const;

const VERIFIED_ON = '2026-09-07';

/**
 * Base de permiso común a las seis marcas. Se escribe una vez y se cita en
 * cada fila para que el registro no pueda decir dos cosas distintas del mismo
 * hecho. La redacción en inglés es la que el negocio confirmó, literal.
 */
export const MANUFACTURER_AUTHORIZATION_BASIS =
  'OrigenLab business-owner confirmation of pre-existing manufacturer authorization for publication of official product photography on origenlab.cl, confirmed 2026-09-07. El titular del negocio de OrigenLab confirmó el 2026-09-07 que OrigenLab ya cuenta con autorización previa de este fabricante para publicar su fotografía oficial de producto en origenlab.cl. La autorización es preexistente y no está publicada en el sitio del fabricante: no se afirma licencia pública, documento escrito, cláusula contractual, vigencia ni condición de exclusividad, porque el negocio no comunicó ninguna. La fotografía se obtuvo de la fuente oficial que indica imageSourceUrl y se aloja localmente, redimensionada y convertida de formato, sin retocar el equipo.';
const MANUFACTURER_AUTHORIZATION_EVIDENCE =
  'Confirmación directa del titular del negocio a la revisión de catálogo del 2026-09-07 (fase 3.1), registrada en docs/product-assets.md, sección «Base de permiso de la fotografía de producto (2026-09-07)».';

const ORTOALRESA_BASIS = `${MANUFACTURER_AUTHORIZATION_BASIS} Activo en poder de OrigenLab desde 2026-05-16, obtenido de la página de producto del fabricante con procedencia registrada por modelo en docs/product-assets.md.`;
const ORTOALRESA_EVIDENCE = `${MANUFACTURER_AUTHORIZATION_EVIDENCE} Procedencia: docs/product-assets.md, tabla «Ortoalresa — active catalog (2026-05-16)».`;

const IKA_BROCHURE_URL =
  'https://www.ika.com/ika/pdf/flyer-catalog/Disperser_Brochure_IWS_EN_wop_screen.pdf';
const SERVA_CATALOG_NOTE =
  'Única imagen que el fabricante publica del producto, en el directorio imgProd/190 de serva.de; el catálogo PDF de electroforesis no contiene una versión mayor.';

interface PublishedSpec {
  id: string;
  brandId: ApprovedBrandId;
  productId?: string;
  modelId?: string;
  model: string;
  scope: ProductImageScope;
  officialUrl: string;
  imageSourceUrl: string;
  masterPath: string;
  masterWidth: number;
  masterHeight: number;
  derivatives: Readonly<Record<number, ProductImageDerivative>>;
  alt: string;
  note?: string;
}

/** Fila publicada con la base de permiso común. */
function published(spec: PublishedSpec): ProductImageRecord {
  return {
    ...spec,
    sourceType: 'autorizacion-fabricante',
    permissionBasis: MANUFACTURER_AUTHORIZATION_BASIS,
    permissionEvidence: MANUFACTURER_AUTHORIZATION_EVIDENCE,
    verifiedOn: VERIFIED_ON,
    status: 'VERIFIED',
  };
}

export const productImages: readonly ProductImageRecord[] = [
  /* -- Ortoalresa · centrifugación ---------------------------------------- */
  {
    id: 'ortoalresa-biocen-22',
    brandId: 'ortoalresa',
    productId: 'ortoalresa-biocen-22',
    model: 'Biocen 22',
    scope: 'modelo-exacto',
    officialUrl: 'https://ortoalresa.com/en/products/',
    imageSourceUrl: 'https://ortoalresa.com/imagen_producto/Biocen_22.avif',
    sourceType: 'autorizacion-fabricante',
    permissionBasis: ORTOALRESA_BASIS,
    permissionEvidence: ORTOALRESA_EVIDENCE,
    masterPath: '/products/ortoalresa/biocen-22.avif',
    masterWidth: 878,
    masterHeight: 1024,
    derivatives: { 480: { width: 480, height: 566 }, 960: { width: 773, height: 912 } },
    verifiedOn: VERIFIED_ON,
    alt: 'Microcentrífuga Ortoalresa Biocen 22',
    status: 'VERIFIED',
  },
  {
    id: 'ortoalresa-biocen-22-r',
    brandId: 'ortoalresa',
    productId: 'ortoalresa-biocen-22-r',
    model: 'Biocen 22 R',
    scope: 'modelo-exacto',
    officialUrl: 'https://ortoalresa.com/en/products/',
    imageSourceUrl: 'https://ortoalresa.com/imagen_producto/Biocen_22_R.avif',
    sourceType: 'autorizacion-fabricante',
    permissionBasis: ORTOALRESA_BASIS,
    permissionEvidence: ORTOALRESA_EVIDENCE,
    masterPath: '/products/ortoalresa/biocen-22-r.avif',
    masterWidth: 1024,
    masterHeight: 1015,
    derivatives: { 480: { width: 480, height: 496 }, 960: { width: 927, height: 957 } },
    verifiedOn: VERIFIED_ON,
    alt: 'Microcentrífuga refrigerada Ortoalresa Biocen 22 R',
    status: 'VERIFIED',
  },
  {
    id: 'ortoalresa-digicen-22',
    brandId: 'ortoalresa',
    productId: 'ortoalresa-digicen-22',
    model: 'Digicen 22',
    scope: 'modelo-exacto',
    officialUrl: 'https://ortoalresa.com/en/products/',
    imageSourceUrl: 'https://ortoalresa.com/imagen_producto/Digicen_22.avif',
    sourceType: 'autorizacion-fabricante',
    permissionBasis: ORTOALRESA_BASIS,
    permissionEvidence: ORTOALRESA_EVIDENCE,
    masterPath: '/products/ortoalresa/digicen-22.avif',
    masterWidth: 940,
    masterHeight: 1024,
    derivatives: { 480: { width: 480, height: 627 }, 960: { width: 743, height: 970 } },
    verifiedOn: VERIFIED_ON,
    alt: 'Centrífuga Ortoalresa Digicen 22',
    status: 'VERIFIED',
  },
  {
    id: 'ortoalresa-digicen-22-r',
    brandId: 'ortoalresa',
    productId: 'ortoalresa-digicen-22-r',
    model: 'Digicen 22 R',
    scope: 'modelo-exacto',
    officialUrl: 'https://ortoalresa.com/en/products/',
    imageSourceUrl: 'https://ortoalresa.com/imagen_producto/Digicen_22_R.avif',
    sourceType: 'autorizacion-fabricante',
    permissionBasis: ORTOALRESA_BASIS,
    permissionEvidence: ORTOALRESA_EVIDENCE,
    masterPath: '/products/ortoalresa/digicen-22-r.avif',
    masterWidth: 1024,
    masterHeight: 839,
    derivatives: { 480: { width: 480, height: 473 }, 960: { width: 844, height: 832 } },
    verifiedOn: VERIFIED_ON,
    alt: 'Centrífuga refrigerada Ortoalresa Digicen 22 R',
    status: 'VERIFIED',
  },
  {
    id: 'ortoalresa-consul-22',
    brandId: 'ortoalresa',
    productId: 'ortoalresa-consul-22',
    model: 'Consul 22',
    scope: 'modelo-exacto',
    officialUrl: 'https://ortoalresa.com/en/products/',
    imageSourceUrl: 'https://ortoalresa.com/imagen_producto/Consul_22.avif',
    sourceType: 'autorizacion-fabricante',
    permissionBasis: ORTOALRESA_BASIS,
    permissionEvidence: ORTOALRESA_EVIDENCE,
    masterPath: '/products/ortoalresa/consul-22.avif',
    masterWidth: 947,
    masterHeight: 1024,
    derivatives: { 480: { width: 480, height: 517 }, 960: { width: 898, height: 968 } },
    verifiedOn: VERIFIED_ON,
    alt: 'Centrífuga Ortoalresa Consul 22',
    status: 'VERIFIED',
  },
  /**
   * `bioprocen-22-r.avif` sigue en disco como archivo retirado (ver
   * docs/product-assets.md, «Archived / unused on site»). No es candidata: el
   * producto salió del catálogo en 2026-05 y `validate:catalog` impide que
   * vuelva. Se registra para que `validate:images` no la tome por una imagen
   * publicada sin fila.
   */
  {
    id: 'ortoalresa-bioprocen-22-r',
    brandId: 'ortoalresa',
    model: 'Bioprocen 22 R',
    scope: 'modelo-exacto',
    officialUrl: 'https://ortoalresa.com/en/products/',
    imageSourceUrl: 'https://ortoalresa.com/imagen_producto/Bioprocen_22_R.avif',
    sourceType: 'autorizacion-fabricante',
    permissionBasis: ORTOALRESA_BASIS,
    permissionEvidence: ORTOALRESA_EVIDENCE,
    masterPath: '/products/ortoalresa/bioprocen-22-r.avif',
    masterWidth: 1019,
    masterHeight: 1024,
    derivatives: { 480: { width: 480, height: 488 }, 960: { width: 814, height: 828 } },
    verifiedOn: VERIFIED_ON,
    alt: 'Centrífuga Ortoalresa Bioprocen 22 R',
    status: 'REJECTED',
    note: 'Producto retirado del catálogo público en 2026-05. El archivo se conserva por instrucción del negocio y no se publica.',
  },

  /* -- Hielscher Ultrasonics · sonicación --------------------------------- */
  published({
    id: 'hielscher-up100h',
    brandId: 'hielscher',
    modelId: 'hielscher-up100h',
    model: 'UP100H',
    scope: 'modelo-exacto',
    officialUrl: 'https://www.hielscher.com/100h_p.htm',
    imageSourceUrl: 'https://www.hielscher.com/image/up100h_05_p1000.jpg',
    masterPath: '/products/hielscher/up100h.jpg',
    masterWidth: 1000,
    masterHeight: 989,
    derivatives: { 480: { width: 480, height: 466 }, 960: { width: 960, height: 931 } },
    alt: 'Sonicador de mano Hielscher UP100H con sonotrodo',
    note: 'Fotografía del modelo exacto en la página oficial del UP100H, versión de 1.000 px.',
  }),
  published({
    id: 'hielscher-up200st',
    brandId: 'hielscher',
    modelId: 'hielscher-up200st',
    model: 'UP200St',
    scope: 'modelo-exacto',
    officialUrl: 'https://www.hielscher.com/up200st-powerful-ultrasonic-lab-homogenizer.htm',
    imageSourceUrl: 'https://www.hielscher.com/wp-content/uploads/UP200St_silver_cut.png',
    masterPath: '/products/hielscher/up200st.png',
    masterWidth: 640,
    masterHeight: 604,
    derivatives: { 480: { width: 480, height: 451 }, 960: { width: 638, height: 599 } },
    alt: 'Sonicador de laboratorio Hielscher UP200St: transductor en soporte sobre un vaso de muestra y generador con pantalla',
    note: 'Recorte de producto del modelo exacto en la página oficial del UP200St.',
  }),
  published({
    id: 'hielscher-up400st',
    brandId: 'hielscher',
    modelId: 'hielscher-up400st',
    model: 'UP400St',
    scope: 'modelo-exacto',
    officialUrl: 'https://www.hielscher.com/up400st-powerful-ultrasonicator.htm',
    imageSourceUrl:
      'https://www.hielscher.com/wp-content/uploads/Ultrasonic_Homogenizer_UP400St_S24d22D-05-p1000.jpg',
    masterPath: '/products/hielscher/up400st.jpg',
    masterWidth: 617,
    masterHeight: 1000,
    derivatives: { 480: { width: 480, height: 828 }, 960: { width: 573, height: 988 } },
    alt: 'Sonicador Hielscher UP400St montado en soporte con sonotrodo S24d22D sobre un vaso de muestra',
    note: 'Fotografía del modelo exacto en la página oficial del UP400St, de 1.000 px de alto.',
  }),
  published({
    id: 'hielscher-uip2000hdt',
    brandId: 'hielscher',
    modelId: 'hielscher-uip2000hdt',
    model: 'UIP2000hdT',
    scope: 'modelo-exacto',
    officialUrl:
      'https://www.hielscher.com/uip2000hdt-2000-watts-powerful-industrial-ultrasonicator-for-full-process-control.htm',
    imageSourceUrl:
      'https://www.hielscher.com/wp-content/uploads/UIP2000hdT-sonicator-transducer-generator-HielscherUltrasonics.jpg',
    masterPath: '/products/hielscher/uip2000hdt.jpg',
    masterWidth: 1000,
    masterHeight: 769,
    derivatives: { 480: { width: 480, height: 483 }, 960: { width: 720, height: 725 } },
    alt: 'Procesador ultrasónico industrial Hielscher UIP2000hdT: transductor y generador',
    note: 'Fotografía de equipo solo (transductor y generador) del modelo exacto, original sin sufijo de tamaño de la página oficial del UIP2000hdT.',
  }),

  /* -- IKA · dispersión (folleto oficial; el sitio HTML no se lee por máquina) */
  published({
    id: 'ika-t10-basic',
    brandId: 'ika',
    modelId: 'ika-t10-basic',
    model: 'T 10 basic ULTRA-TURRAX',
    scope: 'modelo-exacto',
    officialUrl: 'https://www.ika.com/en/Products-LabEq/Dispersers-pg177/',
    imageSourceUrl: IKA_BROCHURE_URL,
    masterPath: '/products/ika/t-10-basic.png',
    masterWidth: 73,
    masterHeight: 339,
    derivatives: { 480: { width: 73, height: 338 }, 960: { width: 73, height: 338 } },
    alt: 'Dispersor de mano IKA T 10 basic ULTRA-TURRAX con eje dispersor',
    note: 'Recorte del modelo exacto incrustado en la página 2 del folleto oficial de dispersores (pliego «Dispersers | From Invention to Innovation», escalera T 10 basic, T 18 digital, T 25 digital, T 50 digital), extraído del PDF sin retoque. El folleto lo incrusta a 73 × 339 px y el sitio lo muestra sin ampliar; conviene pedir a IKA el archivo en mayor resolución.',
  }),
  published({
    id: 'ika-t18-digital',
    brandId: 'ika',
    modelId: 'ika-t18-digital',
    model: 'T 18 digital ULTRA-TURRAX',
    scope: 'modelo-exacto',
    officialUrl: 'https://www.ika.com/en/Products-LabEq/Dispersers-pg177/',
    imageSourceUrl: IKA_BROCHURE_URL,
    masterPath: '/products/ika/t-18-digital.png',
    masterWidth: 117,
    masterHeight: 468,
    derivatives: { 480: { width: 105, height: 449 }, 960: { width: 105, height: 449 } },
    alt: 'Dispersor IKA T 18 digital ULTRA-TURRAX con pantalla de revoluciones y eje dispersor',
    note: 'Recorte del modelo exacto (rotulado «IKA T18 digital» en el propio equipo) incrustado en la página 2 del folleto oficial de dispersores, extraído del PDF sin retoque. El folleto lo incrusta a 117 × 468 px y el sitio lo muestra sin ampliar; conviene pedir a IKA el archivo en mayor resolución.',
  }),
  published({
    id: 'ika-t25-digital',
    brandId: 'ika',
    modelId: 'ika-t25-digital',
    model: 'T 25 digital ULTRA-TURRAX',
    scope: 'modelo-exacto',
    officialUrl: 'https://www.ika.com/en/Products-LabEq/Dispersers-pg177/',
    imageSourceUrl: IKA_BROCHURE_URL,
    masterPath: '/products/ika/t-25-digital.png',
    masterWidth: 153,
    masterHeight: 670,
    derivatives: { 480: { width: 152, height: 661 }, 960: { width: 152, height: 661 } },
    alt: 'Dispersor IKA T 25 digital ULTRA-TURRAX con pantalla de revoluciones y eje dispersor',
    note: 'Recorte del modelo exacto (rotulado «IKA T25 digital» en el propio equipo) incrustado en la página 3 del folleto oficial de dispersores (pliego «T-series | Innovative solutions for dispersion technology»), extraído del PDF sin retoque. El folleto lo incrusta a 153 × 670 px y el sitio lo muestra sin ampliar.',
  }),

  /* -- Adam Equipment · pesaje y humedad (imágenes de familia) ------------ */
  published({
    id: 'adam-pmb',
    brandId: 'adam-equipment',
    modelId: 'adam-pmb',
    model: 'PMB',
    scope: 'familia-representativa',
    officialUrl: 'https://adamequipment.com/pmb-moisture-analyzers-us.html',
    imageSourceUrl: 'https://adamequipment.com/media/docs/data_sheets/PMB-DS-A4-EN.pdf',
    masterPath: '/products/adam-equipment/pmb.png',
    masterWidth: 357,
    masterHeight: 357,
    derivatives: { 480: { width: 343, height: 351 }, 960: { width: 343, height: 351 } },
    alt: 'Analizador de humedad Adam Equipment PMB con la tapa abierta, imagen representativa de la familia',
    note: 'Imagen de producto de la ficha técnica oficial de la serie PMB (página 1 del PDF), extraída con su máscara de recorte y compuesta sobre blanco. Representa a la familia: la ficha no identifica la capacidad del equipo fotografiado. El CDN de imágenes del sitio (adamequipment.sirv.com) rechaza la descarga directa y no se eludió; la ficha PDF la publica el propio fabricante. Se incrusta a 357 × 357 px y el sitio la muestra sin ampliar.',
  }),
  published({
    id: 'adam-solis',
    brandId: 'adam-equipment',
    modelId: 'adam-solis',
    model: 'Solis',
    scope: 'familia-representativa',
    officialUrl: 'https://adamequipment.com/solis-analytical-and-semi-micro-balances-us.html',
    imageSourceUrl:
      'https://adamequipment.com/media/wysiwyg/pagebuilder/EnhancedContentImages/SAB/SAB225i-F.jpg',
    masterPath: '/products/adam-equipment/solis.jpg',
    masterWidth: 999,
    masterHeight: 999,
    derivatives: { 480: { width: 480, height: 890 }, 960: { width: 521, height: 966 } },
    alt: 'Balanza analítica Adam Equipment Solis SAB 225i con cámara de pesaje cerrada, imagen representativa de la familia Solis',
    note: 'Fotografía frontal de la Solis SAB 225i publicada en la página oficial de la familia Solis (imagen de contenido servida desde adamequipment.com). Es un modelo de la familia y representa a la familia; la entrada del catálogo es de familia y no de ese modelo.',
  }),
  published({
    id: 'adam-highland',
    brandId: 'adam-equipment',
    modelId: 'adam-highland',
    model: 'Highland',
    scope: 'familia-representativa',
    officialUrl: 'https://adamequipment.com/highland-portable-precision-balances-us.html',
    imageSourceUrl: 'https://adamequipment.com/media/docs/data_sheets/HCB-DS-A4-EN.pdf',
    masterPath: '/products/adam-equipment/highland.png',
    masterWidth: 1156,
    masterHeight: 1260,
    derivatives: { 480: { width: 480, height: 532 }, 960: { width: 960, height: 1064 } },
    alt: 'Balanza de precisión portátil Adam Equipment Highland con cubierta cortavientos, imagen representativa de la familia Highland',
    note: 'Imagen de producto de la ficha técnica oficial de la serie Highland HCB (página 1 del PDF), extraída con su máscara de recorte y compuesta sobre blanco. Representa a la familia: la ficha no identifica la capacidad del equipo fotografiado.',
  }),

  /* -- Löser Messtechnik · osmometría -------------------------------------- */
  published({
    id: 'loeser-osmometer-basic',
    brandId: 'loeser',
    modelId: 'loeser-osmometer-basic',
    model: 'Osmometer basic',
    scope: 'modelo-exacto',
    officialUrl: 'http://www.loeser-osmometer.de/typ7-eng.html',
    imageSourceUrl: 'http://www.loeser-osmometer.de/Tp7E.jpg',
    masterPath: '/products/loeser/osmometer-basic.jpg',
    masterWidth: 400,
    masterHeight: 500,
    derivatives: { 480: { width: 400, height: 500 }, 960: { width: 400, height: 500 } },
    alt: 'Osmómetro crioscópico Löser Messtechnik Osmometer basic',
    note: 'JPEG de 400 × 500 px de la página oficial del modelo; el sitio lo muestra sin ampliar. Conviene pedir a Löser un original de mayor resolución.',
  }),
  published({
    id: 'loeser-i-osmometer-basic',
    brandId: 'loeser',
    modelId: 'loeser-i-osmometer-basic',
    model: 'i Osmometer basic',
    scope: 'modelo-exacto',
    officialUrl: 'http://www.loeser-osmometer.de/typ7i-eng.html',
    imageSourceUrl: 'http://www.loeser-osmometer.de/Tp7iE.jpg',
    masterPath: '/products/loeser/i-osmometer-basic.jpg',
    masterWidth: 400,
    masterHeight: 501,
    derivatives: { 480: { width: 400, height: 501 }, 960: { width: 400, height: 501 } },
    alt: 'Osmómetro automático Löser Messtechnik i Osmometer basic',
    note: 'JPEG de 400 × 501 px de la página oficial del modelo; el sitio lo muestra sin ampliar.',
  }),
  published({
    id: 'loeser-i-osmometer',
    brandId: 'loeser',
    modelId: 'loeser-i-osmometer',
    model: 'i Osmometer',
    scope: 'modelo-exacto',
    officialUrl: 'http://www.loeser-osmometer.de/typ16-eng.html',
    imageSourceUrl: 'http://www.loeser-osmometer.de/Tp16E-New.jpg',
    masterPath: '/products/loeser/i-osmometer.jpg',
    masterWidth: 400,
    masterHeight: 500,
    derivatives: { 480: { width: 400, height: 500 }, 960: { width: 400, height: 500 } },
    alt: 'Osmómetro automático Löser Messtechnik i Osmometer con impresora y lector integrados',
    note: 'JPEG de 400 × 500 px de la página oficial del modelo, con el distintivo «New» que el fabricante incluye en la propia imagen; el sitio lo muestra sin ampliar.',
  }),
  published({
    id: 'loeser-i-cryometer',
    brandId: 'loeser',
    modelId: 'loeser-i-cryometer',
    model: 'i Cryometer',
    scope: 'modelo-exacto',
    officialUrl: 'http://www.loeser-osmometer.de/typ21-eng.html',
    imageSourceUrl: 'http://www.loeser-osmometer.de/Tp21E-New.jpg',
    masterPath: '/products/loeser/i-cryometer.jpg',
    masterWidth: 400,
    masterHeight: 500,
    derivatives: { 480: { width: 400, height: 500 }, 960: { width: 400, height: 500 } },
    alt: 'Criómetro automático Löser Messtechnik i Cryometer',
    note: 'JPEG de 400 × 500 px de la página oficial del modelo, con el distintivo «New» que el fabricante incluye en la propia imagen; el sitio lo muestra sin ampliar.',
  }),

  /* -- SERVA Electrophoresis · electroforesis ------------------------------ */
  published({
    id: 'serva-bluevertical-prime',
    brandId: 'serva',
    modelId: 'serva-bluevertical-prime',
    model: 'BlueVertical PRiME',
    scope: 'modelo-exacto',
    officialUrl:
      'https://www.serva.de/enDE/ProductDetails/4741_BV-104_BlueVertical_TM_PRiME_TM_Mini_Slab_Gel_Unit_0_0.html',
    imageSourceUrl: 'https://www.serva.de/images/imgProd/190/BV-104-s.jpg',
    masterPath: '/products/serva/bluevertical-prime.jpg',
    masterWidth: 500,
    masterHeight: 374,
    derivatives: { 480: { width: 480, height: 359 }, 960: { width: 500, height: 374 } },
    alt: 'Cubeta vertical para minigeles SERVA Electrophoresis BlueVertical PRiME',
    note: `JPEG de 500 × 374 px. ${SERVA_CATALOG_NOTE}`,
  }),
  published({
    id: 'serva-hpe-bluehorizon',
    brandId: 'serva',
    modelId: 'serva-hpe-bluehorizon',
    model: 'HPE BlueHorizon',
    scope: 'modelo-exacto',
    officialUrl: 'https://www.serva.de/enDE/ProductDetails/5120_HPE-BH_HPE_TM_BlueHorizon_TM_212_457.html',
    imageSourceUrl: 'https://www.serva.de/images/imgProd/190/HPE-BH-s.jpg',
    masterPath: '/products/serva/hpe-bluehorizon.jpg',
    masterWidth: 400,
    masterHeight: 256,
    derivatives: { 480: { width: 399, height: 255 }, 960: { width: 399, height: 255 } },
    alt: 'Cámara horizontal de lecho plano SERVA Electrophoresis HPE BlueHorizon con su fuente de alimentación',
    note: `JPEG de 400 × 256 px. ${SERVA_CATALOG_NOTE}`,
  }),
  published({
    id: 'serva-bluemarine-100',
    brandId: 'serva',
    modelId: 'serva-bluemarine-100',
    model: 'BlueMarine 100',
    scope: 'modelo-exacto',
    officialUrl: 'https://www.serva.de/enDE/ProductDetails/2291_BM-100_BlueMarine_TM_100_0_208.html',
    imageSourceUrl: 'https://www.serva.de/images/imgProd/190/BM-100-s.jpg',
    masterPath: '/products/serva/bluemarine-100.jpg',
    masterWidth: 169,
    masterHeight: 107,
    derivatives: { 480: { width: 169, height: 107 }, 960: { width: 169, height: 107 } },
    alt: 'Cámara submarina de agarosa SERVA Electrophoresis BlueMarine 100 con sus cables de electrodo',
    note: `JPEG de 169 × 107 px: el sitio lo muestra a su tamaño real, sin ampliar, y por eso se ve pequeño. ${SERVA_CATALOG_NOTE} Conviene pedir a SERVA (LICORbio) el original en alta resolución.`,
  }),
  published({
    id: 'serva-bluepower',
    brandId: 'serva',
    modelId: 'serva-bluepower',
    model: 'BluePower',
    scope: 'familia-representativa',
    officialUrl:
      'https://www.serva.de/enDE/Catalog/459_Laboratory_Equipment_Electrophoresis_Devices_Power_Supplies_212_449.html',
    imageSourceUrl: 'https://www.serva.de/images/imgProd/190/BP-600-PRI-s.jpg',
    masterPath: '/products/serva/bluepower-600-prime.jpg',
    masterWidth: 400,
    masterHeight: 267,
    derivatives: { 480: { width: 400, height: 267 }, 960: { width: 400, height: 267 } },
    alt: 'Fuente de alimentación SERVA Electrophoresis BluePower 600 PRIME, imagen representativa de la familia BluePower',
    note: `Fotografía de la BluePower 600 PRIME (400 × 267 px) publicada en la página oficial de fuentes de alimentación; representa a la familia BluePower. ${SERVA_CATALOG_NOTE}`,
  }),
  published({
    id: 'serva-blueslick-42500',
    brandId: 'serva',
    productId: 'serva-blueslick-42500',
    model: 'BlueSlick',
    scope: 'modelo-exacto',
    officialUrl: 'https://www.serva.de/enDE/ProductDetails/158_42500_BlueSlick_TM_0_214.html',
    imageSourceUrl: 'https://www.serva.de/images/imgProd/190/42500-s.jpg',
    masterPath: '/products/serva/blueslick-42500.jpg',
    masterWidth: 126,
    masterHeight: 194,
    derivatives: { 480: { width: 126, height: 194 }, 960: { width: 126, height: 194 } },
    alt: 'Pulverizador de reactivo SERVA Electrophoresis BlueSlick de 250 ml en uso',
    note: `JPEG de 126 × 194 px: el sitio lo muestra a su tamaño real, sin ampliar, y por eso se ve pequeño. ${SERVA_CATALOG_NOTE} Conviene pedir a SERVA (LICORbio) el original en alta resolución.`,
  }),
];

/** Imagen publicable de un producto con ficha propia o referencia. */
export function imageForProduct(productId: string): ProductImageRecord | undefined {
  return productImages.find(
    (image) => image.productId === productId && image.status === 'VERIFIED',
  );
}

/** Imagen publicable de un modelo documentado. */
export function imageForModel(modelId: string): ProductImageRecord | undefined {
  return productImages.find((image) => image.modelId === modelId && image.status === 'VERIFIED');
}

/** Fotografías publicables de una marca, en el orden del registro. */
export function publishedImagesForBrand(brandId: string): ProductImageRecord[] {
  return productImages.filter((image) => image.brandId === brandId && image.status === 'VERIFIED');
}

/** Filas que esperan permiso o contenido. Para auditoría y para el informe. */
export function imagesPending(): ProductImageRecord[] {
  return productImages.filter(
    (image) => image.status === 'ASSET_PERMISSION_NEEDED' || image.status === 'CONTENT_NEEDED',
  );
}
