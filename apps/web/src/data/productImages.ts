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
 * Sólo imágenes con una de estas cinco bases, y la fila dice cuál:
 *
 *   `activo-origenlab`    ya en poder de OrigenLab, con procedencia registrada
 *   `recurso-distribuidor` portal o paquete del fabricante para distribuidores
 *   `portal-prensa`        prensa o medios del fabricante con términos de reuso
 *   `entrega-fabricante`   enviada por el fabricante para uso comercial
 *   `licencia-oficial`     activo oficial con licencia expresa
 *
 * Que una imagen sea visible en la web del fabricante no es ninguna de las
 * cinco. Sin base documentada, la fila queda en `ASSET_PERMISSION_NEEDED`, el
 * sitio **no** la incrusta y `note` dice qué habría que pedir y a quién.
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
 * `PRODUCT_IMAGE_WIDTHS`, en AVIF y WebP, junto al original. Las dimensiones
 * de cada derivado se declaran aquí porque el HTML las necesita (`width` y
 * `height` evitan el salto de maquetación) y porque así la validación puede
 * comprobar que el archivo en disco es el que el registro describe.
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
  /** De dónde se obtuvo la imagen original. */
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
  /** Texto alternativo en español. Describe el equipo, no la marca. */
  alt: string;
  status: ProductImageStatus;
  /** Qué falta, qué se rechazó o qué conviene pedir. */
  note?: string;
}

/** Anchos derivados por `scripts/build-product-images.mjs`. */
export const PRODUCT_IMAGE_WIDTHS = [480, 960] as const;

const VERIFIED_ON = '2026-09-07';

const ORTOALRESA_BASIS =
  'Activo en poder de OrigenLab desde 2026-05-16, obtenido de la página de producto del fabricante con procedencia registrada por modelo en docs/product-assets.md. Publicación aprobada por el negocio en la revisión de catálogo del 2026-09-07 (fase 2) como imagen de producto de un equipo que OrigenLab cotiza. El Aviso Legal del fabricante (ortoalresa.com/aviso-legal, leído el 2026-09-07) reserva la reproducción de sus imágenes a la autorización expresa de Ortoalresa: el permiso escrito está pendiente de solicitar a marketing@ortoalresa.com (docs/design/CONTENT_NEEDED.md) y el negocio asume mientras tanto la publicación.';
const ORTOALRESA_EVIDENCE =
  'docs/product-assets.md, tabla «Ortoalresa — active catalog (2026-05-16)»; términos: https://ortoalresa.com/aviso-legal';

/**
 * Cinco fabricantes sin base de permiso. Cada fila identifica la fotografía
 * exacta del modelo en la fuente oficial y qué dijo el fabricante sobre su
 * reutilización, leído el 2026-09-07. Ninguna se descargó ni se publica: la
 * fila existe para que la petición de permiso nombre archivo por archivo y
 * para que, cuando llegue la respuesta, baste con rellenar `masterPath`,
 * dimensiones y derivados y cambiar el estado.
 */
const HIELSCHER_TERMS =
  'Sin base de permiso. El aviso legal del fabricante (hielscher.com/copy_1.htm) dice que textos e imágenes «may not be copied for commercial or other purposes, nor may it be displayed, even in a modified version, on other websites» y advierte que parte de las fotografías son de terceros. No hay portal de prensa ni de distribuidores. Pedir por escrito a Hielscher Ultrasonics GmbH (formulario hielscher.com/email.htm, Teltow).';
const HIELSCHER_EVIDENCE = 'https://www.hielscher.com/copy_1.htm (Imprint & Copyright, leído el 2026-09-07)';
const IKA_TERMS =
  'Sin base de permiso comprobable. ika.com devuelve 403 a todo cliente automatizado, incluidos el aviso legal (ika.com/en/Impressum-imp.html), las páginas de modelo y el centro de descargas; el folleto oficial de dispersores (PDF) no contiene ninguna cláusula de reutilización. No se pudo leer ninguna URL de imagen. Pedir por escrito a IKA-Werke GmbH & Co. KG (sales@ika.de, formulario ika.com/owa/ika/content.contact_form) los archivos oficiales de los tres modelos.';
const IKA_EVIDENCE = 'Registro de bloqueo 403 del 2026-09-07; PDF Disperser_Brochure_IWS_EN_wop_screen.pdf sin cláusula de reutilización';
const ADAM_TERMS =
  'Sin base de permiso para fotografía. La página legal del fabricante (adamequipment.com/legal-and-privacy) sólo reserva derechos («All rights reserved»). El Adam Brand Toolkit (adamequipment.com/toolkit) cede a distribuidores logotipos, banners y textos, no fotografías de producto; la Dealer Zone exige alta como distribuidor autorizado. Las imágenes se sirven desde adamequipment.sirv.com con protección de enlace directo. Pedir a marketing@adamequipment.com, con copia a sales@adamequipment.com.';
const ADAM_EVIDENCE =
  'https://adamequipment.com/legal-and-privacy y https://adamequipment.com/toolkit (leídos el 2026-09-07)';
const LOESER_TERMS =
  'Sin base de permiso. El Impressum del fabricante (loeser-osmometer.de/impressum-eng.html) dice: «it is not allowed to reproduce, save or use in every other way the contents from this side - also not in extracts - without the agreement from Löser Messtechnik». No hay área de prensa ni de distribuidores. Pedir a info@loeser-osmometer.de (Axel Löser, Berlín) los cuatro archivos y, si existen, versiones de mayor resolución.';
const LOESER_EVIDENCE = 'http://www.loeser-osmometer.de/impressum-eng.html (leído el 2026-09-07)';
const SERVA_TERMS =
  'Sin base de permiso. serva.de no publica términos de reutilización: sólo «© SERVA Electrophoresis GmbH» en el pie; el Impressum, las condiciones de venta y el centro de descargas no tratan las imágenes, y no hay portal de prensa ni de distribuidores. Las imágenes de producto son de 126 a 500 px. Pedir a info@licorbio.com (SERVA opera bajo LICORbio desde 2025-07) los archivos en alta resolución y la autorización escrita.';
const SERVA_EVIDENCE =
  'https://www.serva.de/enDE/216_Impressum.html y https://www.serva.de/enDE/2_Download_Center.html (leídos el 2026-09-07)';

interface PendingSpec {
  id: string;
  brandId: ApprovedBrandId;
  productId?: string;
  modelId?: string;
  model: string;
  scope: ProductImageScope;
  officialUrl: string;
  imageSourceUrl: string;
  alt: string;
  note: string;
}

function pending(
  spec: PendingSpec,
  permissionBasis: string,
  permissionEvidence: string,
): ProductImageRecord {
  return {
    ...spec,
    sourceType: 'entrega-fabricante',
    permissionBasis,
    permissionEvidence,
    masterPath: null,
    masterWidth: null,
    masterHeight: null,
    derivatives: null,
    verifiedOn: VERIFIED_ON,
    status: 'ASSET_PERMISSION_NEEDED',
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
    sourceType: 'activo-origenlab',
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
    sourceType: 'activo-origenlab',
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
    sourceType: 'activo-origenlab',
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
    sourceType: 'activo-origenlab',
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
    sourceType: 'activo-origenlab',
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
    sourceType: 'activo-origenlab',
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

  /* -- Hielscher Ultrasonics · sonicación (pendiente de permiso) ---------- */
  ...[
    {
      id: 'hielscher-up100h',
      modelId: 'hielscher-up100h',
      model: 'UP100H',
      officialUrl: 'https://www.hielscher.com/100h_p.htm',
      imageSourceUrl: 'https://www.hielscher.com/image/up100h_02_p0500.jpg',
      alt: 'Sonicador de mano Hielscher UP100H con sonotrodo',
      note: 'Fotografía del modelo exacto en la página oficial (alt del fabricante nombra al UP100H). Pedir up100h_02_p0500.jpg y up100h_05_p1000.jpg.',
    },
    {
      id: 'hielscher-up200st',
      modelId: 'hielscher-up200st',
      model: 'UP200St',
      officialUrl: 'https://www.hielscher.com/up200st-powerful-ultrasonic-lab-homogenizer.htm',
      imageSourceUrl: 'https://www.hielscher.com/wp-content/uploads/UP200St_silver_cut.png',
      alt: 'Sonicador de laboratorio Hielscher UP200St con transductor y generador',
      note: 'Recorte de producto del modelo exacto en la página oficial. Pedir UP200St_silver_cut.png y up200st-s26d2-vial-p300-opt.jpg.',
    },
    {
      id: 'hielscher-up400st',
      modelId: 'hielscher-up400st',
      model: 'UP400St',
      officialUrl: 'https://www.hielscher.com/up400st-powerful-ultrasonicator.htm',
      imageSourceUrl:
        'https://www.hielscher.com/wp-content/uploads/Ultrasonic_Homogenizer_UP400St_S24d22D-05-p1000.jpg',
      alt: 'Sonicador Hielscher UP400St montado en soporte con sonotrodo S24d22D',
      note: 'Fotografía del modelo exacto en la página oficial, de unos 1.000 px de alto.',
    },
    {
      id: 'hielscher-uip2000hdt',
      modelId: 'hielscher-uip2000hdt',
      model: 'UIP2000hdT',
      officialUrl:
        'https://www.hielscher.com/uip2000hdt-2000-watts-powerful-industrial-ultrasonicator-for-full-process-control.htm',
      imageSourceUrl:
        'https://www.hielscher.com/wp-content/uploads/UIP2000hdT-sonicator-transducer-generator-HielscherUltrasonics-400x308.jpg',
      alt: 'Procesador ultrasónico industrial Hielscher UIP2000hdT, transductor y generador',
      note: 'Fotografía de equipo solo (transductor y generador) del modelo exacto. Pedir el original sin sufijo de tamaño.',
    },
  ].map((spec) =>
    pending({ ...spec, brandId: 'hielscher', scope: 'modelo-exacto' }, HIELSCHER_TERMS, HIELSCHER_EVIDENCE),
  ),

  /* -- IKA · dispersión (pendiente de permiso; sitio inaccesible por máquina) */
  ...[
    {
      id: 'ika-t10-basic',
      modelId: 'ika-t10-basic',
      model: 'T 10 basic ULTRA-TURRAX',
      officialUrl: 'https://www.ika.com/en/Products-LabEq/Dispersers-pg177/T-10-basic-ULTRA-TURRAX-3737000/',
      alt: 'Dispersor de mano IKA T 10 basic ULTRA-TURRAX',
    },
    {
      id: 'ika-t18-digital',
      modelId: 'ika-t18-digital',
      model: 'T 18 digital ULTRA-TURRAX',
      officialUrl: 'https://www.ika.com/en/Products-LabEq/Dispersers-pg177/T-18-digital-ULTRA-TURRAX-3720000/',
      alt: 'Dispersor de sobremesa IKA T 18 digital ULTRA-TURRAX en su soporte',
    },
    {
      id: 'ika-t25-digital',
      modelId: 'ika-t25-digital',
      model: 'T 25 digital ULTRA-TURRAX',
      officialUrl: 'https://www.ika.com/en/Products-LabEq/Dispersers-pg177/T-25-digital-ULTRA-TURRAX-3725000/',
      alt: 'Dispersor de sobremesa IKA T 25 digital ULTRA-TURRAX en su soporte',
    },
  ].map((spec) =>
    pending(
      {
        ...spec,
        brandId: 'ika',
        scope: 'modelo-exacto',
        imageSourceUrl: 'https://www.ika.com/ika/pdf/flyer-catalog/Disperser_Brochure_IWS_EN_wop_screen.pdf',
        note: 'La página del modelo no se pudo leer por máquina (403); el folleto oficial contiene la fotografía del modelo pero no autoriza su reutilización. No se extrae nada del PDF.',
      },
      IKA_TERMS,
      IKA_EVIDENCE,
    ),
  ),

  /* -- Adam Equipment · pesaje y humedad (pendiente de permiso) ----------- */
  ...[
    {
      id: 'adam-pmb',
      modelId: 'adam-pmb',
      model: 'PMB',
      officialUrl: 'https://adamequipment.com/pmb-moisture-analyzers-us.html',
      imageSourceUrl:
        'https://adamequipment.sirv.com/magento/catalog/product/i/m/images-w_1100,h_1100,c_fit,dn_72-kphbkj5ysyvxaojhtb4f-pmb_moisture_analysers.jpg',
      alt: 'Analizador de humedad Adam Equipment PMB, imagen representativa de la familia',
      note: 'La imagen de familia muestra un PMB 53 según el alt del fabricante; se rotularía como representativa de la familia. Existen fotografías por modelo (pmb_53, pmb_163, pmb_202) en el mismo CDN.',
    },
    {
      id: 'adam-solis',
      modelId: 'adam-solis',
      model: 'Solis',
      officialUrl: 'https://adamequipment.com/solis-analytical-and-semi-micro-balances-us.html',
      imageSourceUrl:
        'https://adamequipment.sirv.com/magento/catalog/product/i/m/images-w_1100,h_1100,c_fit,dn_72-nrpvrffcemgjax5phfjb-solis_analytical_and_semi-micro_balances.jpg',
      alt: 'Balanza analítica Adam Equipment Solis con cámara de pesaje cerrada, imagen representativa de la familia',
      note: 'La imagen de familia muestra una SAB 125i según el alt del fabricante. Existen fotografías por modelo (sab_124e a sab_514i) en el mismo CDN.',
    },
    {
      id: 'adam-highland',
      modelId: 'adam-highland',
      model: 'Highland',
      officialUrl: 'https://adamequipment.com/highland-portable-precision-balances-us.html',
      imageSourceUrl:
        'https://adamequipment.sirv.com/magento/catalog/product/i/m/images-w_1100,h_1100,c_fit,dn_72-rzygdtzjweywtaajdd3s-highland_portable_precision_balances.jpg',
      alt: 'Balanza de precisión portátil Adam Equipment Highland, imagen representativa de la familia',
      note: 'Existen fotografías por modelo (hcb_123 a hcb_6001) en el mismo CDN.',
    },
  ].map((spec) =>
    pending({ ...spec, brandId: 'adam-equipment', scope: 'familia-representativa' }, ADAM_TERMS, ADAM_EVIDENCE),
  ),

  /* -- Löser Messtechnik · osmometría (pendiente de permiso) -------------- */
  ...[
    {
      id: 'loeser-osmometer-basic',
      modelId: 'loeser-osmometer-basic',
      model: 'Osmometer basic',
      officialUrl: 'http://www.loeser-osmometer.de/typ7-eng.html',
      imageSourceUrl: 'http://www.loeser-osmometer.de/Tp7E.jpg',
      alt: 'Osmómetro crioscópico Löser Osmometer basic',
      note: 'JPEG de 400 × 500 px en la página oficial. Conviene pedir un original de mayor resolución.',
    },
    {
      id: 'loeser-i-osmometer-basic',
      modelId: 'loeser-i-osmometer-basic',
      model: 'i Osmometer basic',
      officialUrl: 'http://www.loeser-osmometer.de/typ7i-eng.html',
      imageSourceUrl: 'http://www.loeser-osmometer.de/Tp7iE.jpg',
      alt: 'Osmómetro automático Löser i Osmometer basic',
      note: 'JPEG de 400 × 501 px en la página oficial.',
    },
    {
      id: 'loeser-i-osmometer',
      modelId: 'loeser-i-osmometer',
      model: 'i Osmometer',
      officialUrl: 'http://www.loeser-osmometer.de/typ16-eng.html',
      imageSourceUrl: 'http://www.loeser-osmometer.de/Tp16E-New.jpg',
      alt: 'Osmómetro automático Löser i Osmometer con impresora y lector integrados',
      note: 'JPEG de 400 × 500 px en la página oficial.',
    },
    {
      id: 'loeser-i-cryometer',
      modelId: 'loeser-i-cryometer',
      model: 'i Cryometer',
      officialUrl: 'http://www.loeser-osmometer.de/typ21-eng.html',
      imageSourceUrl: 'http://www.loeser-osmometer.de/Tp21E-New.jpg',
      alt: 'Criómetro automático Löser i Cryometer',
      note: 'JPEG de 400 × 500 px en la página oficial.',
    },
  ].map((spec) =>
    pending({ ...spec, brandId: 'loeser', scope: 'modelo-exacto' }, LOESER_TERMS, LOESER_EVIDENCE),
  ),

  /* -- SERVA Electrophoresis · electroforesis (pendiente de permiso) ------ */
  ...[
    {
      id: 'serva-bluevertical-prime',
      modelId: 'serva-bluevertical-prime',
      model: 'BlueVertical PRiME',
      scope: 'modelo-exacto' as const,
      officialUrl:
        'https://www.serva.de/enDE/ProductDetails/4741_BV-104_BlueVertical_TM_PRiME_TM_Mini_Slab_Gel_Unit_0_0.html',
      imageSourceUrl: 'https://www.serva.de/images/imgProd/190/BV-104-s.jpg',
      alt: 'Cubeta vertical para minigeles SERVA BlueVertical PRiME',
      note: 'JPEG de 500 × 374 px, única imagen publicada del producto.',
    },
    {
      id: 'serva-hpe-bluehorizon',
      modelId: 'serva-hpe-bluehorizon',
      model: 'HPE BlueHorizon',
      scope: 'modelo-exacto' as const,
      officialUrl: 'https://www.serva.de/enDE/ProductDetails/5120_HPE-BH_HPE_TM_BlueHorizon_TM_212_457.html',
      imageSourceUrl: 'https://www.serva.de/images/imgProd/190/HPE-BH-s.jpg',
      alt: 'Cámara horizontal de lecho plano SERVA HPE BlueHorizon',
      note: 'JPEG de 400 × 256 px, única imagen publicada del producto.',
    },
    {
      id: 'serva-bluemarine-100',
      modelId: 'serva-bluemarine-100',
      model: 'BlueMarine 100',
      scope: 'modelo-exacto' as const,
      officialUrl: 'https://www.serva.de/enDE/ProductDetails/2291_BM-100_BlueMarine_TM_100_0_208.html',
      imageSourceUrl: 'https://www.serva.de/images/imgProd/190/BM-100-s.jpg',
      alt: 'Cámara submarina de agarosa SERVA BlueMarine 100',
      note: 'JPEG de 169 × 107 px: demasiado pequeño para publicar aunque llegue el permiso. Pedir el original.',
    },
    {
      id: 'serva-bluepower',
      modelId: 'serva-bluepower',
      model: 'BluePower',
      scope: 'familia-representativa' as const,
      officialUrl:
        'https://www.serva.de/enDE/Catalog/459_Laboratory_Equipment_Electrophoresis_Devices_Power_Supplies_212_449.html',
      imageSourceUrl: 'https://www.serva.de/images/imgProd/190/BP-600-PRI-s.jpg',
      alt: 'Fuente de alimentación SERVA BluePower 600 PRIME, imagen representativa de la familia BluePower',
      note: 'La fotografía es de la BluePower 600 PRIME (400 × 267 px) y representaría a la familia. Las otras tres fuentes tienen imagen propia en el mismo directorio.',
    },
    {
      id: 'serva-blueslick-42500',
      productId: 'serva-blueslick-42500',
      model: 'BlueSlick',
      scope: 'modelo-exacto' as const,
      officialUrl: 'https://www.serva.de/enDE/ProductDetails/158_42500_BlueSlick_TM_0_214.html',
      imageSourceUrl: 'https://www.serva.de/images/imgProd/190/42500-s.jpg',
      alt: 'Pulverizador de reactivo SERVA BlueSlick de 250 ml',
      note: 'JPEG de 126 × 194 px: demasiado pequeño para publicar aunque llegue el permiso. Pedir el original.',
    },
  ].map((spec) => pending({ ...spec, brandId: 'serva' }, SERVA_TERMS, SERVA_EVIDENCE)),
];

/** Imagen publicable de un producto con ficha propia. */
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
