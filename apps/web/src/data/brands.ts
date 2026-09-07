/**
 * Marcas publicadas. Lista cerrada de seis.
 *
 * `APPROVED_BRAND_IDS` es la única lista de marcas que el sitio público puede
 * mostrar. La comparte todo: portada, páginas de marca, navegación, catálogo,
 * buscador, sitemap, datos estructurados y pie. `validate:brands` comprueba que
 * este archivo, los logotipos de `public/brands/`, el registro de fuentes, el
 * registro de modelos y el HTML construido coincidan exactamente con ella, de
 * modo que una marca retirada no pueda reaparecer sin que falle la validación.
 *
 * Procedencia de la lista y de la familia de cada marca: revisión de marca y
 * portada del 2026-09-06, en la que el negocio fijó las seis marcas aprobadas y
 * qué fabrica cada una.
 *
 * # Dos ejes independientes, y conviene no confundirlos
 *
 * La versión anterior tenía una sola bandera, `catalogPublished`, y con ella
 * decidía dos cosas a la vez: si la marca tenía página propia y si se podía
 * describir cómo la vende OrigenLab. Eso dejaba a cuatro de las seis sin
 * ninguna página, y con ellas a cuatro familias de equipo enteras, cuando lo
 * único que falta de esas cuatro es la confirmación comercial.
 *
 * Ahora son dos ejes:
 *
 * - `editorialPublished` — hay material editorial verificado suficiente para
 *   una página de marca completa: qué fabrica, qué resuelve, modelos con
 *   documentación oficial del fabricante y enlaces comprobados. Las seis lo
 *   tienen, y por eso las seis tienen `/marcas/{slug}/`. El material vive en
 *   este archivo y en `src/data/brandModels.ts`.
 *
 * - `commercialScopeConfirmed` — el negocio ha confirmado por escrito en qué
 *   condiciones trabaja la línea. Sólo dos lo tienen. Es lo único que habilita
 *   `commercialNote`, y `validate:catalog` lo comprueba. Sin él la página dice
 *   qué fabrica el fabricante y que OrigenLab la cotiza, nunca en qué calidad.
 *
 * Las seis se presentan como marcas con las que OrigenLab trabaja y que puede
 * cotizar. Ninguna se describe como representación oficial, distribución
 * exclusiva, territorial ni certificación: `validate:catalog` bloquea esa
 * redacción en los datos y en las plantillas.
 *
 * Lo que sigue sin publicarse:
 *
 * - Precios, plazos y condiciones de las cuatro marcas sin alcance confirmado.
 * - Fotografía de producto sin procedencia y permiso documentados: ver
 *   `src/data/sourceRegistry.ts`.
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

/**
 * Composición de la página de marca. Las seis comparten el mismo estándar de
 * calidad (qué fabrica, familia, modelos con documentación oficial, condición
 * comercial y cierre) y ninguna comparte la disposición: repetir seis veces la
 * misma plantilla convertiría el catálogo en un formulario.
 */
export type BrandLayout =
  /** Escalera de potencia: los modelos ordenados por el salto de escala. */
  | 'escala'
  /** La fotografía aprobada manda y los modelos van en tabla comparable. */
  | 'fotografia'
  /** El mecanismo primero: un diagrama explica el principio y luego los modelos. */
  | 'mecanismo'
  /** Rejilla de medición: legibilidad y rango como eje de lectura. */
  | 'medicion'
  /** Ficha de instrumento único: un solo cuadro técnico, cuatro variantes. */
  | 'instrumento'
  /** Sistema: equipos y consumibles que se cotizan juntos. */
  | 'sistema';

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
   * altura que los cuadrados para pesar lo mismo en el riel.
   */
  logoDisplayHeight: number;
  /** Familia de equipo que fabrica. Id de `equipmentScope.ts`. */
  familyId: string;
  /** Qué fabrica, en una línea. Confirmado por el negocio el 2026-09-06. */
  listSummary: string;
  /** Hay página de marca completa: material editorial verificado. */
  editorialPublished: boolean;
  /** El negocio confirmó por escrito en qué condiciones trabaja la línea. */
  commercialScopeConfirmed: boolean;
  /** Disposición de la página de marca. Una distinta por marca. */
  layout: BrandLayout;
  /** Qué fabrica y cómo, en un párrafo. Redactado desde la fuente oficial. */
  summary: string;
  /** Segundo párrafo, cuando el principio técnico necesita explicarse. */
  brandIntro?: string;
  applicationAreas: readonly string[];
  /** Condiciones comerciales. Sólo con `commercialScopeConfirmed`. */
  commercialNote?: string;
  /** Subtítulo de la página de marca. */
  pageSubtitle: string;
  /** Descripción SEO de la página de marca (máx. 158 caracteres). */
  metaDescription: string;
  manufacturerAttribution: string;
}

/**
 * Atribución común. El sitio publica especificaciones que redactó a partir de
 * la documentación pública del fabricante, y tiene que decir de quién son.
 */
const MANUFACTURER_SPECS =
  'Datos de producto según la documentación pública del fabricante. OrigenLab no fabrica estos equipos.' as const;

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
    editorialPublished: true,
    commercialScopeConfirmed: false,
    layout: 'escala',
    pageSubtitle: 'Procesadores ultrasónicos de sonda, del tubo de ensayo al proceso continuo',
    metaDescription:
      'Sonicadores Hielscher Ultrasonics: UP100H, UP200St, UP400St y UIP2000hdT, con potencia, frecuencia y volumen de trabajo por modelo.',
    summary:
      'Hielscher Ultrasonics fabrica procesadores ultrasónicos de sonda. Un generador convierte la corriente de red en oscilación de alta frecuencia, el transductor la transmite a un sonotrodo sumergido en la muestra y la cavitación que se forma en el líquido rompe células, desaglomera partículas y mezcla fases que no se mezclan solas.',
    brandIntro:
      'La línea cubre una escala poco habitual con la misma tecnología: el mismo principio que trata 100 µl en un tubo de ensayo trata varios litros por minuto en línea. Eso permite desarrollar un método en el banco y escalarlo después sin cambiar de técnica, que es la razón por la que un laboratorio de investigación y una planta piloto suelen comprar la misma marca.',
    applicationAreas: [
      'Lisis celular y extracción',
      'Desaglomeración de nanomateriales',
      'Emulsión y homogeneización',
      'Desgasificación de líquidos',
      'Sonoquímica',
    ],
    manufacturerAttribution: MANUFACTURER_SPECS,
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
    editorialPublished: true,
    commercialScopeConfirmed: true,
    layout: 'fotografia',
    pageSubtitle: 'Centrífugas de laboratorio para aplicaciones generales y especializadas',
    metaDescription:
      'Centrífugas Ortoalresa disponibles para cotización a través de OrigenLab: cinco modelos de catálogo con ficha técnica del fabricante.',
    summary:
      'Ortoalresa, fabricante europeo de centrífugas de laboratorio, ofrece equipos para aplicaciones generales y necesidades específicas de laboratorio. Es la única familia del catálogo con fotografía de producto y ficha completa publicadas en este sitio.',
    applicationAreas: [
      'Preparación de muestras',
      'Microtubos y tubos cónicos',
      'Bioprocesos',
      'Laboratorio clínico y control de calidad',
    ],
    manufacturerAttribution:
      'Información de producto según documentación pública del fabricante. OrigenLab no fabrica estos equipos.',
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
    editorialPublished: true,
    commercialScopeConfirmed: false,
    layout: 'mecanismo',
    pageSubtitle: 'Dispersores de rotor y estátor de la línea ULTRA-TURRAX',
    metaDescription:
      'Dispersores IKA ULTRA-TURRAX: T 10 basic, T 18 digital y T 25 digital, con volumen de trabajo, revoluciones y herramienta dispersora.',
    summary:
      'IKA fabrica dispersores y homogeneizadores de rotor y estátor. El medio entra por el eje del cabezal, sale forzado a través de las ranuras del conjunto y la velocidad periférica, junto con el juego mínimo entre rotor y estátor, genera el esfuerzo de corte que reduce el tamaño de partícula.',
    brandIntro:
      'El equipo y la herramienta dispersora se eligen por separado, y esa es la decisión que de verdad determina el resultado: cada elemento declara su velocidad periférica y la finura que alcanza en suspensión y en emulsión. Un mismo T 25 digital rinde distinto con un elemento de 8 mm que con uno de 25 mm.',
    applicationAreas: [
      'Homogeneización de tejidos y alimentos',
      'Emulsión y suspensión',
      'Preparación de muestra para análisis',
      'Dispersión de sólidos en líquido',
    ],
    manufacturerAttribution: MANUFACTURER_SPECS,
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
    editorialPublished: true,
    commercialScopeConfirmed: false,
    layout: 'medicion',
    pageSubtitle: 'Balanzas analíticas, balanzas de precisión y análisis de humedad',
    metaDescription:
      'Balanzas Adam Equipment y analizadores de humedad PMB: capacidad, legibilidad y calibración por familia, con ficha oficial del fabricante.',
    summary:
      'Adam Equipment fabrica instrumentos de pesaje para laboratorio. La línea va desde balanzas analíticas y semimicro, con legibilidad de 0,01 mg y cámara de pesaje cerrada, hasta balanzas de precisión portátiles y analizadores de humedad que pesan y calientan la muestra en el mismo plato.',
    brandIntro:
      'En pesaje la decisión no es la capacidad sino la legibilidad que exige el método: una diferencia de un orden de magnitud en el último dígito cambia la balanza, el entorno que necesita y el procedimiento de calibración. Por eso las familias se leen por pares de capacidad y legibilidad, no por número de modelo.',
    applicationAreas: [
      'Pesaje analítico y formulación',
      'Control de calidad y ensayo de rutina',
      'Determinación de humedad en alimentos',
      'Conteo de piezas y pesaje por porcentaje',
    ],
    manufacturerAttribution: MANUFACTURER_SPECS,
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
    editorialPublished: true,
    commercialScopeConfirmed: false,
    layout: 'instrumento',
    pageSubtitle: 'Osmómetros y criómetros por descenso del punto de congelación',
    metaDescription:
      'Osmómetros Löser Messtechnik: Osmometer basic, i Osmometer basic, i Osmometer e i Cryometer, con volumen de muestra, rango y reproducibilidad.',
    summary:
      'Löser Messtechnik fabrica osmómetros y criómetros de sobremesa. Los cuatro instrumentos miden lo mismo y de la misma forma: sobreenfrían la muestra, provocan la cristalización y leen la temperatura de congelación, que desciende en proporción a las partículas disueltas.',
    brandIntro:
      'Toda la línea comparte volumen de muestra, tiempo de medición y reproducibilidad. Lo que cambia entre un modelo y el siguiente es la automatización del ciclo y la documentación del resultado: cuánta memoria guarda, si imprime, si lee el código de la muestra y si registra usuario y estado de calibración. La medición no cambia; el registro sí.',
    applicationAreas: [
      'Osmolalidad en suero, plasma y orina',
      'Control de soluciones parenterales',
      'Investigación en fisiología y nutrición',
      'Crioscopía de soluciones no acuosas',
    ],
    manufacturerAttribution: MANUFACTURER_SPECS,
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
      'Equipos, reactivos e insumos verificados para electroforesis y preparación de muestras.',
    editorialPublished: true,
    commercialScopeConfirmed: true,
    layout: 'sistema',
    pageSubtitle: 'Cubetas, fuentes de alimentación y reactivos para electroforesis',
    metaDescription:
      'Electroforesis SERVA a través de OrigenLab: cubeta vertical BlueVertical PRiME, sistema horizontal BlueHorizon, fuentes BluePower y BlueSlick.',
    summary:
      'SERVA Electrophoresis fabrica y distribuye la cadena completa de la electroforesis en gel: la cubeta donde corre el gel, la fuente de alimentación que impone el campo, los reactivos con que se cuela y se tiñe, y los consumibles que hacen reproducible el montaje.',
    brandIntro:
      'Una separación se decide en tres elecciones que tienen que encajar: el formato del gel, la cubeta que lo acepta y la fuente capaz de sostener el voltaje que ese formato pide. Un equipo aislado no resuelve nada si la fuente no llega, y por eso la línea se cotiza como sistema y no como pieza suelta.',
    applicationAreas: [
      'SDS-PAGE y electroforesis nativa',
      'Enfoque isoeléctrico y electroforesis bidimensional',
      'Electroforesis de ácidos nucleicos en agarosa',
      'Transferencia y tinción de geles',
    ],
    manufacturerAttribution: MANUFACTURER_SPECS,
    commercialNote:
      'OrigenLab puede gestionar pedidos directos con SERVA según confirmación comercial. La modalidad de prepago aplica y la disponibilidad, documentación técnica y condiciones se confirman al cotizar.',
  },
];

/**
 * Encabezado verificado para el riel de marcas. Texto idéntico al usado en la
 * firma corporativa; no reemplazar por lenguaje de representación oficial.
 */
export const brandsWallHeading = 'Marcas con las que trabajamos' as const;

/** Orden público del riel y de toda lista de marcas. Es el de la lista cerrada. */
export const brandRailOrder = APPROVED_BRAND_IDS;
