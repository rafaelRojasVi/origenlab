/**
 * Hechos de negocio confirmados para copy y reglas de IA.
 * No inventar marcas ni certificaciones; actualizar cuando haya datos formales.
 */
export const company = {
  name: 'OrigenLab',
  /** Cobertura comercial */
  geography: 'Chile',
  oneLiner:
    'Venta de equipos para laboratorios de servicio e investigación, con atención en todo Chile.',
  /** Hero: breve; detalle en secciones siguientes */
  heroSubtitle:
    'Alimentos, control de calidad y laboratorio clínico. Cotice por correo o WhatsApp; asesoría, soporte e instalación o puesta en marcha cuando el equipo lo requiera.',
  /**
   * Línea de valor del titular para la franja del hero de las páginas de
   * modelo, con el texto exacto que el titular aprobó el 2026-11-07. Sin cifras. «Distribuidor
   * en Chile de …» no va aquí: sólo lo dice `TrustStrip`, y sólo para una marca
   * con `commercialScopeConfirmed`.
   */
  valueLine:
    'Trabajamos directo con Hielscher, OHAUS y Ortoalresa · despacho a todo Chile · puesta en marcha y capacitación con fábrica',
  /** Párrafo breve home: audiencia desde datos confirmados */
  homeIntro:
    'Atendemos laboratorios de servicios e investigación, universidades, clínicas, hospitales e industrias de I+D en todo Chile. Equipamiento de laboratorio y cotización según su necesidad.',
  /** Texto seguro sobre catálogos (no inventar estructura ni marcas) */
  catalogNote:
    'Catálogos y fichas técnicas disponibles según línea de producto. Contáctenos para evaluar la alternativa adecuada para su laboratorio.',
  audience: [
    'Laboratorios de servicios',
    'Laboratorios de investigación',
    'Universidades',
    'Clínicas',
    'Hospitales',
    'Industrias de I+D',
  ] as const,
  /** Insumos pueden incorporarse más adelante */
  primaryOffer: 'equipos de laboratorio',
  valueProps: [
    {
      title: 'Tres líneas claras',
      text: 'Alimentos, control de calidad y laboratorio clínico: explora la categoría que te corresponde y pide cotización con contexto de tu laboratorio.',
    },
    {
      title: 'Acompañamiento postventa',
      text: 'Soporte y asesoría; garantía según fabricante y equipo; instalación o puesta en marcha en equipos más complejos cuando aplique.',
    },
    {
      title: 'Cotización sin fricción',
      text: 'Correo y WhatsApp con base en Valdivia; atendemos consultas desde todo Chile.',
    },
  ] as const,
} as const;
