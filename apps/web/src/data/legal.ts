/**
 * Fuente central del estado legal del sitio y de los datos de identidad que
 * todavía faltan.
 *
 * Las páginas `/privacidad/` y `/aviso-legal/` se construyen desde aquí. No
 * contienen texto legal redactado como si estuviera aprobado: son borradores de
 * revisión que muestran lo verificado y **declaran expresamente lo que falta**,
 * en vez de taparlo con lenguaje jurídico genérico.
 *
 * Reglas de este archivo:
 *
 * - Ningún `value` puede rellenarse desde el repositorio. Razón social, RUT,
 *   domicilio, representante, responsable del tratamiento, bases de licitud y
 *   plazos de conservación los aporta el negocio o los confirma un abogado.
 * - Mientras `legalStatus.reviewedBy` sea `null`, las dos rutas se sirven con
 *   `noindex`, fuera del sitemap y marcadas como borrador en el pie.
 * - `siteBehaviour` describe lo que el sitio hace de verdad, comprobado en el
 *   código y en el build, no lo que sería cómodo afirmar.
 */

export const legalStatus = {
  /** `draft` hasta que un profesional habilitado en Chile revise el texto. */
  state: 'draft' as const,
  lastReviewed: '2026-09-06',
  /** Profesional que revisó y aprobó el texto. Sin esto no se publica. */
  reviewedBy: null as string | null,
  /** Fecha de esa revisión. */
  reviewedOn: null as string | null,
  /**
   * Aviso visible en cabecera de ambas rutas. No se suaviza: quien lo lea tiene
   * que entender que no está frente a una política vigente.
   */
  draftNotice:
    'Borrador en revisión. Este texto no es una política vigente ni asesoría legal. Falta la identidad legal de la empresa y la revisión de un profesional habilitado en Chile. Hasta entonces la página se sirve sin indexar.',
} as const;

export type FactOwner = 'CONTENIDO' | 'LEGAL';

export interface RequiredLegalFact {
  id: string;
  label: string;
  /** `null` mientras el dato no exista. Nunca inventar un valor. */
  value: string | null;
  owner: FactOwner;
  /** Qué queda bloqueado mientras falte. */
  blocks: string;
}

export const requiredLegalFacts: readonly RequiredLegalFact[] = [
  {
    id: 'razon-social',
    label: 'Razón social',
    value: null,
    owner: 'CONTENIDO',
    blocks: 'Identificación del titular del sitio, campo legalName en los datos estructurados',
  },
  {
    id: 'rut',
    label: 'RUT',
    value: null,
    owner: 'CONTENIDO',
    blocks: 'Identificación del titular del sitio',
  },
  {
    id: 'domicilio-legal',
    label: 'Domicilio legal y comuna',
    value: null,
    owner: 'CONTENIDO',
    blocks: 'Identificación del titular y domicilio de notificaciones',
  },
  {
    id: 'representante-legal',
    label: 'Representante legal',
    value: null,
    owner: 'CONTENIDO',
    blocks: 'Identificación del titular del sitio',
  },
  {
    id: 'responsable-tratamiento',
    label: 'Responsable del tratamiento de datos',
    value: null,
    owner: 'LEGAL',
    blocks: 'Política de privacidad: quién responde por los datos y bajo qué figura',
  },
  {
    id: 'canal-derechos',
    label: 'Canal para ejercer derechos del titular de los datos',
    value: null,
    owner: 'CONTENIDO',
    blocks: 'Vía de solicitud de acceso, rectificación, cancelación y oposición',
  },
  {
    id: 'bases-licitud',
    label: 'Bases de licitud de cada tratamiento',
    value: null,
    owner: 'LEGAL',
    blocks: 'Política de privacidad: por qué es lícito tratar cada dato',
  },
  {
    id: 'plazos-conservacion',
    label: 'Plazos de conservación',
    value: null,
    owner: 'LEGAL',
    blocks: 'Política de privacidad: cuánto tiempo se conserva cada dato',
  },
  {
    id: 'encargados-tratamiento',
    label: 'Acuerdos con encargados de tratamiento',
    value: null,
    owner: 'CONTENIDO',
    blocks: 'Inventario de encargados y análisis de transferencia internacional',
  },
  {
    id: 'necesidad-aviso-cookies',
    label: 'Si el estado actual del sitio exige aviso de cookies',
    value: null,
    owner: 'LEGAL',
    blocks: 'Decisión sobre el aviso de cookies, hoy ausente por no haber cookies que avisar',
  },
];

export interface SiteBehaviourFact {
  id: string;
  label: string;
  detail: string;
  /** Cómo se comprueba, para que la afirmación no dependa de la memoria. */
  evidence: string;
}

/**
 * Comportamiento técnico realmente verificado del sitio construido. Es la base
 * factual de cualquier política futura; si algo de esto cambia, el texto legal
 * cambia con ello.
 */
export const siteBehaviour: readonly SiteBehaviourFact[] = [
  {
    id: 'sin-terceros',
    label: 'Sin recursos de terceros',
    detail:
      'Ninguna página carga scripts, tipografías, hojas de estilo ni imágenes de otro dominio. Las tipografías están alojadas en el propio sitio.',
    evidence: 'npm run validate:dist recorre el HTML construido y falla ante cualquier recurso externo',
  },
  {
    id: 'sin-cookies',
    label: 'Sin cookies propias',
    detail:
      'El sitio no fija cookies ni usa almacenamiento local del navegador. Por eso no hay aviso de cookies: no habría nada que consentir.',
    evidence: 'No existe código que escriba cookies, localStorage, sessionStorage ni IndexedDB en src/',
  },
  {
    id: 'sin-analitica',
    label: 'Sin analítica ni píxeles',
    detail:
      'No hay analítica, gestor de etiquetas, píxel publicitario ni chat externo. Se retiró Tidio y se retiraron las tipografías de Google.',
    evidence: 'Bloqueado en validate:catalog y en validate:dist, y por la CSP de public/.htaccess',
  },
  {
    id: 'sin-formularios',
    label: 'Sin formularios',
    detail:
      'El sitio no tiene formularios ni recibe envíos. No hay ningún punto en el que el sitio recoja datos que el visitante escriba.',
    evidence: 'No existe ningún elemento form en src/, y la CSP declara form-action self',
  },
  {
    id: 'javascript',
    label: 'JavaScript mínimo y propio',
    detail:
      'El único JavaScript del sitio abre el menú móvil y revela secciones al desplazarse. No observa al visitante ni envía nada a ningún servidor.',
    evidence: 'Scripts inline en src/components/SiteHeader.astro y src/pages/index.astro',
  },
  {
    id: 'hosting',
    label: 'Alojamiento y proxy',
    detail:
      'El sitio se sirve desde HostGator con un proxy de Cloudflare por delante. Ambos procesan direcciones IP y registros de acceso como proveedores de infraestructura.',
    evidence: 'docs/deployment-status.md, cabeceras server cloudflare y cf-ray verificadas el 2026-09-05',
  },
  {
    id: 'registros',
    label: 'Registros de acceso',
    detail:
      'Los registros de servidor los genera y conserva la infraestructura, no el sitio. OrigenLab no ha confirmado qué se conserva ni por cuánto tiempo.',
    evidence: 'Pendiente: plazos-conservacion y encargados-tratamiento en este mismo archivo',
  },
];

export interface OutboundChannel {
  id: string;
  label: string;
  detail: string;
}

/**
 * Distinción que la política tiene que dejar clara: una cosa es lo que trata el
 * sitio web y otra lo que el visitante decide enviar por sus propios medios.
 */
export const outboundChannels: readonly OutboundChannel[] = [
  {
    id: 'sitio',
    label: 'Lo que trata el sitio web',
    detail:
      'Nada que el visitante escriba. El sitio es estático: entrega páginas y no recoge, almacena ni transmite datos de quien las lee, más allá de los registros técnicos de la infraestructura que lo sirve.',
  },
  {
    id: 'correo',
    label: 'Lo que usted envía por correo',
    detail:
      'Al escribir a la dirección publicada, su mensaje llega al buzón corporativo de OrigenLab, alojado en Titan. Los datos que contenga los aporta usted y quedan en ese buzón.',
  },
  {
    id: 'whatsapp',
    label: 'Lo que usted envía por WhatsApp',
    detail:
      'Los enlaces de WhatsApp del sitio abren la aplicación en su dispositivo con un mensaje ya escrito, que usted puede editar o descartar. La conversación ocurre en WhatsApp y se rige por las condiciones de esa plataforma, ajenas a OrigenLab.',
  },
];

/** Rutas legales en borrador. Fuera del sitemap y sin indexar mientras lo sean. */
export const legalRoutes = [
  { href: '/privacidad/', label: 'Privacidad' },
  { href: '/aviso-legal/', label: 'Aviso legal' },
] as const;

export function pendingLegalFacts(): RequiredLegalFact[] {
  return requiredLegalFacts.filter((fact) => fact.value === null);
}

/** El texto legal sólo puede presentarse como vigente tras revisión profesional. */
export function isLegalTextApproved(): boolean {
  return legalStatus.reviewedBy !== null && legalStatus.reviewedOn !== null;
}
