/**
 * Boletín de OrigenLab: estado de publicación, categorías de interés y el
 * texto de consentimiento con su versión.
 *
 * Este archivo no describe un formulario que exista en producción. Describe
 * las condiciones bajo las cuales podría existir, y mientras alguna falte el
 * sitio construido no contiene ningún formulario.
 *
 * Reglas de este archivo:
 *
 * - **La activación es una conjunción, no una bandera.** `newsletterGates`
 *   enumera las ocho condiciones y `isNewsletterPublished()` exige todas. Que
 *   el Worker exista no publica nada; que el texto legal esté aprobado tampoco.
 * - **La vista previa no puede filtrarse a producción.** `isNewsletterPreview()`
 *   sólo es cierta en el servidor de desarrollo o en una compilación que pide
 *   la vista previa por variable de entorno privada, y esa compilación se
 *   escribe en `dist-preview/`, nunca en `dist/`. `validate:dist` comprueba
 *   sobre `dist/` que no hay formulario mientras las puertas no estén abiertas.
 * - **Ningún texto de este archivo inventa una promesa de negocio.** La
 *   frecuencia de envío y el plazo de conservación no están aquí porque nadie
 *   los ha confirmado: viven como pendientes en `legal.ts`.
 */

import { equipmentScope } from './equipmentScope';
import { isLegalTextApproved } from './legal';

/**
 * Versión del texto de consentimiento. Cambia cuando cambia una sola palabra
 * de `consentText`, porque cada suscripción guarda la versión que aceptó y una
 * versión reutilizada haría ilegible el registro.
 */
export const CONSENT_TEXT_VERSION = '2026-09-21';

/**
 * El texto que la persona acepta. Se guarda por versión junto a la suscripción.
 * Describe exactamente lo que ocurre y no pide nada más que lo que hace falta.
 */
export const consentText =
  'Autorizo a OrigenLab a enviarme comunicaciones comerciales sobre equipamiento de laboratorio al correo que indico. Puedo retirar esta autorización en cualquier momento desde el enlace de baja de cada mensaje.';

/** Ruta del endpoint. Misma procedencia que el sitio: no hay petición cruzada. */
export const NEWSLETTER_ENDPOINT = '/api/newsletter/subscribe';

/* -- Puertas de activación ------------------------------------------------- */

export type GateOwner = 'LEGAL' | 'NEGOCIO' | 'INFRAESTRUCTURA';

export interface NewsletterGate {
  id: string;
  label: string;
  /** Qué significa exactamente que esta puerta esté abierta. */
  detail: string;
  owner: GateOwner;
  /** `false` mientras nadie lo haya hecho. Nunca se marca por optimismo. */
  met: boolean;
}

/**
 * Las ocho condiciones de activación, fijadas por el titular del negocio el
 * 2026-09-21. El boletín no se publica por tener código: se publica cuando las
 * ocho son ciertas y alguien lo ha comprobado.
 */
export const newsletterGates: readonly NewsletterGate[] = [
  {
    id: 'texto-legal-aprobado',
    label: 'Texto legal aprobado',
    detail:
      'Un profesional habilitado en Chile revisó la política de privacidad y el negocio aportó la identidad legal. Se comprueba contra legal.ts, no se declara aquí.',
    owner: 'LEGAL',
    met: isLegalTextApproved(),
  },
  {
    id: 'plazo-conservacion-aprobado',
    label: 'Plazo de conservación aprobado',
    detail:
      'Cuánto tiempo se conserva una suscripción, una baja y su evidencia, y qué se hace al vencer el plazo.',
    owner: 'LEGAL',
    met: false,
  },
  {
    id: 'worker-desplegado',
    label: 'Worker desplegado',
    detail:
      'El Worker de suscripción responde en la ruta de origenlab.cl con la configuración de producción.',
    owner: 'INFRAESTRUCTURA',
    met: false,
  },
  {
    id: 'esquema-d1-aplicado',
    label: 'Esquema D1 aplicado',
    detail: 'La base D1 de producción existe y tiene aplicadas las migraciones del Worker.',
    owner: 'INFRAESTRUCTURA',
    met: false,
  },
  {
    id: 'remitente-real-configurado',
    label: 'Remitente de confirmación configurado',
    detail:
      'Hay un remitente real de correo de confirmación y no el remitente nulo. Con el remitente nulo el formulario queda cerrado por definición.',
    owner: 'INFRAESTRUCTURA',
    met: false,
  },
  {
    id: 'baja-operativa',
    label: 'Ruta de baja operativa',
    detail:
      'La baja por enlace y la baja de un clic responden en producción y dejan el mismo registro de supresión.',
    owner: 'INFRAESTRUCTURA',
    met: false,
  },
  {
    id: 'humo-produccion',
    label: 'Prueba de humo en producción',
    detail:
      'Alta, confirmación y baja comprobadas de extremo a extremo contra el despliegue real, con el resultado registrado.',
    owner: 'INFRAESTRUCTURA',
    met: false,
  },
  {
    id: 'supresion-integrada',
    label: 'Supresión integrada con campañas',
    detail:
      'El envío de campañas consulta el estado canónico de baja y supresión antes de enviar. Una copia manual de suscriptores no cumple esta condición.',
    owner: 'NEGOCIO',
    met: false,
  },
];

export function pendingNewsletterGates(): NewsletterGate[] {
  return newsletterGates.filter((gate) => !gate.met);
}

/**
 * El boletín está publicado cuando las ocho puertas están abiertas. No hay
 * atajo, no hay bandera suelta y no hay entorno que lo fuerce.
 */
export function isNewsletterPublished(): boolean {
  return newsletterGates.every((gate) => gate.met);
}

/**
 * Vista previa de revisión.
 *
 * Cierta en el servidor de desarrollo, y en una compilación que declare
 * `ORIGENLAB_NEWSLETTER_PREVIEW=1`. Esa variable no se expone al navegador: se
 * inyecta como constante de compilación en `astro.config.mjs`, que además
 * manda la salida a `dist-preview/`. `dist/` se construye sin ella, y
 * `validate:dist` comprueba sobre `dist/` que no hay ningún formulario
 * mientras las puertas no estén abiertas, de modo que una activación
 * accidental falla la validación antes del despliegue.
 */
export function isNewsletterPreview(): boolean {
  return import.meta.env.DEV === true || __ORIGENLAB_NEWSLETTER_PREVIEW__ === true;
}

/** Qué decide si el formulario se dibuja en esta compilación. */
export function shouldRenderNewsletterForm(): boolean {
  return isNewsletterPublished() || isNewsletterPreview();
}

/**
 * Una compilación de vista previa tiene que decirlo en la página. Un `dist`
 * de revisión que se parezca a producción es exactamente el error que las
 * puertas existen para evitar.
 */
export function isPreviewOnly(): boolean {
  return !isNewsletterPublished() && isNewsletterPreview();
}

/* -- Intereses ------------------------------------------------------------- */

export interface NewsletterInterest {
  id: string;
  label: string;
  /** Qué recibiría quien lo marque. Sin promesa de frecuencia. */
  hint: string;
}

/**
 * Las seis familias de `equipmentScope.ts`, no una lista nueva. El boletín no
 * puede ofrecer una categoría que el negocio no cotiza, y mantener dos listas
 * garantiza que un día digan cosas distintas.
 */
export const newsletterInterests: readonly NewsletterInterest[] = equipmentScope.map(
  (family) => ({
    id: family.id,
    label: family.name,
    hint: family.workType,
  }),
);

export const NEWSLETTER_INTEREST_IDS: readonly string[] = newsletterInterests.map(
  (interest) => interest.id,
);

/* -- Límites del formulario ------------------------------------------------ */

/**
 * Límites de tamaño de entrada. Son los mismos que aplica el Worker: si el
 * navegador y el servidor no coinciden, el usuario ve un error que no entiende.
 */
export const FIELD_LIMITS = {
  email: 254,
  name: 120,
  organization: 160,
} as const;
