/**
 * Registro de afirmaciones comerciales verificables.
 *
 * Toda cifra visible en el sitio público tiene que existir aquí con su
 * redacción exacta, su fuente, la fecha en que se midió, quién la aprobó y si
 * es pública. Una cifra sin fuente y sin aprobación no se renderiza: no basta
 * con que sea cierta, tiene que ser comprobable por alguien que llegue después.
 *
 * Dos guardas automáticas sostienen la regla:
 *
 * - `validate:catalog` comprueba la forma de cada registro y bloquea que una
 *   afirmación numérica llegue a `status: 'approved'` sin fuente ni fecha de
 *   aprobación.
 * - `validate:dist` busca la redacción literal de toda afirmación no aprobada
 *   dentro del HTML construido y falla si aparece.
 *
 * Las entradas `unavailable` no son ruido: documentan que la cifra se evaluó y
 * se descartó, para que nadie la vuelva a proponer sin la evidencia que falta.
 */

export type ClaimStatus =
  /** Redacción, fuente y aprobación completas. Puede renderizarse. */
  | 'approved'
  /** Redactada y propuesta al negocio, aún sin aprobación. No se renderiza. */
  | 'proposed'
  /** No existe métrica comercial que la sostenga. No se redacta ni se propone. */
  | 'unavailable';

export interface Claim {
  id: string;
  /** Redacción pública exacta. Es la cadena que vigila `validate:dist`. */
  text: string;
  /** Valor numérico de la afirmación, o `null` si es cualitativa. */
  value: number | null;
  /** De dónde sale el dato: ruta del repositorio o registro de negocio citado. */
  source: string | null;
  /** Fecha en que el dato se midió o se verificó contra la fuente (ISO). */
  measuredOn: string | null;
  /** Quién autorizó su uso público. */
  approvedBy: string | null;
  /** Fecha de esa autorización (ISO). */
  approvedOn: string | null;
  status: ClaimStatus;
  visibility: 'public' | 'internal';
  /** Por qué no se publica, cuando corresponda. */
  note?: string;
}

export const claims: readonly Claim[] = [
  {
    id: 'marcas-con-las-que-trabajamos',
    text: '6 marcas con las que trabajamos',
    value: 6,
    source:
      'src/data/brands.ts (6 registros) y public/email/origenlab-contacto-signature.html, que publica esos mismos 6 logotipos bajo el texto verificado',
    measuredOn: '2026-09-06',
    approvedBy: 'Negocio, revisión de portada del 2026-09-06',
    approvedOn: '2026-09-06',
    status: 'approved',
    visibility: 'public',
  },
  {
    id: 'modelos-centrifuga-publicados',
    text: '5 modelos con ficha técnica publicada',
    value: 5,
    source:
      'src/data/products.ts, productos Ortoalresa con keySpecs y datasheetUrl del fabricante',
    measuredOn: '2026-09-06',
    approvedBy: 'Negocio, catálogo ya publicado en el sitio',
    approvedOn: '2026-09-06',
    status: 'approved',
    visibility: 'public',
  },
  {
    id: 'referencias-serva-publicadas',
    text: '3 referencias publicadas',
    value: 3,
    source: "src/data/products.ts, productos con brandId 'serva'",
    measuredOn: '2026-09-06',
    approvedBy: 'Negocio, catálogo ya publicado en el sitio',
    approvedOn: '2026-09-06',
    status: 'approved',
    visibility: 'public',
  },
  {
    id: 'respuesta-agil',
    text: 'Respuesta ágil y seguimiento claro',
    value: null,
    source: 'Redacción cualitativa: no declara plazo ni compromiso medible',
    measuredOn: '2026-09-06',
    approvedBy: 'Negocio, revisión de portada del 2026-09-06',
    approvedOn: '2026-09-06',
    status: 'approved',
    visibility: 'public',
  },
  {
    id: 'respuesta-inicial-un-dia-habil',
    text: 'Respuesta inicial en 1 día hábil',
    value: 1,
    source: null,
    measuredOn: null,
    approvedBy: null,
    approvedOn: null,
    status: 'proposed',
    visibility: 'internal',
    note:
      'Promesa de servicio propuesta, no aprobada. Requiere que el negocio la asuma por escrito y defina qué cuenta como respuesta inicial. Hasta entonces el sitio usa la redacción cualitativa de respuesta-agil. Es distinta del plazo de la cotización formal, que depende de precio de fábrica, configuración, flete y confirmación de entrega.',
  },
  {
    id: 'clientes-atendidos',
    text: '',
    value: null,
    source: null,
    measuredOn: null,
    approvedBy: null,
    approvedOn: null,
    status: 'unavailable',
    visibility: 'internal',
    note:
      'No existe métrica comercial autoritativa de clientes. Los contactos, organizaciones y destinatarios de campaña del CRM no son clientes: son registros de contacto. No convertir en cifra pública.',
  },
  {
    id: 'ventas-cerradas',
    text: '',
    value: null,
    source: null,
    measuredOn: null,
    approvedBy: null,
    approvedOn: null,
    status: 'unavailable',
    visibility: 'internal',
    note:
      'No existe registro consolidado de ventas cerradas aprobado para uso público. Los mensajes de correo históricos no son ventas.',
  },
  {
    id: 'cotizaciones-emitidas',
    text: '',
    value: null,
    source: null,
    measuredOn: null,
    approvedBy: null,
    approvedOn: null,
    status: 'unavailable',
    visibility: 'internal',
    note: 'No existe conteo de cotizaciones verificado para uso público.',
  },
  {
    id: 'proyectos-realizados',
    text: '',
    value: null,
    source: null,
    measuredOn: null,
    approvedBy: null,
    approvedOn: null,
    status: 'unavailable',
    visibility: 'internal',
    note: 'No existe definición de proyecto ni registro aprobado que lo sostenga.',
  },
  {
    id: 'anos-de-experiencia',
    text: '',
    value: null,
    source: null,
    measuredOn: null,
    approvedBy: null,
    approvedOn: null,
    status: 'unavailable',
    visibility: 'internal',
    note:
      'Falta la fecha de constitución de la empresa. Pendiente en docs/design/CONTENT_NEEDED.md junto con la identidad legal.',
  },
  {
    id: 'historial-de-tiempo-de-respuesta',
    text: '',
    value: null,
    source: null,
    measuredOn: null,
    approvedBy: null,
    approvedOn: null,
    status: 'unavailable',
    visibility: 'internal',
    note:
      'No hay medición histórica de tiempos de respuesta. Sin ella no puede publicarse ninguna cifra de rapidez ni presentarse un historial.',
  },
];

function findClaim(id: string): Claim | undefined {
  return claims.find((claim) => claim.id === id);
}

/**
 * Redacción publicable de una afirmación, o `undefined` si no puede rendirse.
 *
 * Es la única puerta por la que una cifra llega a una plantilla. Cuando
 * devuelve `undefined` la plantilla omite el elemento entero: no hay texto de
 * reserva que pueda confundirse con la cifra no aprobada.
 */
export function publicClaim(id: string): string | undefined {
  const claim = findClaim(id);
  if (!claim) return undefined;
  if (claim.status !== 'approved') return undefined;
  if (claim.visibility !== 'public') return undefined;
  if (!claim.text || !claim.source || !claim.approvedOn) return undefined;
  return claim.text;
}

/** Afirmaciones que el sitio decidió no publicar, para auditoría del registro. */
export function withheldClaims(): Claim[] {
  return claims.filter((claim) => claim.status !== 'approved');
}
