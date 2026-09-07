export interface ProductSpec {
  label: string;
  value: string;
}

export interface Product {
  id: string;
  brandId: string;
  name: string;
  slug: string;
  /** Familia editorial bajo /productos/{slug}/ */
  productFamilySlug?: string;
  sku?: string;
  summary: string;
  /** Descripción extendida para página de detalle */
  description?: string;
  /** Tipo de equipo (ej. Microcentrífuga ventilada) */
  equipmentType?: string;
  application: string;
  applications?: readonly string[];
  keySpecs?: readonly ProductSpec[];
  imagePath?: string;
  imageAlt?: string;
  imageSourceUrl?: string;
  manufacturerUrl?: string;
  datasheetUrl?: string;
  /** Slugs de categorías comerciales relacionadas */
  categorySlugs?: readonly string[];
  showOnProductsPage: boolean;
  showOnBrandPage: boolean;
  commercialNote: string;
  availabilityNote?: string;
  ctaText?: string;
  /** Atribución de especificaciones (fabricante) */
  specsAttribution?: string;
  /** Título SEO opcional (si difiere del encabezado de página) */
  metaTitle?: string;
  metaDescription?: string;
  /** Agrupación en página de marca */
  productGroup?: 'microcentrifugas' | 'universales-gran-capacidad';
  /** Etiquetas para filtros en familia de productos */
  filterTags?: readonly string[];
  /** Orden en vitrinas (menor primero) */
  catalogSortOrder?: number;
}

const ortoalresaAvailability =
  'Cotización y disponibilidad sujetas a confirmación comercial.' as const;

const ortoalresaCommercialNote =
  'La configuración, accesorios, disponibilidad, garantía y condiciones comerciales se confirman en la cotización.' as const;

/**
 * Productos destacados para vitrina editorial.
 * Mantener copy comercial conservador y confirmar condiciones al cotizar.
 */
export const products: Product[] = [
  {
    /**
     * Única referencia SERVA con ficha publicada. Las otras dos que había
     * (TEMED de 25 ml y REPEL-SILANE GE17-1332-01) se retiraron en la revisión
     * del 2026-09-07: no fue posible verificar su identidad exacta ni su
     * alcance en el catálogo del fabricante, y una referencia de reactivo mal
     * identificada es peor que ninguna. Quedan en REMOVED_SLUGS de
     * validate-catalog para que no vuelvan sin la verificación.
     */
    id: 'serva-blueslick-42500',
    brandId: 'serva',
    name: 'BlueSlick',
    slug: 'blueslick-42500',
    sku: '42500.01',
    equipmentType: 'Reactivo de tratamiento de vidrio',
    summary:
      'Reactivo listo para usar que trata la placa de vidrio para que el gel no se adhiera al desmoldar.',
    description:
      'Se aplica sobre la placa de vidrio antes de colar el gel y evita que el gel se pegue al desmoldarlo. El fabricante lo declara alternativa no tóxica a los productos con silano y no irritante para los ojos, de modo que puede manipularse fuera de campana. El envase es un pulverizador de polietileno sin propelente y cada pulsación dispensa 0,7 ml.',
    application: 'Preparación de placas de vidrio en electroforesis en gel.',
    applications: [
      'SDS-PAGE',
      'Enfoque isoeléctrico en gel',
      'Secuenciación de ADN',
      'Colado de geles propios',
    ],
    keySpecs: [
      { label: 'Presentación', value: 'Pulverizador de 250 ml, sin propelente' },
      { label: 'Dosis', value: '0,7 ml por pulsación' },
      { label: 'Duración del tratamiento', value: '3 a 4 corridas de electroforesis' },
      { label: 'Temperatura de almacenamiento', value: 'De 15 a 30 °C' },
    ],
    specsAttribution: 'Datos según la ficha pública del fabricante SERVA Electrophoresis.',
    manufacturerUrl:
      'https://www.serva.de/enDE/ProductDetails/158_42500_BlueSlick_TM_0_214.html',
    showOnProductsPage: true,
    showOnBrandPage: true,
    catalogSortOrder: 10,
    commercialNote:
      'Disponibilidad, condiciones comerciales y documentación técnica se confirman durante la cotización.',
  },
  {
    id: 'ortoalresa-biocen-22',
    brandId: 'ortoalresa',
    name: 'Biocen 22',
    slug: 'biocen-22',
    metaTitle: 'Biocen 22 | Microcentrífuga ventilada | OrigenLab',
    metaDescription:
      'Microcentrífuga ventilada Ortoalresa Biocen 22. Cotización y disponibilidad por OrigenLab; configuración según laboratorio.',
    productFamilySlug: 'centrifugas',
    equipmentType: 'Microcentrífuga ventilada',
    summary:
      'Microcentrífuga ventilada para microtubos y aplicaciones de microhematocrito según configuración.',
    description:
      'Microcentrífuga ventilada para laboratorios que requieren trabajo con microtubos y posibles aplicaciones de microhematocrito. Su formato compacto y configuración de rotores permite evaluar distintas necesidades de separación en laboratorio, sujeto a configuración y accesorios disponibles.',
    application:
      'Preparación de muestras y separación en microtubos; evaluación según rotor y accesorios.',
    applications: [
      'Microtubos',
      'Microhematocrito',
      'Investigación',
      'Laboratorio clínico',
      'Control de calidad',
      'Preparación de muestras',
    ],
    categorySlugs: ['control-de-calidad', 'laboratorio-clinico'],
    imagePath: '/products/ortoalresa/biocen-22.avif',
    imageAlt: 'Microcentrífuga Ortoalresa Biocen 22',
    imageSourceUrl: 'https://ortoalresa.com/imagen_producto/Biocen_22.avif',
    manufacturerUrl: 'https://ortoalresa.com/centrifugas/biocen-22/',
    datasheetUrl: 'https://ortoalresa.com/catalogo_producto/Catalogo_Biocen_22_ESP.pdf',
    keySpecs: [
      { label: 'Capacidad máxima', value: '24 × 2 ml' },
      { label: 'Pantalla', value: 'LED' },
      { label: 'Velocidad máxima', value: '15.000 RPM / 21.885 ×g' },
      { label: 'Versión', value: 'Ventilada' },
      { label: 'Cámara', value: 'Acero inoxidable' },
      { label: 'Motor', value: 'Inducción sin escobillas' },
      { label: 'Nivel de ruido', value: 'Inferior a 60 dB' },
      { label: 'Control', value: 'Microprocesador' },
      { label: 'Seguridad', value: 'Tapa con sistemas de seguridad' },
      { label: 'Dimensiones (CE 146 / CE 147)', value: '270 × 380 × 270 mm' },
      { label: 'Peso neto', value: '16 kg' },
      { label: 'Alimentación CE 146', value: '220-230 V, 50-60 Hz, 220 W' },
      { label: 'Alimentación CE 147', value: '110-120 V, 50-60 Hz, 220 W' },
    ],
    specsAttribution: 'Especificaciones según documentación del fabricante Ortoalresa.',
    productGroup: 'microcentrifugas',
    filterTags: ['ventilada', 'microcentrífuga'],
    catalogSortOrder: 1,
    showOnProductsPage: true,
    showOnBrandPage: true,
    commercialNote: ortoalresaCommercialNote,
    availabilityNote: ortoalresaAvailability,
    ctaText: 'Solicitar cotización',
  },
  {
    id: 'ortoalresa-biocen-22-r',
    brandId: 'ortoalresa',
    name: 'Biocen 22 R',
    slug: 'biocen-22-r',
    metaTitle: 'Biocen 22 R | Microcentrífuga refrigerada | OrigenLab',
    metaDescription:
      'Microcentrífuga refrigerada Ortoalresa Biocen 22 R. Cotización y configuración por OrigenLab; condiciones sujetas a confirmación comercial.',
    productFamilySlug: 'centrifugas',
    equipmentType: 'Microcentrífuga refrigerada',
    summary:
      'Microcentrífuga refrigerada para alta velocidad, control de temperatura y distintos formatos de tubos según rotor.',
    description:
      'Microcentrífuga refrigerada para laboratorios que requieren alta velocidad, control de temperatura y compatibilidad con distintos formatos de tubos, desde microtubos hasta tubos cónicos de 15 ml según rotor y adaptadores.',
    application:
      'Separación con control de temperatura en microtubos y tubos cónicos; configuración según rotor.',
    applications: [
      'Microtubos',
      'Tubos cónicos',
      'Refrigeración',
      'Investigación',
      'Biotecnología',
      'Laboratorio clínico',
    ],
    categorySlugs: ['control-de-calidad', 'laboratorio-clinico'],
    imagePath: '/products/ortoalresa/biocen-22-r.avif',
    imageAlt: 'Microcentrífuga refrigerada Ortoalresa Biocen 22 R',
    imageSourceUrl: 'https://ortoalresa.com/imagen_producto/Biocen_22_R.avif',
    manufacturerUrl: 'https://ortoalresa.com/centrifugas/biocen-22-r/',
    datasheetUrl: 'https://ortoalresa.com/catalogo_producto/Catalogo_Biocen_22_R_ESP.pdf',
    keySpecs: [
      { label: 'Capacidad máxima', value: '8 × 15 ml' },
      { label: 'Pantalla', value: 'LCD' },
      { label: 'Velocidad máxima', value: '18.100 RPM / 31.865 ×g' },
      { label: 'Versión', value: 'Refrigerada' },
      { label: 'Temperatura', value: 'Regulable de -20 °C a 40 °C' },
      { label: 'Temperatura a máximas RPM', value: '4 °C según ficha del fabricante' },
      { label: 'Pre-enfriamiento', value: 'Programa con rotor girando' },
      { label: 'Sensor', value: 'Temperatura en cámara' },
      { label: 'Refrigerante', value: 'Gas R 449A HFO libre de CFC' },
      { label: 'Motor', value: 'Inducción sin escobillas' },
      { label: 'Nivel de ruido', value: 'Inferior a 60 dB' },
      { label: 'Rotor', value: 'Reconocimiento automático' },
      { label: 'Cámara', value: 'Acero inoxidable' },
      { label: 'Dimensiones (CE 148 / CE 149)', value: '270 × 650 × 280 mm' },
      { label: 'Peso neto', value: '41 kg' },
      { label: 'Alimentación CE 148', value: '220-230 V, 50 Hz, 580 W' },
      { label: 'Alimentación CE 149', value: '110-120 V, 60 Hz, 580 W' },
    ],
    specsAttribution: 'Especificaciones según documentación del fabricante Ortoalresa.',
    productGroup: 'microcentrifugas',
    filterTags: ['refrigerada', 'microcentrífuga'],
    catalogSortOrder: 2,
    showOnProductsPage: true,
    showOnBrandPage: true,
    commercialNote: ortoalresaCommercialNote,
    availabilityNote: ortoalresaAvailability,
    ctaText: 'Solicitar cotización',
  },
  {
    id: 'ortoalresa-consul-22',
    brandId: 'ortoalresa',
    name: 'Consul 22',
    slug: 'consul-22',
    metaTitle: 'Consul 22 | Centrífuga de gran capacidad | OrigenLab',
    metaDescription:
      'Centrífuga ventilada de gran capacidad Ortoalresa Consul 22. Cotización por OrigenLab; configuración y accesorios según laboratorio.',
    productFamilySlug: 'centrifugas',
    equipmentType: 'Centrífuga de gran capacidad',
    summary:
      'Centrífuga ventilada de gran capacidad para volúmenes mayores con rotores y adaptadores según configuración.',
    description:
      'Centrífuga ventilada de gran capacidad para laboratorios que requieren procesar volúmenes mayores con opciones de rotores oscilantes, microplacas, rotores angulares y adaptadores según configuración. Su pantalla TFT y funciones de control permiten trabajar con trazabilidad de parámetros, sujeto a la configuración seleccionada.',
    application:
      'Separación en tubos de mayor volumen y microplacas; configuración según rotor y adaptadores.',
    applications: [
      'Gran capacidad',
      'Tubos hasta 400 ml',
      'Microplacas',
      'Preparación de muestras',
      'Control de calidad',
      'Investigación',
    ],
    categorySlugs: ['control-de-calidad', 'laboratorio-clinico'],
    imagePath: '/products/ortoalresa/consul-22.avif',
    imageAlt: 'Centrífuga Ortoalresa Consul 22',
    imageSourceUrl: 'https://ortoalresa.com/imagen_producto/Consul_22.avif',
    manufacturerUrl: 'https://ortoalresa.com/centrifugas/consul-22/',
    datasheetUrl: 'https://ortoalresa.com/catalogo_producto/Catalogo_serie_Consul_22_ESP.pdf',
    keySpecs: [
      { label: 'Capacidad máxima', value: '4 × 400 ml' },
      { label: 'Pantalla', value: 'TFT táctil a color' },
      { label: 'Velocidad máxima', value: '14.300 RPM / 21.948 ×g' },
      { label: 'Versión', value: 'Ventilada' },
      { label: 'Cámara', value: 'Acero inoxidable' },
      { label: 'Motor', value: 'Inducción sin escobillas' },
      { label: 'Rotor', value: 'Reconocimiento automático' },
      { label: 'Nivel de ruido', value: 'Inferior a 60 dB' },
      { label: 'Control', value: 'ULS (localización de desequilibrio)' },
      { label: 'Aceleración / frenado', value: 'Sistema PCBS progresivo' },
      { label: 'Dimensiones (CE 226 / CE 227)', value: '490 × 600 × 400 mm' },
      { label: 'Peso neto', value: '59 kg' },
      { label: 'Alimentación CE 226', value: '220-230 V, 50-60 Hz, 600 W' },
      { label: 'Alimentación CE 227', value: '110-120 V, 50-60 Hz, 600 W' },
    ],
    specsAttribution: 'Especificaciones según documentación del fabricante Ortoalresa.',
    productGroup: 'universales-gran-capacidad',
    filterTags: ['ventilada', 'gran capacidad', 'universal'],
    catalogSortOrder: 5,
    showOnProductsPage: true,
    showOnBrandPage: true,
    commercialNote: ortoalresaCommercialNote,
    availabilityNote: ortoalresaAvailability,
    ctaText: 'Solicitar cotización',
  },
  {
    id: 'ortoalresa-digicen-22',
    brandId: 'ortoalresa',
    name: 'Digicen 22',
    slug: 'digicen-22',
    metaTitle: 'Digicen 22 | Centrífuga universal | OrigenLab',
    metaDescription:
      'Centrífuga universal ventilada Ortoalresa Digicen 22. Cotización por OrigenLab; rotores y accesorios según necesidad.',
    productFamilySlug: 'centrifugas',
    equipmentType: 'Centrífuga universal',
    summary:
      'Centrífuga universal ventilada para microplacas, criotubos, microtubos y tubos hasta 100 ml según configuración.',
    description:
      'Centrífuga universal ventilada para laboratorios que necesitan versatilidad en preparación de muestras, con opciones de rotores para microplacas, criotubos, microtubos y tubos de mayor volumen según configuración.',
    application:
      'Preparación de muestras con múltiples formatos de tubos y placas; configuración según rotor.',
    applications: [
      'Universal',
      'Microplacas',
      'Criotubos',
      'Microtubos',
      'Tubos hasta 100 ml',
      'Control de calidad',
      'Investigación',
    ],
    categorySlugs: ['control-de-calidad', 'laboratorio-clinico'],
    imagePath: '/products/ortoalresa/digicen-22.avif',
    imageAlt: 'Centrífuga Ortoalresa Digicen 22',
    imageSourceUrl: 'https://ortoalresa.com/imagen_producto/Digicen_22.avif',
    manufacturerUrl: 'https://ortoalresa.com/centrifugas/digicen-22/',
    /** Serie Digicen 22: mismo PDF del fabricante para ventilada y refrigerada. */
    datasheetUrl: 'https://ortoalresa.com/catalogo_producto/Catalogo_serie_Digicen_22_ESP.pdf',
    keySpecs: [
      { label: 'Capacidad máxima', value: '4 × 100 ml' },
      { label: 'Pantalla', value: 'TFT táctil a color' },
      { label: 'Velocidad máxima', value: '16.500 RPM / 26.480 ×g' },
      { label: 'Versión', value: 'Ventilada' },
      { label: 'Rotores', value: 'Microplacas, criotubos, microtubos y tubos hasta 100 ml (según configuración)' },
      { label: 'Accesorios', value: 'Sistema REI sin herramientas' },
      { label: 'Conectividad', value: 'SmartConnect según ficha del fabricante' },
      { label: 'Motor', value: 'Inducción sin escobillas' },
      { label: 'Nivel de ruido', value: 'Inferior a 60 dB' },
      { label: 'Rotor', value: 'Reconocimiento automático' },
      { label: 'Cámara', value: 'Acero inoxidable' },
    ],
    specsAttribution: 'Especificaciones según documentación del fabricante Ortoalresa.',
    productGroup: 'universales-gran-capacidad',
    filterTags: ['ventilada', 'universal'],
    catalogSortOrder: 3,
    showOnProductsPage: true,
    showOnBrandPage: true,
    commercialNote: ortoalresaCommercialNote,
    availabilityNote: ortoalresaAvailability,
    ctaText: 'Solicitar cotización',
  },
  {
    id: 'ortoalresa-digicen-22-r',
    brandId: 'ortoalresa',
    name: 'Digicen 22 R',
    slug: 'digicen-22-r',
    metaTitle: 'Digicen 22 R | Centrífuga universal refrigerada | OrigenLab',
    metaDescription:
      'Centrífuga universal refrigerada Ortoalresa Digicen 22 R. Cotización por OrigenLab; configuración según laboratorio.',
    productFamilySlug: 'centrifugas',
    equipmentType: 'Centrífuga universal refrigerada',
    summary:
      'Centrífuga universal refrigerada con versatilidad de rotores y control de temperatura en preparación de muestras.',
    description:
      'Centrífuga universal refrigerada para laboratorios que requieren versatilidad de rotores y control de temperatura en preparación de muestras, microtubos, criotubos, tubos cónicos y configuraciones oscilantes según accesorios.',
    application:
      'Separación con control de temperatura y múltiples formatos de muestra; configuración según rotor.',
    applications: [
      'Universal refrigerada',
      'Microplacas',
      'Criotubos',
      'Microtubos',
      'Tubos cónicos',
      'Refrigeración',
      'Investigación',
      'Control de calidad',
    ],
    categorySlugs: ['control-de-calidad', 'laboratorio-clinico'],
    imagePath: '/products/ortoalresa/digicen-22-r.avif',
    imageAlt: 'Centrífuga refrigerada Ortoalresa Digicen 22 R',
    imageSourceUrl: 'https://ortoalresa.com/imagen_producto/Digicen_22_R.avif',
    manufacturerUrl: 'https://ortoalresa.com/centrifugas/digicen-22-r/',
    /** Mismo catálogo PDF de serie Digicen 22 (fabricante). */
    datasheetUrl: 'https://ortoalresa.com/catalogo_producto/Catalogo_serie_Digicen_22_ESP.pdf',
    keySpecs: [
      { label: 'Capacidad máxima', value: '4 × 100 ml' },
      { label: 'Pantalla', value: 'TFT táctil a color' },
      { label: 'Velocidad máxima', value: '16.500 RPM / 26.480 ×g' },
      { label: 'Versión', value: 'Refrigerada' },
      { label: 'Temperatura', value: 'Regulable de -20 °C a 40 °C' },
      { label: 'Temperatura a máximas RPM', value: '4 °C según ficha del fabricante' },
      { label: 'Pre-enfriamiento', value: 'Programa con rotor girando' },
      { label: 'Sensor', value: 'Temperatura en cámara' },
      { label: 'Refrigerante', value: 'Gas R 449A HFO libre de CFC' },
      { label: 'Accesorios', value: 'Sistema REI' },
      { label: 'Conectividad', value: 'SmartConnect según ficha del fabricante' },
      { label: 'Motor', value: 'Inducción sin escobillas' },
      { label: 'Nivel de ruido', value: 'Inferior a 60 dB' },
      { label: 'Rotor', value: 'Reconocimiento automático' },
      { label: 'Cámara', value: 'Acero inoxidable' },
      { label: 'Dimensiones (CE 259 / CE 260)', value: '590 × 620 × 320 mm' },
      { label: 'Peso neto', value: '65 kg' },
      { label: 'Alimentación CE 259', value: '220-230 V, 50 Hz, 700 W' },
      { label: 'Alimentación CE 260', value: '110-120 V, 60 Hz, 700 W' },
    ],
    specsAttribution: 'Especificaciones según documentación del fabricante Ortoalresa.',
    productGroup: 'universales-gran-capacidad',
    filterTags: ['refrigerada', 'universal'],
    catalogSortOrder: 4,
    showOnProductsPage: true,
    showOnBrandPage: true,
    commercialNote: ortoalresaCommercialNote,
    availabilityNote: ortoalresaAvailability,
    ctaText: 'Solicitar cotización',
  },
];
