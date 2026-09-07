/**
 * Registro de fuentes, procedencia y permisos.
 *
 * Una fila por marca publicada. Es la respuesta única a cuatro preguntas que
 * antes vivían repartidas entre `docs/product-assets.md`, comentarios sueltos y
 * la cabeza de quien hizo el último cambio:
 *
 *   1. ¿Cuál es la página oficial del fabricante para esta familia?
 *   2. ¿Existe un PDF alojado por el propio fabricante?
 *   3. ¿De dónde sale cada imagen y con qué base se puede publicar?
 *   4. ¿Cuándo se comprobó por última vez que todo eso resuelve?
 *
 * Reglas que sostiene este archivo:
 *
 * - Sólo dominios del fabricante. Nada de Scribd, marketplaces, revendedores ni
 *   catálogos raspados: si el fabricante no lo publica, la fila queda en
 *   `PDF_NOT_FOUND` y no se enlaza nada.
 * - Un PDF del fabricante se enlaza, nunca se rehospeda. Descargarlo y servirlo
 *   desde origenlab.cl lo presentaría como documento propio.
 * - Que una imagen sea visible en público no implica permiso de reutilización.
 *   Sin base documentada, la fila queda en `ASSET_PERMISSION_NEEDED` y el sitio
 *   **no** incrusta la imagen: compone la familia sin fotografía. La base de la
 *   fotografía de producto de las seis marcas es hoy la autorización previa del
 *   fabricante confirmada por el titular del negocio el 2026-09-07; se registra
 *   por imagen en `productImages.ts`, y permiso y procedencia van separados.
 * - Ninguna imagen de un fabricante ilustra a otro. Jamás.
 *
 * `lastVerified` es la fecha en que se comprobó, con petición real, que cada
 * URL de la fila responde. Lo reproduce `npm run verify:sources`, que es un
 * script de red y por eso no forma parte de `npm run validate`.
 *
 * Este registro es por marca: la fuente oficial de la familia, el permiso de
 * imagen y el estado. La fuente **por modelo** vive en `src/data/brandModels.ts`,
 * con su propia URL y su propia fecha de lectura, porque un modelo puede tener
 * ficha PDF propia mientras la familia no la tiene, y al revés. La procedencia
 * y el permiso **por fotografía** viven en `src/data/productImages.ts`, que es
 * lo único que una plantilla puede renderizar como imagen de equipo.
 */

export type SourceStatus =
  /** Página oficial comprobada y, si existe, PDF oficial comprobado. */
  | 'VERIFIED'
  /** Página oficial comprobada; el fabricante no publica PDF descargable. */
  | 'PDF_NOT_FOUND'
  /** Falta base documentada para reproducir una imagen. No se incrusta. */
  | 'ASSET_PERMISSION_NEEDED'
  /** Falta contenido que sólo puede aportar el negocio. */
  | 'CONTENT_NEEDED';

export interface BrandSource {
  /** Id de `brands.ts`. */
  brandId: string;
  /** Familia de `equipmentScope.ts`. */
  familyId: string;
  /** Modelo o familia concreta, cuando la fila apunta a una. */
  model: string | null;
  /** Página oficial del fabricante para esta familia. */
  officialUrl: string;
  /** PDF alojado por el fabricante, o `null` si no publica ninguno. */
  officialPdfUrl: string | null;
  /** De dónde sale la imagen local, o `null` si no hay imagen publicada. */
  imageSourceUrl: string | null;
  /** Ruta del activo local servido por el sitio, o `null`. */
  localAssetPath: string | null;
  /** Base por la que se puede publicar la imagen. Explícita o ausente. */
  permissionBasis: string | null;
  /** Fecha de la última comprobación de que las URL responden (ISO). */
  lastVerified: string;
  status: SourceStatus;
  /** Qué falta, cuando falta algo. */
  note?: string;
}

const VERIFIED_ON = '2026-09-07';

/**
 * Nota común a los logotipos. El sitio reproduce los seis a una tinta para
 * identificar al fabricante, que es uso nominativo, pero el permiso escrito de
 * reproducción sigue sin pedirse: TODO abierto desde 2026-05.
 */
const LOGO_PERMISSION_PENDING =
  'Logotipo reproducido a una tinta para identificar al fabricante. Permiso escrito de reproducción pendiente (docs/design/CONTENT_NEEDED.md).';

export const brandSources: readonly BrandSource[] = [
  {
    brandId: 'hielscher',
    familyId: 'sonicacion',
    model: null,
    officialUrl: 'https://www.hielscher.com/products.htm',
    officialPdfUrl:
      'https://www.hielscher.com/wp-content/uploads/Catalogue-Hielscher-Ultrasonics-eng-v.02.2025.pdf',
    imageSourceUrl: 'https://www.hielscher.com/wp-content/uploads/hielscher-logo2.svg',
    localAssetPath: '/brands/hielscher-logo.png',
    permissionBasis: LOGO_PERMISSION_PENDING,
    lastVerified: VERIFIED_ON,
    status: 'ASSET_PERMISSION_NEEDED',
    note: 'Página y catálogo PDF oficiales comprobados. Fotografía de equipo: publicada con autorización previa del fabricante confirmada por el titular del negocio el 2026-09-07; procedencia y permiso por imagen en productImages.ts. Las cuatro fotografías de modelo salen de la página oficial de cada modelo.',
  },
  {
    brandId: 'ortoalresa',
    familyId: 'centrifugacion',
    model: 'Biocen 22, Biocen 22 R, Digicen 22, Digicen 22 R, Consul 22',
    officialUrl: 'https://ortoalresa.com/en/products/',
    officialPdfUrl: 'https://ortoalresa.com/catalogo_producto/Catalogo_serie_Digicen_22_ESP.pdf',
    imageSourceUrl: 'https://ortoalresa.com/imagen_producto/',
    localAssetPath: '/products/ortoalresa/',
    permissionBasis:
      'Imágenes de producto en poder de OrigenLab desde 2026-05 con procedencia registrada por modelo en docs/product-assets.md, publicadas con autorización previa del fabricante confirmada por el titular del negocio el 2026-09-07 (productImages.ts). El permiso del logotipo sigue en la fila general de logotipos.',
    lastVerified: VERIFIED_ON,
    status: 'ASSET_PERMISSION_NEEDED',
    note: 'La procedencia por imagen vive en productImages.ts. El PDF por modelo vive en products.ts; aquí figura el de la serie Digicen. Fotografía de equipo: publicada con autorización previa del fabricante confirmada por el titular del negocio el 2026-09-07; procedencia y permiso por imagen en productImages.ts.',
  },
  {
    brandId: 'ika',
    familyId: 'dispersion-homogeneizacion',
    model: null,
    officialUrl: 'https://www.ika.com/en/Products-LabEq/Dispersers-pg177/',
    officialPdfUrl:
      'https://www.ika.com/ika/pdf/flyer-catalog/Disperser_Brochure_IWS_EN_wop_screen.pdf',
    imageSourceUrl: 'https://www.ika.com/ika/images/Logo-IKA-without-Claim.png',
    localAssetPath: '/brands/ika-logo.png',
    permissionBasis: LOGO_PERMISSION_PENDING,
    lastVerified: VERIFIED_ON,
    status: 'ASSET_PERMISSION_NEEDED',
    note: 'El PDF oficial responde (200, application/pdf). Las páginas HTML de ika.com devuelven 403 con el interstitial de Cloudflare a todo cliente automatizado, incluido un navegador headless real, de modo que la URL de la página **no está comprobada por máquina**: queda pendiente de abrirla una persona. El dominio y la ruta se dan por vivos porque el PDF cuelga del mismo servidor. Fotografía de equipo: publicada con autorización previa del fabricante confirmada por el titular del negocio el 2026-09-07; procedencia y permiso por imagen en productImages.ts. Las tres fotografías de modelo salen del folleto oficial de dispersores (PDF), porque las páginas HTML no se pueden leer por máquina.',
  },
  {
    brandId: 'adam-equipment',
    familyId: 'pesaje-humedad',
    model: null,
    officialUrl: 'https://adamequipment.com/products.html',
    officialPdfUrl: 'https://adamequipment.com/media/docs/data_sheets/PMB-DS-A4-EN.pdf',
    imageSourceUrl: 'https://adamequipment.com/media/logo/default/Adam_Logo_2.png',
    localAssetPath: '/brands/adam-equipment-logo.png',
    permissionBasis: LOGO_PERMISSION_PENDING,
    lastVerified: VERIFIED_ON,
    status: 'ASSET_PERMISSION_NEEDED',
    note: 'Índice de productos del fabricante y ficha PDF de la serie PMB comprobados. El enlace de familia pasó de la página de analizadores de humedad al índice de productos, porque la familia publicada incluye ahora balanzas analíticas y de precisión. Fotografía de equipo: publicada con autorización previa del fabricante confirmada por el titular del negocio el 2026-09-07; procedencia y permiso por imagen en productImages.ts. Las tres imágenes son de familia (rotuladas como representativas) y salen de las fichas técnicas PDF oficiales y de la página oficial de la familia Solis; el CDN de imágenes del sitio rechaza la descarga directa y no se eludió.',
  },
  {
    brandId: 'loeser',
    familyId: 'osmometria',
    model: 'Osmometer basic, i Osmometer basic, i Osmometer, i Cryometer',
    officialUrl: 'http://www.loeser-osmometer.de/produkte-eng.html',
    officialPdfUrl: null,
    imageSourceUrl: 'http://www.loeser-osmometer.de/LoeLogo.jpg',
    localAssetPath: '/brands/loeser-logo.png',
    permissionBasis: LOGO_PERMISSION_PENDING,
    lastVerified: VERIFIED_ON,
    status: 'PDF_NOT_FOUND',
    note: 'El fabricante no publica PDF descargable: los folletos se piden por formulario (anfrage-eng.html). Su sitio no ofrece HTTPS (el servidor rechaza el saludo TLS), así que el enlace oficial es http. El logotipo original es un JPEG de 70x70; conviene pedir uno vectorial. Fotografía de equipo: publicada con autorización previa del fabricante confirmada por el titular del negocio el 2026-09-07; procedencia y permiso por imagen en productImages.ts. Las cuatro fotografías de modelo (400 × 500 px) salen de la página oficial de cada modelo y se muestran sin ampliar.',
  },
  {
    brandId: 'serva',
    familyId: 'electroforesis',
    model: null,
    officialUrl: 'https://www.serva.de/enDE/19_Download_Center_Electrophoresis_by_SERVA_Catalog.html',
    officialPdfUrl: 'https://www.serva.de/www_root/documents/Electrophoresis%20by%20SERVA_web.pdf',
    imageSourceUrl: 'https://www.serva.de/lib/images/serva-logo.png',
    localAssetPath: '/brands/serva-logo.png',
    permissionBasis: LOGO_PERMISSION_PENDING,
    lastVerified: VERIFIED_ON,
    status: 'ASSET_PERMISSION_NEEDED',
    note: 'Centro de descargas y catálogo de electroforesis comprobados. Fotografía de equipo: publicada con autorización previa del fabricante confirmada por el titular del negocio el 2026-09-07; procedencia y permiso por imagen en productImages.ts. Las cinco fotografías salen de la página oficial de cada producto, miden entre 126 y 500 px y se muestran sin ampliar; conviene pedir a LICORbio originales en alta resolución.',
  },
];

export function sourceForBrand(brandId: string): BrandSource | undefined {
  return brandSources.find((entry) => entry.brandId === brandId);
}

export function sourceForFamily(familyId: string): BrandSource | undefined {
  return brandSources.find((entry) => entry.familyId === familyId);
}

/** Filas cuya imagen no puede publicarse todavía. Para auditoría del registro. */
export function assetsPendingPermission(): BrandSource[] {
  return brandSources.filter((entry) => entry.status === 'ASSET_PERMISSION_NEEDED');
}
