/**
 * Estado legal del sitio y hechos públicos de privacidad.
 *
 * Las páginas `/privacidad/`, `/cookies/` y `/aviso-legal/` se construyen desde
 * aquí. Son páginas breves: dicen lo que OrigenLab hace de verdad con los datos
 * que alguien le entrega y nada más. No declaran cumplimiento legal completo,
 * no inventan identidad societaria y no muestran al visitante la lista de lo
 * que falta por confirmar. Ese seguimiento es interno y vive en
 * `docs/design/CONTENT_NEEDED.md`.
 *
 * Reglas de este archivo:
 *
 * - **Identidad societaria, decidida el 2026-09-21.** El sitio se presenta con
 *   el nombre comercial OrigenLab. No publica RUT, representante legal, razón
 *   social ni domicilio legal, y no se rellenan desde el repositorio.
 * - **Mientras `legalStatus.reviewedBy` sea `null`**, las tres rutas se sirven
 *   con `noindex`, fuera del sitemap, con `Disallow` en `robots.txt` y con
 *   `X-Robots-Tag` en `.htaccess`: sin identidad legal publicada no pueden
 *   presentarse como definitivas. Eso no se explica en la página.
 * - **`SITE_CLAIMS` guarda la redacción literal** de las dos afirmaciones que
 *   `validate:dist` y `validate:privacy` comprueban sobre el HTML construido.
 *   Si el sitio cambia de comportamiento, la validación rompe antes de que el
 *   texto envejezca.
 */

export const legalStatus = {
  /** `draft` hasta que un profesional habilitado en Chile revise el texto. */
  state: 'draft' as const,
  lastReviewed: '2026-09-21',
  /** Profesional que revisó y aprobó el texto. Sin esto no se indexa. */
  reviewedBy: null as string | null,
  /** Fecha de esa revisión. */
  reviewedOn: null as string | null,
} as const;

/* -- Afirmaciones comprobadas sobre el HTML construido --------------------- */

/**
 * Redacción literal de las dos afirmaciones que las validaciones buscan en
 * `dist/`. Viven aquí para que la frase publicada y la frase comprobada sean
 * la misma cadena y no dos copias que se separan con el tiempo.
 */
export const SITE_CLAIMS = {
  /**
   * Sólo puede publicarse mientras la compilación no dibuje ningún formulario.
   * `validate:dist` comprueba las dos direcciones: sitio con formulario que lo
   * niega, y sitio sin formulario que ya no lo dice.
   */
  noForms: 'El sitio no tiene formularios ni recibe envíos',
  /**
   * Acotada a propósito a la aplicación. Lo que emita la infraestructura por
   * delante del origen es otra cosa y la página de cookies lo dice aparte.
   */
  noAppStorage: 'La aplicación de OrigenLab no fija cookies ni almacenamiento del navegador',
} as const;

/* -- Pendientes internos ---------------------------------------------------- */

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

/**
 * Seguimiento interno. **No se renderiza en ninguna página.** El sitio público
 * dejó de enseñarle al visitante la lista de lo que falta; el seguimiento vive
 * en `docs/design/CONTENT_NEEDED.md` y estos registros son su reflejo en código.
 *
 * Los datos societarios no están en esta lista porque no son un pendiente: el
 * negocio decidió no publicarlos.
 */
export const requiredLegalFacts: readonly RequiredLegalFact[] = [
  {
    id: 'bases-licitud',
    label: 'Bases de licitud de cada tratamiento',
    value: null,
    owner: 'LEGAL',
    blocks: 'Una política de privacidad que se presente como definitiva',
  },
  {
    id: 'plazos-conservacion',
    label: 'Plazos de conservación',
    value: null,
    owner: 'LEGAL',
    blocks: 'Una política de privacidad que se presente como definitiva',
  },
  {
    id: 'encargados-tratamiento',
    label: 'Acuerdos con encargados de tratamiento',
    value: null,
    owner: 'CONTENIDO',
    blocks: 'Inventario formal de encargados y análisis de transferencia internacional',
  },

];

/* -- Proveedores técnicos actuales ----------------------------------------- */

export interface TechnicalProvider {
  id: string;
  label: string;
  /** Para qué interviene. Una finalidad por fila. */
  role: string;
}

/**
 * Los tres proveedores que hoy intervienen, confirmados por el negocio el
 * 2026-09-21. No se nombra ninguno más: un proveedor que no está en uso no se
 * anuncia, y el boletín sigue desactivado, sin Worker, sin base de datos y sin
 * remitente.
 */
export const technicalProviders: readonly TechnicalProvider[] = [
  {
    id: 'hostgator',
    label: 'HostGator',
    role: 'aloja el sitio',
  },
  {
    id: 'cloudflare',
    label: 'Cloudflare',
    role: 'actúa como proxy, DNS y capa de seguridad del dominio',
  },
  {
    id: 'titan',
    label: 'Titan',
    role: 'entrega el correo empresarial que recibe sus mensajes',
  },
];

/* -- Rutas ----------------------------------------------------------------- */

/** Rutas legales. Fuera del sitemap y sin indexar mientras no haya revisión. */
export const legalRoutes = [
  { href: '/privacidad/', label: 'Privacidad' },
  { href: '/cookies/', label: 'Cookies' },
  { href: '/aviso-legal/', label: 'Aviso legal' },
] as const;

export function pendingLegalFacts(): RequiredLegalFact[] {
  return requiredLegalFacts.filter((fact) => fact.value === null);
}

export function legalFact(id: string): RequiredLegalFact | undefined {
  return requiredLegalFacts.find((fact) => fact.id === id);
}

/** El texto legal sólo se indexa tras revisión profesional. */
export function isLegalTextApproved(): boolean {
  return legalStatus.reviewedBy !== null && legalStatus.reviewedOn !== null;
}
