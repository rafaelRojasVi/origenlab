/**
 * Categorías de producto alineadas al negocio actual.
 * buyerGuide: copy orientado al comprador; alto nivel, sin specs inventadas.
 */
const catalogBullet =
  'Catálogos y fichas técnicas según línea; solicítelos al cotizar.' as const;

export interface Category {
  id: string;
  /** Nombre completo, usado como H1 de la página de aplicación. */
  name: string;
  /** Nombre corto para navegación, listas y migas. */
  shortName: string;
  slug: string;
  description?: string;
  buyerGuide: string;
  buyerBullets: readonly string[];
}

export const categories: Category[] = [
  {
    id: '1',
    name: 'Equipos para alimentos',
    shortName: 'Alimentos',
    slug: 'alimentos',
    description:
      'Equipamiento para análisis y control en entornos de alimentos y calidad asociada.',
    buyerGuide:
      'Si su laboratorio trabaja con inocuidad, calidad o análisis asociados a alimentos, puede cotizar equipos alineados a ese entorno. No publicamos listado cerrado: la oferta depende de proyecto y disponibilidad.',
    buyerBullets: [
      'Indique tipo de ensayo o norma que maneja (si aplica) y volumen aproximado de trabajo.',
      'Podemos orientar por aplicación; la propuesta formal va por cotización.',
      catalogBullet,
    ],
  },
  {
    id: '2',
    name: 'Equipos para control de calidad',
    shortName: 'Control de calidad',
    slug: 'control-de-calidad',
    description:
      'Instrumentación y equipos orientados a control de calidad y ensayos en laboratorio.',
    buyerGuide:
      'Laboratorios de control de calidad suelen combinar medición, ensayo y trazabilidad. OrigenLab cotiza equipamiento según su flujo; sin prometer marcas ni plazos hasta tener su consulta concreta.',
    buyerBullets: [
      'Útil traer referencia de equipo o función (ej. medición, preparación de muestra).',
      'Cotización y documentación de apoyo según línea disponible.',
      catalogBullet,
    ],
  },
  {
    id: '3',
    name: 'Equipos para laboratorio clínico',
    shortName: 'Laboratorio clínico',
    slug: 'laboratorio-clinico',
    description:
      'Líneas de equipamiento para laboratorio clínico; cotización y asesoría según necesidad.',
    buyerGuide:
      'El equipamiento clínico varía según especialidad y carga de trabajo. Escríbanos con el tipo de servicio o equipo buscado; respondemos con alternativas acotadas a lo que podamos ofrecer en cada momento.',
    buyerBullets: [
      'No sustituimos criterio médico ni normativa: cotizamos equipamiento comercial.',
      'Asesoría y puesta en marcha en equipos más complejos según acuerdo.',
      catalogBullet,
    ],
  },
];
