/**
 * Modelos y familias de equipo documentados desde la fuente oficial.
 *
 * Complementa `products.ts` y no lo sustituye. La diferencia es de qué puede
 * responder el sitio, no de importancia:
 *
 * - `products.ts` — modelos con **ficha propia** en origenlab.cl: fotografía
 *   con procedencia, especificaciones agrupadas, PDF del fabricante y URL
 *   interna. Hoy son las cinco centrífugas Ortoalresa y BlueSlick.
 * - este archivo — modelos y familias que el sitio **describe y enlaza** pero
 *   no aloja: qué hace el equipo, para qué se usa en el laboratorio, qué
 *   criterios deciden la elección, y de ahí al fabricante. Sin fotografía,
 *   porque no hay ninguna con permiso, y sin página interna, porque una ficha
 *   sin imagen ni documentación propia sería una página vacía con un título.
 *
 * # De dónde sale cada dato
 *
 * Cada entrada declara `officialUrl` y `verifiedOn`, y no se escribe una cifra
 * que no esté en ese destino. `npm run verify:sources` comprueba con peticiones
 * reales que los destinos siguen respondiendo, y `validate:brands` comprueba
 * que ninguna entrada pertenezca a una marca fuera de la lista cerrada.
 *
 * Las cifras se redactaron leyendo la página o el PDF del fabricante el
 * 2026-09-07. Están en el idioma del laboratorio y con la puntuación decimal
 * chilena, pero no se convierten unidades ni se redondean rangos: si el
 * fabricante dice «approx. 1.0 to 8.0 L/min», aquí dice «aprox. 1,0 a 8,0
 * l/min», no «hasta 8 l/min».
 *
 * # Lo que este archivo no puede tener
 *
 * Precio, plazo, stock, garantía, condición comercial ni fotografía. Nada de
 * eso está verificado para estas cinco marcas y `validate:catalog` lo bloquea.
 *
 * # El caso de IKA
 *
 * `ika.com` responde 403 a todo cliente automatizado, incluido un navegador
 * headless real: es protección antibot de Cloudflare, no un enlace roto. El PDF
 * oficial de dispersores sí responde 200 con `application/pdf`, y de ahí salen
 * las cifras de los tres modelos. Las entradas de IKA declaran
 * `officialUrlScope: 'familia'` y una nota: el enlace lleva a la página de
 * dispersores del fabricante, no a la del modelo, porque no hay forma de
 * comprobar por máquina la URL de cada modelo. Queda anotado en
 * `docs/design/CONTENT_NEEDED.md` para abrirlo a mano en un navegador.
 */
import type { ApprovedBrandId } from './brands';

export interface ModelCriterion {
  label: string;
  value: string;
}

export interface BrandModel {
  id: string;
  brandId: ApprovedBrandId;
  /** Familia de `equipmentScope.ts`. */
  familyId: string;
  /** Nombre exacto del fabricante. No traducir ni abreviar. */
  name: string;
  /** Código de catálogo del fabricante, cuando lo publica. */
  code?: string;
  /** Un modelo concreto o una familia de modelos con la misma ficha. */
  scope: 'modelo' | 'familia';
  /** Qué hace el equipo. Una frase, sin adjetivos comerciales. */
  does: string;
  /** Usos típicos en el laboratorio. Lo que el cliente reconoce. */
  uses: readonly string[];
  /** Lo que decide la elección: rango, legibilidad, volumen, potencia. */
  criteria: readonly ModelCriterion[];
  /** Página del fabricante. */
  officialUrl: string;
  /** El destino describe este modelo o la familia que lo contiene. */
  officialUrlScope: 'modelo' | 'familia';
  /** PDF alojado por el fabricante, o `null` si no publica ninguno. */
  officialPdfUrl: string | null;
  /** El destino oficial sólo responde por http (sólo Löser). */
  officialUrlInsecure?: true;
  /** Por qué el enlace es de familia, o por qué no hay PDF. */
  sourceNote?: string;
  /** Fecha en que se leyó la fuente y se comprobó que responde (ISO). */
  verifiedOn: string;
}

const READ_ON = '2026-09-07';

/**
 * Documentación de familia: la página y el PDF que cubren la familia entera.
 * Se enlaza donde no hay un modelo concreto que enlazar, y en el pie de cada
 * sección de familia como fuente de lo que se acaba de leer.
 */
export interface FamilyDocumentation {
  familyId: string;
  officialUrl: string;
  officialPdfUrl: string | null;
  officialUrlInsecure?: true;
  note?: string;
  verifiedOn: string;
}

export const familyDocumentation: readonly FamilyDocumentation[] = [
  {
    familyId: 'sonicacion',
    officialUrl: 'https://www.hielscher.com/products.htm',
    officialPdfUrl:
      'https://www.hielscher.com/wp-content/uploads/Catalogue-Hielscher-Ultrasonics-eng-v.02.2025.pdf',
    verifiedOn: READ_ON,
  },
  {
    familyId: 'dispersion-homogeneizacion',
    officialUrl: 'https://www.ika.com/en/Products-LabEq/Dispersers-pg177/',
    officialPdfUrl:
      'https://www.ika.com/ika/pdf/flyer-catalog/Disperser_Brochure_IWS_EN_wop_screen.pdf',
    note: 'La página HTML del fabricante devuelve 403 a peticiones automatizadas. El PDF oficial responde y es la fuente de las cifras publicadas.',
    verifiedOn: READ_ON,
  },
  {
    familyId: 'centrifugacion',
    officialUrl: 'https://ortoalresa.com/en/products/',
    officialPdfUrl: 'https://ortoalresa.com/catalogo_producto/Catalogo_serie_Digicen_22_ESP.pdf',
    note: 'El PDF por modelo vive en products.ts; aquí figura el de la serie Digicen 22.',
    verifiedOn: READ_ON,
  },
  {
    familyId: 'pesaje-humedad',
    officialUrl: 'https://adamequipment.com/products.html',
    officialPdfUrl: 'https://adamequipment.com/media/docs/data_sheets/PMB-DS-A4-EN.pdf',
    verifiedOn: READ_ON,
  },
  {
    familyId: 'osmometria',
    officialUrl: 'http://www.loeser-osmometer.de/produkte-eng.html',
    officialPdfUrl: null,
    officialUrlInsecure: true,
    note: 'El fabricante no publica PDF descargable: los folletos se piden por formulario. Su servidor rechaza el saludo TLS, de modo que el enlace oficial es http.',
    verifiedOn: READ_ON,
  },
  {
    familyId: 'electroforesis',
    officialUrl:
      'https://www.serva.de/enDE/Catalog/449_Laboratory_Equipment_Electrophoresis_Devices_212_0.html',
    officialPdfUrl: 'https://www.serva.de/www_root/documents/Electrophoresis%20by%20SERVA_web.pdf',
    verifiedOn: READ_ON,
  },
];

export const brandModels: readonly BrandModel[] = [
  /* -- Hielscher Ultrasonics · sonicación -------------------------------- */
  {
    id: 'hielscher-up100h',
    brandId: 'hielscher',
    familyId: 'sonicacion',
    name: 'UP100H',
    scope: 'modelo',
    does: 'Sonicador de sobremesa con sonda intercambiable para muestras pequeñas y medianas, con el generador y el transductor en una sola pieza de mano.',
    uses: [
      'Lisis de células y de tejido blando antes de extraer',
      'Dispersión de nanopartículas y desaglomeración',
      'Desgasificación de disolventes y de muestras para HPLC',
      'Emulsión de fases inmiscibles a escala de tubo',
    ],
    criteria: [
      { label: 'Potencia', value: '100 W' },
      { label: 'Frecuencia', value: '30 kHz' },
      { label: 'Amplitud', value: 'Regulable del 20 al 100 %' },
      { label: 'Volumen de muestra', value: '0,1 a 500 ml según sonotrodo' },
      { label: 'Peso', value: '1,1 kg' },
      { label: 'Régimen de trabajo', value: 'Operación continua' },
    ],
    officialUrl: 'https://www.hielscher.com/100h_p.htm',
    officialUrlScope: 'modelo',
    officialPdfUrl:
      'https://www.hielscher.com/wp-content/uploads/FactSheet-sonicator-UP100H-eng-14022024.pdf',
    verifiedOn: READ_ON,
  },
  {
    id: 'hielscher-up200st',
    brandId: 'hielscher',
    familyId: 'sonicacion',
    name: 'UP200St',
    scope: 'modelo',
    does: 'Sonicador de laboratorio con transductor separado del generador, pantalla táctil y registro de parámetros en tarjeta SD.',
    uses: [
      'Preparación de muestra reproducible con parámetros documentados',
      'Homogeneización y emulsión de lotes de decenas a cientos de mililitros',
      'Molienda húmeda y desaglomeración',
      'Extracción y sonoquímica en banco',
    ],
    criteria: [
      { label: 'Potencia', value: '200 W' },
      { label: 'Frecuencia', value: '26 kHz' },
      { label: 'Amplitud', value: 'Regulable del 20 al 100 %' },
      { label: 'Volumen de muestra', value: '2 a 1.000 ml' },
      { label: 'Protección', value: 'Transductor IP65, generador IP30' },
      { label: 'Registro', value: 'Tarjeta SD integrada y control remoto por LAN' },
    ],
    officialUrl: 'https://www.hielscher.com/up200st-powerful-ultrasonic-lab-homogenizer.htm',
    officialUrlScope: 'modelo',
    officialPdfUrl:
      'https://www.hielscher.com/wp-content/uploads/FactSheet-UP200St-HielscherUltrasonics_eng-13022024.pdf',
    verifiedOn: READ_ON,
  },
  {
    id: 'hielscher-up400st',
    brandId: 'hielscher',
    familyId: 'sonicacion',
    name: 'UP400St',
    scope: 'modelo',
    does: 'Sonicador de laboratorio de 400 W que acepta sonotrodos de 3 a 40 mm, con lo que un mismo equipo cubre desde 5 ml hasta unos 4 litros por lote.',
    uses: [
      'Extracción de compuestos de matrices vegetales y de alimentos',
      'Homogeneización de lotes de hasta varios litros',
      'Desarrollo de método antes de escalar a proceso',
      'Trabajo en celda de flujo a 20 a 200 ml por minuto',
    ],
    criteria: [
      { label: 'Potencia', value: '400 W' },
      { label: 'Frecuencia', value: '24 kHz' },
      { label: 'Amplitud', value: 'Regulable del 20 al 100 %' },
      { label: 'Volumen de muestra', value: '5 ml a aprox. 4 l según sonotrodo' },
      { label: 'Sonotrodos', value: 'Diámetros de 3 a 40 mm' },
      { label: 'Registro', value: 'Pantalla en color, registro de datos y sensor de temperatura' },
    ],
    officialUrl: 'https://www.hielscher.com/up400st-powerful-ultrasonicator.htm',
    officialUrlScope: 'modelo',
    officialPdfUrl:
      'https://www.hielscher.com/wp-content/uploads/UP400St-FactSheet-ultrasonicator-eng-HielscherUltrasonics-v04082022.pdf',
    verifiedOn: READ_ON,
  },
  {
    id: 'hielscher-uip2000hdt',
    brandId: 'hielscher',
    familyId: 'sonicacion',
    name: 'UIP2000hdT',
    scope: 'modelo',
    does: 'Procesador ultrasónico industrial de 2 kW para trabajo continuo en línea, con registro automático de amplitud, potencia, tiempo, temperatura y presión.',
    uses: [
      'Paso de laboratorio a planta piloto con la misma técnica',
      'Homogeneización y emulsión en flujo continuo',
      'Extracción y lisis a escala de producción',
      'Reacciones sonoquímicas con control de proceso',
    ],
    criteria: [
      { label: 'Potencia', value: '2.000 W' },
      { label: 'Frecuencia', value: '20 kHz' },
      { label: 'Amplitud', value: 'Regulable del 20 al 100 % en el generador' },
      { label: 'Caudal', value: 'Aprox. 1,0 a 8,0 l/min según producto y objetivo' },
      { label: 'Capacidad diaria', value: 'Aprox. 2 a 10 m³/día según energía aplicada' },
      { label: 'Registro', value: 'CSV automático y control remoto por LAN' },
    ],
    officialUrl:
      'https://www.hielscher.com/uip2000hdt-2000-watts-powerful-industrial-ultrasonicator-for-full-process-control.htm',
    officialUrlScope: 'modelo',
    officialPdfUrl:
      'https://www.hielscher.com/wp-content/uploads/FactSheet-UIP2000hdT-2kW-sonicator-engl-082022.pdf',
    verifiedOn: READ_ON,
  },

  /* -- IKA · dispersión y homogeneización -------------------------------- */
  {
    id: 'ika-t10-basic',
    brandId: 'ika',
    familyId: 'dispersion-homogeneizacion',
    name: 'T 10 basic ULTRA-TURRAX',
    code: '0003737000',
    scope: 'modelo',
    does: 'Dispersor de rotor y estátor de mano para volúmenes pequeños, el único de la línea que baja al medio mililitro.',
    uses: [
      'Homogeneización de muestra en tubo de microcentrífuga y en tubo cónico',
      'Preparación de muestra para PCR y para extracción de ácidos nucleicos',
      'Disgregación de biopsias y de tejido en volumen mínimo',
    ],
    criteria: [
      { label: 'Volumen de trabajo (H₂O)', value: '0,5 a 100 ml' },
      { label: 'Revoluciones', value: '8.000 a 30.000 rpm, regulación continua' },
      { label: 'Potencia', value: '125 W de entrada, 75 W de salida' },
      { label: 'Viscosidad máxima', value: '5.000 mPas' },
      { label: 'Herramienta dispersora', value: 'Serie S 10, generadores de 5, 8 y 10 mm' },
      { label: 'Peso', value: '0,5 kg' },
    ],
    officialUrl: 'https://www.ika.com/en/Products-LabEq/Dispersers-pg177/',
    officialUrlScope: 'familia',
    officialPdfUrl:
      'https://www.ika.com/ika/pdf/flyer-catalog/Disperser_Brochure_IWS_EN_wop_screen.pdf',
    sourceNote:
      'Cifras tomadas del folleto oficial de dispersores del fabricante. El enlace lleva a la página de dispersores y no a la del modelo: ika.com devuelve 403 a peticiones automatizadas y no fue posible comprobar por máquina la URL de cada modelo.',
    verifiedOn: READ_ON,
  },
  {
    id: 'ika-t18-digital',
    brandId: 'ika',
    familyId: 'dispersion-homogeneizacion',
    name: 'T 18 digital ULTRA-TURRAX',
    code: '0003720000',
    scope: 'modelo',
    does: 'Dispersor de sobremesa con soporte y lectura digital de revoluciones para lotes de hasta un litro y medio.',
    uses: [
      'Homogeneización de alimentos y de matrices sólidas en suspensión',
      'Emulsión de cremas, ungüentos y formulaciones de laboratorio',
      'Preparación de muestra en serie con parámetro repetible',
    ],
    criteria: [
      { label: 'Volumen de trabajo (H₂O)', value: '1 a 1.500 ml' },
      { label: 'Revoluciones', value: '3.000 a 25.000 rpm, regulación continua' },
      { label: 'Potencia', value: '500 W de entrada, 300 W de salida' },
      { label: 'Viscosidad máxima', value: '5.000 mPas' },
      { label: 'Herramienta dispersora', value: 'Serie S 18, generadores de 10 y 19 mm' },
      { label: 'Indicación', value: 'LED de revoluciones' },
    ],
    officialUrl: 'https://www.ika.com/en/Products-LabEq/Dispersers-pg177/',
    officialUrlScope: 'familia',
    officialPdfUrl:
      'https://www.ika.com/ika/pdf/flyer-catalog/Disperser_Brochure_IWS_EN_wop_screen.pdf',
    sourceNote:
      'Cifras tomadas del folleto oficial de dispersores del fabricante. El enlace lleva a la página de dispersores y no a la del modelo, por la misma razón que en el T 10 basic.',
    verifiedOn: READ_ON,
  },
  {
    id: 'ika-t25-digital',
    brandId: 'ika',
    familyId: 'dispersion-homogeneizacion',
    name: 'T 25 digital ULTRA-TURRAX',
    code: '0003725000',
    scope: 'modelo',
    does: 'Dispersor de sobremesa de 800 W para lotes de hasta dos litros, con la mayor variedad de herramientas de la línea, incluidas las de trabajo en vacío.',
    uses: [
      'Homogeneización de lotes de laboratorio y de planta piloto pequeña',
      'Dispersión de sólidos difíciles de mojar',
      'Emulsión en vacío para evitar arrastre de aire',
      'Molienda húmeda hasta finura de suspensión de 10 a 50 µm',
    ],
    criteria: [
      { label: 'Volumen de trabajo (H₂O)', value: '1 a 2.000 ml' },
      { label: 'Revoluciones', value: '3.000 a 25.000 rpm, regulación continua' },
      { label: 'Potencia', value: '800 W de entrada, 500 W de salida' },
      { label: 'Viscosidad máxima', value: '5.000 mPas' },
      {
        label: 'Herramienta dispersora',
        value: 'Serie S 25, generadores de 8 a 25 mm, con variantes para vacío',
      },
      { label: 'Indicación', value: 'LED de revoluciones' },
    ],
    officialUrl: 'https://www.ika.com/en/Products-LabEq/Dispersers-pg177/',
    officialUrlScope: 'familia',
    officialPdfUrl:
      'https://www.ika.com/ika/pdf/flyer-catalog/Disperser_Brochure_IWS_EN_wop_screen.pdf',
    sourceNote:
      'Cifras tomadas del folleto oficial de dispersores del fabricante. El enlace lleva a la página de dispersores y no a la del modelo, por la misma razón que en el T 10 basic.',
    verifiedOn: READ_ON,
  },

  /* -- Adam Equipment · pesaje y análisis de humedad --------------------- */
  {
    id: 'adam-pmb',
    brandId: 'adam-equipment',
    familyId: 'pesaje-humedad',
    name: 'PMB',
    scope: 'familia',
    does: 'Analizador de humedad por termogravimetría: pesa la muestra, la calienta con una lámpara halógena y calcula el contenido de humedad por la pérdida de masa.',
    uses: [
      'Humedad en alimentos, granos y harinas',
      'Control de secado en línea de producción',
      'Humedad en aceites, cremas y cosméticos',
      'Sustituir el ensayo de estufa cuando el tiempo de respuesta importa',
    ],
    criteria: [
      { label: 'Capacidad y legibilidad', value: '50 y 160 g con 0,001 g / 0,01 %; 200 g con 0,01 g / 0,05 %' },
      { label: 'Calentamiento', value: 'Lámpara halógena de 400 W' },
      { label: 'Temperatura', value: '50 a 160 °C en pasos de 1 °C' },
      { label: 'Modos de secado', value: 'Rampa, escalonado y temperatura única' },
      { label: 'Memoria', value: '49 recetas y hasta 99 resultados' },
      { label: 'Interfaces', value: 'RS-232 y dos puertos USB' },
    ],
    officialUrl: 'https://adamequipment.com/pmb-moisture-analyzers-us.html',
    officialUrlScope: 'familia',
    officialPdfUrl: 'https://adamequipment.com/media/docs/data_sheets/PMB-DS-A4-EN.pdf',
    verifiedOn: READ_ON,
  },
  {
    id: 'adam-solis',
    brandId: 'adam-equipment',
    familyId: 'pesaje-humedad',
    name: 'Solis',
    scope: 'familia',
    does: 'Balanzas analíticas y semimicro con cámara de pesaje cerrada, calibración interna y externa y registro con fecha y hora para trazabilidad.',
    uses: [
      'Pesaje analítico y preparación de patrones',
      'Formulación y control de calidad',
      'Determinación de densidad y pesaje dinámico',
      'Docencia en química analítica',
    ],
    criteria: [
      { label: 'Capacidad', value: '120 a 510 g según modelo' },
      { label: 'Legibilidad', value: 'Desde 0,01 mg / 0,1 mg hasta 0,0001 g' },
      { label: 'Calibración', value: 'Interna y externa' },
      { label: 'Trazabilidad', value: 'Registro con fecha y hora' },
      { label: 'Interfaz', value: 'RS-232 para computador o impresora' },
      { label: 'Funciones', value: 'Conteo de piezas, pesaje por porcentaje y control de peso' },
    ],
    officialUrl: 'https://adamequipment.com/solis-analytical-and-semi-micro-balances-us.html',
    officialUrlScope: 'familia',
    officialPdfUrl: 'https://adamequipment.com/media/docs/data_sheets/SAB-Solis-DS-LT-EN-Print.pdf',
    verifiedOn: READ_ON,
  },
  {
    id: 'adam-highland',
    brandId: 'adam-equipment',
    familyId: 'pesaje-humedad',
    name: 'Highland',
    scope: 'familia',
    does: 'Balanzas de precisión portátiles con calibración interna y protección de sobrecarga, pensadas para pesar fuera del banco de analítica.',
    uses: [
      'Pesaje de precisión en planta y en terreno',
      'Recepción de materia prima y dosificación',
      'Conteo de piezas y acumulación',
      'Segundo puesto de pesaje junto a una analítica',
    ],
    criteria: [
      { label: 'Capacidad', value: '120 a 6.000 g según modelo' },
      { label: 'Legibilidad', value: '0,001 a 0,1 g según modelo' },
      { label: 'Calibración', value: 'Interna, sin juego de masas externo' },
      { label: 'Protección', value: 'Sistema de protección contra sobrecarga' },
      { label: 'Interfaces', value: 'USB y RS-232' },
      { label: 'Pantalla', value: 'LCD retroiluminado' },
    ],
    officialUrl: 'https://adamequipment.com/highland-portable-precision-balances-us.html',
    officialUrlScope: 'familia',
    officialPdfUrl: 'https://adamequipment.com/media/docs/data_sheets/HCB-DS-A4-EN.pdf',
    verifiedOn: READ_ON,
  },

  /* -- Löser Messtechnik · osmometría ------------------------------------ */
  {
    id: 'loeser-osmometer-basic',
    brandId: 'loeser',
    familyId: 'osmometria',
    name: 'Osmometer basic',
    scope: 'modelo',
    does: 'Osmómetro crioscópico de operación manual: el usuario inicia la congelación con la aguja de cristales y el equipo reconoce y guarda el resultado.',
    uses: [
      'Osmolalidad en suero, plasma y orina',
      'Control de soluciones de infusión y de diálisis',
      'Puesto de medición con poca carga diaria',
    ],
    criteria: [
      { label: 'Volumen de muestra', value: '100 o 50 µl' },
      { label: 'Tiempo de medición', value: 'Aprox. 1,5 min con 100 µl' },
      { label: 'Rango', value: '0 a 2.500 mosm/kg H₂O, resolución 1 mosm/kg' },
      {
        label: 'Reproducibilidad',
        value: '±0,5 % o ±1,5 mosm con 100 µl; ±1 % o ±3 mosm con 50 µl',
      },
      { label: 'Memoria', value: '100 mediciones con número de muestra y usuario' },
      { label: 'Servicios', value: 'No necesita suministro de agua; 100 a 230 V, aprox. 65 VA' },
    ],
    officialUrl: 'http://www.loeser-osmometer.de/typ7-eng.html',
    officialUrlScope: 'modelo',
    officialPdfUrl: null,
    officialUrlInsecure: true,
    sourceNote:
      'El fabricante no publica PDF descargable de ninguno de los cuatro instrumentos: los folletos se piden por formulario. Su servidor rechaza el saludo TLS y sólo responde por http.',
    verifiedOn: READ_ON,
  },
  {
    id: 'loeser-i-osmometer-basic',
    brandId: 'loeser',
    familyId: 'osmometria',
    name: 'i Osmometer basic',
    scope: 'modelo',
    does: 'La misma medición del Osmometer basic con el ciclo automatizado y salida de datos a computador o impresora.',
    uses: [
      'Osmolalidad de rutina con volcado de datos',
      'Laboratorio clínico con exigencia de registro',
      'Series de medición sin intervención en cada tubo',
    ],
    criteria: [
      { label: 'Volumen de muestra', value: '100 o 50 µl' },
      { label: 'Tiempo de medición', value: 'Aprox. 1,5 min con 100 µl' },
      { label: 'Proceso', value: 'Automático' },
      { label: 'Rango', value: '0 a 2.500 mosm/kg H₂O, resolución 1 mosm/kg' },
      { label: 'Interfaces', value: '1 USB y 1 RS-232, con software para Windows incluido' },
      { label: 'Memoria', value: '100 mediciones con número de muestra y usuario' },
    ],
    officialUrl: 'http://www.loeser-osmometer.de/typ7i-eng.html',
    officialUrlScope: 'modelo',
    officialPdfUrl: null,
    officialUrlInsecure: true,
    verifiedOn: READ_ON,
  },
  {
    id: 'loeser-i-osmometer',
    brandId: 'loeser',
    familyId: 'osmometria',
    name: 'i Osmometer',
    scope: 'modelo',
    does: 'Osmómetro automático con impresora térmica y lector de código integrados, pantalla en color y registro de cambios y de inicios de sesión.',
    uses: [
      'Laboratorio clínico con volumen alto de muestras',
      'Trazabilidad por muestra, usuario y estado de calibración',
      'Puesto en que el resultado se imprime junto a la muestra',
    ],
    criteria: [
      { label: 'Volumen de muestra', value: '100 o 50 µl' },
      { label: 'Tiempo de medición', value: 'Aprox. 1,5 min con 100 µl' },
      { label: 'Rango', value: '0 a 2.500 mosm/kg H₂O, resolución 1 mosm/kg' },
      { label: 'Memoria', value: '600 mediciones con fecha, hora, usuario y estado de calibración' },
      { label: 'Documentación', value: 'Impresora térmica y lector de código integrados' },
      { label: 'Interfaces', value: '1 USB y 2 RS-232' },
    ],
    officialUrl: 'http://www.loeser-osmometer.de/typ16-eng.html',
    officialUrlScope: 'modelo',
    officialPdfUrl: null,
    officialUrlInsecure: true,
    verifiedOn: READ_ON,
  },
  {
    id: 'loeser-i-cryometer',
    brandId: 'loeser',
    familyId: 'osmometria',
    name: 'i Cryometer',
    scope: 'modelo',
    does: 'Criómetro automático programado de fábrica para soluciones de benceno, con el mismo cuerpo y la misma documentación que el i Osmometer.',
    uses: [
      'Determinación de masa molar por crioscopía',
      'Control de concentración en disolvente orgánico',
      'Investigación en química de polímeros y de materiales',
    ],
    criteria: [
      { label: 'Volumen de muestra', value: '100 o 200 µl' },
      { label: 'Tiempo de medición', value: 'Aprox. 1,3 min con 150 µl' },
      { label: 'Rango', value: '0 a 1.000 mmol/kg de benceno, resolución 1 mmol/kg' },
      { label: 'Reproducibilidad', value: '±1 % o ±2 mmol' },
      { label: 'Memoria', value: '600 mediciones con fecha, hora y usuario' },
      { label: 'Otros disolventes', value: 'A consultar con el fabricante' },
    ],
    officialUrl: 'http://www.loeser-osmometer.de/typ21-eng.html',
    officialUrlScope: 'modelo',
    officialPdfUrl: null,
    officialUrlInsecure: true,
    verifiedOn: READ_ON,
  },

  /* -- SERVA Electrophoresis · electroforesis ---------------------------- */
  {
    id: 'serva-bluevertical-prime',
    brandId: 'serva',
    familyId: 'electroforesis',
    name: 'BlueVertical PRiME',
    code: 'BV-104.01',
    scope: 'modelo',
    does: 'Cubeta vertical para minigeles de 10 × 10 × 0,7 cm, propios o precolados, con un cierre que fija el gel a la cámara interior sin tornillos ni pinzas.',
    uses: [
      'SDS-PAGE y electroforesis nativa de proteínas',
      'Separación de fragmentos de ácidos nucleicos en minigel',
      'Transferencia en tanque con el módulo de blot opcional',
      'Docencia y laboratorio de rutina en formato minigel',
    ],
    criteria: [
      { label: 'Formato de gel', value: '10 × 10 × 0,7 cm, propio o precolado' },
      { label: 'Tampón', value: '200 ml en cámara interior, 450 ml en exterior' },
      { label: 'Máximos', value: '500 V y 250 mA' },
      { label: 'Electrodo', value: 'Varilla recubierta de platino' },
      { label: 'Dimensiones', value: '16 × 15,6 × 9,5 cm' },
      { label: 'Ampliaciones', value: 'Módulo de blot BV-104-B y soporte de colado BV-104-CS' },
    ],
    officialUrl:
      'https://www.serva.de/enDE/ProductDetails/4741_BV-104_BlueVertical_TM_PRiME_TM_Mini_Slab_Gel_Unit_0_0.html',
    officialUrlScope: 'modelo',
    officialPdfUrl: 'https://www.serva.de/www_root/documents/Electrophoresis%20by%20SERVA_web.pdf',
    sourceNote: 'El PDF es el catálogo de electroforesis del fabricante; no hay ficha PDF por modelo.',
    verifiedOn: READ_ON,
  },
  {
    id: 'serva-hpe-bluehorizon',
    brandId: 'serva',
    familyId: 'electroforesis',
    name: 'HPE BlueHorizon',
    code: 'HPE-BH.01',
    scope: 'modelo',
    does: 'Cámara horizontal de lecho plano con placa cerámica de refrigeración y tres posiciones de electrodo, para geles soportados en película de hasta 260 × 205 mm.',
    uses: [
      'Enfoque isoeléctrico en gel soportado',
      'Segunda dimensión de electroforesis bidimensional',
      'SDS-PAGE de alta resolución en formato grande',
      'Separaciones que exigen temperatura controlada',
    ],
    criteria: [
      { label: 'Formato de gel', value: 'Soportado en película, hasta 260 × 205 mm' },
      { label: 'Refrigeración', value: 'Placa cerámica integrada, conectada a un refrigerador de agua' },
      {
        label: 'Electrodos',
        value: 'Tres posiciones de varilla de platino: 270 mm fija, 195 mm fija y 115 mm variable',
      },
      { label: 'Construcción', value: 'Carcasa metálica con cajón integrado' },
      { label: 'Fuente recomendada', value: 'BluePower 3000 HPE' },
      { label: 'Sistema completo', value: 'HPE-BHSYS, con unidad de refrigeración' },
    ],
    officialUrl:
      'https://www.serva.de/enDE/ProductDetails/5120_HPE-BH_HPE_TM_BlueHorizon_TM_212_457.html',
    officialUrlScope: 'modelo',
    officialPdfUrl: 'https://www.serva.de/www_root/documents/Electrophoresis%20by%20SERVA_web.pdf',
    sourceNote: 'El PDF es el catálogo de electroforesis del fabricante; no hay ficha PDF por modelo.',
    verifiedOn: READ_ON,
  },
  {
    id: 'serva-bluemarine-100',
    brandId: 'serva',
    familyId: 'electroforesis',
    name: 'BlueMarine 100',
    code: 'BM-100',
    scope: 'modelo',
    does: 'Cámara submarina de acrílico para geles de agarosa de 7 × 10 cm, para análisis rápido de hasta 28 muestras de ácidos nucleicos.',
    uses: [
      'Comprobación de productos de PCR',
      'Control de integridad de ADN y de ARN',
      'Digestión con enzimas de restricción',
      'Docencia en biología molecular',
    ],
    criteria: [
      { label: 'Formato de gel', value: '7 × 10 cm' },
      { label: 'Muestras', value: 'Hasta 28 por corrida' },
      { label: 'Construcción', value: 'Acrílico, con material de electrodo resistente' },
      { label: 'Técnica', value: 'Electroforesis submarina en agarosa' },
      { label: 'Formato mayor', value: 'BlueMarine 200, de 15 × 15 cm y 15 × 20 cm' },
    ],
    officialUrl: 'https://www.serva.de/enDE/ProductDetails/2291_BM-100_BlueMarine_TM_100_0_208.html',
    officialUrlScope: 'modelo',
    officialPdfUrl: 'https://www.serva.de/www_root/documents/Electrophoresis%20by%20SERVA_web.pdf',
    sourceNote: 'El PDF es el catálogo de electroforesis del fabricante; no hay ficha PDF por modelo.',
    verifiedOn: READ_ON,
  },
  {
    id: 'serva-bluepower',
    brandId: 'serva',
    familyId: 'electroforesis',
    name: 'BluePower',
    scope: 'familia',
    does: 'Fuentes de alimentación programables para electroforesis, con voltaje, corriente y potencia constantes y conmutación automática entre los tres.',
    uses: [
      'Alimentar una o varias cubetas verticales a la vez',
      'Transferencia en tanque y semiseca',
      'Enfoque isoeléctrico con rampa de voltaje',
      'Corridas con integrador de voltios por hora para reproducibilidad',
    ],
    criteria: [
      { label: 'BluePower 300 BLOT', value: 'Cat. BP-300-BLO, para transferencia' },
      { label: 'BluePower 600 PRIME', value: 'Cat. BP-600-PRI, 600 V, 1.000 mA y 300 W' },
      { label: 'BluePower 3000 HPE', value: 'Cat. BP-3000-HPE, para el sistema horizontal HPE' },
      { label: 'BluePower 6000 IPG', value: 'Cat. BP-6000-IPG, para enfoque isoeléctrico' },
      { label: 'Programación', value: 'Hasta 9 programas de 9 pasos, con registro por USB' },
      { label: 'Seguridad', value: 'Protección de sobrecarga y corte por fuga a tierra' },
    ],
    officialUrl:
      'https://www.serva.de/enDE/Catalog/459_Laboratory_Equipment_Electrophoresis_Devices_Power_Supplies_212_449.html',
    officialUrlScope: 'familia',
    officialPdfUrl: 'https://www.serva.de/www_root/documents/Electrophoresis%20by%20SERVA_web.pdf',
    sourceNote:
      'Las cifras de la BluePower 600 PRIME salen de su ficha del fabricante. De las otras tres se publica el código de catálogo y para qué está pensada cada una, no sus rangos: se confirman al cotizar.',
    verifiedOn: READ_ON,
  },
];

export function modelsForBrand(brandId: string): BrandModel[] {
  return brandModels.filter((model) => model.brandId === brandId);
}

export function modelsForFamily(familyId: string): BrandModel[] {
  return brandModels.filter((model) => model.familyId === familyId);
}

export function familyDocsFor(familyId: string): FamilyDocumentation | undefined {
  return familyDocumentation.find((entry) => entry.familyId === familyId);
}
