/**
 * A first, visual sort of the pending queue — and nothing more than that.
 *
 * Thirty-one pending messages is a queue nobody reads, because the twenty that are real
 * commercial correspondence sit between a Google Workspace onboarding drip and a Tidio
 * signup confirmation. The reviewer's first act is always the same triage, so the surface
 * does it once, out loud, and lets the reviewer disagree.
 *
 * **Three properties make that safe.**
 *
 * 1. *It proposes nothing durable.* A verdict is a `category` and the `reasons` that produced
 *    it. There is no score, no promotion, no "recommended action" and no write path — a
 *    category never becomes a fact about a person or an institution.
 * 2. *Every verdict shows its work.* Each reason names the observed thing that produced it:
 *    the subject marker matched, the sender's mailbox, the platform domain. A reviewer who
 *    disagrees can see exactly what to disagree with.
 * 3. *Nothing is hidden or removed.* This is a **partition** of the queue, proven by test:
 *    every record lands in exactly one bucket, the counts of all four are always on screen,
 *    and filtering to one bucket is a view, never a deletion.
 *
 * It reads only fields the API already returns — sender, domain, subject. No new route, no
 * new column, no migration. If the triage is wrong, the cost is one wrong chip on a row the
 * operator was going to read anyway.
 */

import type { V2EvidenceRecord } from "../api/v2Types";

export type TriageCategory =
  | "commercial"
  | "counterparty_auto_reply"
  | "vendor_notice"
  | "unclassified";

/** The order is the reading order of the workspace: useful work first, noise last. */
export const TRIAGE_CATEGORIES: readonly TriageCategory[] = [
  "commercial",
  "counterparty_auto_reply",
  "vendor_notice",
  "unclassified",
];

export type TriageReasonKind =
  | "auto_reply_subject"
  | "no_reply_mailbox"
  | "platform_domain"
  | "security_subject"
  | "commercial_subject"
  | "no_signal";

export interface TriageReason {
  kind: TriageReasonKind;
  /** The observed fact, named. Never a recommendation and never a judgement of the sender. */
  text: string;
}

export interface TriageVerdict {
  category: TriageCategory;
  reasons: TriageReason[];
}

/**
 * Subject markers that say "this was written by a mail server, not by a person".
 *
 * The Chilean ones matter as much as the English: a Chilean institutional mailbox answers an
 * absence with `Feriado Legal` or `Permiso Administrativo` in front of the original subject,
 * which carries none of the words an English auto-reply filter looks for.
 */
const AUTO_REPLY_SUBJECT_MARKERS: readonly string[] = [
  "automatic reply",
  "auto-reply",
  "auto reply",
  "autoreply",
  "respuesta automática",
  "respuesta automatica",
  "autorespuesta",
  "out of office",
  "out-of-office",
  "fuera de oficina",
  "fuera de la oficina",
  "feriado legal",
  "permiso administrativo",
  "ausencia laboral",
  "vacaciones",
  "vacation reply",
  "vacation responder",
  "acuse de recibo",
];

/**
 * Mailboxes that cannot be answered.
 *
 * A `no-reply` address is not itself a category: `no-reply@accounts.google.com` is our own
 * security tooling, while `proveedores+noreply@` at a hospital is a counterparty's desk that
 * happens to refuse replies. The domain decides which; this set only decides that the mailbox
 * is a machine's.
 */
const NO_REPLY_LOCAL_PARTS: ReadonlySet<string> = new Set([
  "no-reply",
  "noreply",
  "donotreply",
  "do-not-reply",
  "mailer-daemon",
  "postmaster",
]);

/**
 * Platforms the operator is a *customer* of.
 *
 * A message from one of these is about our own tooling — a signup, a security alert, a
 * product drip — and is never commercial correspondence with a laboratory. The list is
 * explicit rather than inferred because guessing "is this a SaaS vendor" from a domain is
 * exactly the kind of interpretation this surface refuses to make silently. A domain that is
 * not on it simply gets no such reason.
 */
const PLATFORM_DOMAINS: readonly string[] = [
  "google.com",
  "accounts.google.com",
  "googlemail.com",
  "tidio.com",
  "tidio.net",
  "account.tidio.com",
];

const SECURITY_SUBJECT_MARKERS: readonly string[] = [
  "alerta de seguridad",
  "security alert",
  "critical security alert",
  "nuevo inicio de sesión",
  "nuevo inicio de sesion",
  "suspicious sign-in",
];

/**
 * Subject markers of real commercial correspondence.
 *
 * Deliberately concrete — a quote, a purchase order, an order number, a standard being asked
 * for by name. A bare `Re:` is *not* here: it says a thread exists, not that it is
 * commercial, and treating it as commercial is how a queue fills back up with noise.
 */
const COMMERCIAL_SUBJECT_MARKERS: readonly string[] = [
  "cotiz",
  "quotation",
  "quote",
  "presupuesto",
  "precio",
  "orden de compra",
  "purchase order",
  "oc ",
  "pedido",
  "solicitud de compra",
  "solicitud de cotiz",
  "insumo",
  "equipos",
  "estándar",
  "estandar",
  "std ",
  "excedentes",
  "factura",
  "despacho",
  "su solicitud",
];

function normalised(value: string | null): string {
  return (value ?? "").toLowerCase().trim();
}

function localPartOf(address: string | null): string {
  const value = normalised(address);
  const at = value.indexOf("@");
  return at > 0 ? value.slice(0, at) : value;
}

/**
 * `proveedores+noreply@` and `ventas.no-reply@` are the same machine desk under a suffix.
 * Only an explicit separator counts, so a name like `noreplica` is never read as one.
 */
function isNoReplyMailbox(address: string | null): boolean {
  const local = localPartOf(address);
  if (!local) {
    return false;
  }
  if (NO_REPLY_LOCAL_PARTS.has(local)) {
    return true;
  }
  const parts = local.split(/[+._-]/);
  return parts.length > 1 && parts.some((part) => NO_REPLY_LOCAL_PARTS.has(part));
}

/**
 * The sender's own domain, and the platform it belongs to — which are not always the same
 * string. `accounts.google.com` belongs to `google.com`, and a reason that named only the
 * parent would be describing a domain the operator never saw on the message.
 */
function platformMatchOf(
  record: V2EvidenceRecord,
): { observed: string; platform: string } | null {
  const observed = normalised(record.from_domain);
  if (!observed) {
    return null;
  }
  const platform = PLATFORM_DOMAINS.find(
    (candidate) => observed === candidate || observed.endsWith(`.${candidate}`),
  );
  return platform ? { observed, platform } : null;
}

function firstMarker(haystack: string, markers: readonly string[]): string | null {
  return markers.find((marker) => haystack.includes(marker)) ?? null;
}

/**
 * The one verdict for one record.
 *
 * Order is the whole design. An away note about a quote is still an away note, so the
 * auto-reply test runs before the commercial one; a platform notice is decided by the domain
 * rather than by its subject, because a Google drip campaign's subject reads commercial.
 * Anything that matches nothing stays `unclassified` rather than being pushed into the
 * commercial batch to look complete.
 */
export function triageOf(record: V2EvidenceRecord): TriageVerdict {
  const subject = normalised(record.subject);
  const address = normalised(record.from_address);
  const platform = platformMatchOf(record);
  const reasons: TriageReason[] = [];

  const autoReplyMarker = firstMarker(subject, AUTO_REPLY_SUBJECT_MARKERS);
  if (autoReplyMarker) {
    reasons.push({
      kind: "auto_reply_subject",
      text: `El asunto contiene «${autoReplyMarker}»: lo escribió un servidor de correo, no la persona.`,
    });
    return { category: "counterparty_auto_reply", reasons };
  }

  if (platform) {
    reasons.push({
      kind: "platform_domain",
      text:
        platform.observed === platform.platform
          ? `El remitente está en ${platform.observed}, una plataforma que nosotros contratamos: el mensaje habla de nuestra propia herramienta, no de un laboratorio.`
          : `El remitente está en ${platform.observed}, bajo ${platform.platform} — una plataforma que nosotros contratamos: el mensaje habla de nuestra propia herramienta, no de un laboratorio.`,
    });
    const securityMarker = firstMarker(subject, SECURITY_SUBJECT_MARKERS);
    if (securityMarker) {
      reasons.push({
        kind: "security_subject",
        text: `El asunto contiene «${securityMarker}»: es un aviso de seguridad de nuestra cuenta.`,
      });
    }
    return { category: "vendor_notice", reasons };
  }

  if (isNoReplyMailbox(address)) {
    reasons.push({
      kind: "no_reply_mailbox",
      text: `${address} es un buzón que no acepta respuesta. Viene de una contraparte, pero no hay una persona al otro lado de esta dirección.`,
    });
    return { category: "counterparty_auto_reply", reasons };
  }

  const commercialMarker = firstMarker(subject, COMMERCIAL_SUBJECT_MARKERS);
  if (commercialMarker) {
    reasons.push({
      kind: "commercial_subject",
      text: `El asunto contiene «${commercialMarker.trim()}»: es correspondencia comercial y merece una lectura humana.`,
    });
    return { category: "commercial", reasons };
  }

  reasons.push({
    kind: "no_signal",
    text: subject
      ? "Ningún indicio decide esto: ni marca de respuesta automática, ni plataforma conocida, ni asunto comercial. Queda para que lo leas tú."
      : "El mensaje no trae asunto observable. No hay nada sobre lo que sugerir una categoría.",
  });
  return { category: "unclassified", reasons };
}

const CATEGORY_LABELS: Record<TriageCategory, string> = {
  commercial: "Correspondencia comercial",
  counterparty_auto_reply: "Respuesta automática de contraparte",
  vendor_notice: "Aviso de proveedor o seguridad",
  unclassified: "Sin clasificar",
};

export function triageCategoryLabel(category: TriageCategory): string {
  return CATEGORY_LABELS[category];
}

const CATEGORY_CAPTIONS: Record<TriageCategory, string> = {
  commercial: "Alguien escribió por un equipo, una cotización o una compra. Aquí está el trabajo.",
  counterparty_auto_reply:
    "Una contraparte real, contestada por su servidor: ausencia, feriado o buzón sin respuesta. La dirección sigue siendo evidencia; el mensaje no dice nada.",
  vendor_notice:
    "Google, Tidio y avisos de seguridad de nuestras propias cuentas. No es correspondencia con un laboratorio.",
  unclassified: "Nada decidió la categoría. Se muestra entero para que lo leas tú.",
};

export function triageCategoryCaption(category: TriageCategory): string {
  return CATEGORY_CAPTIONS[category];
}

export function triageCounts(
  records: readonly V2EvidenceRecord[],
): Record<TriageCategory, number> {
  const counts: Record<TriageCategory, number> = {
    commercial: 0,
    counterparty_auto_reply: 0,
    vendor_notice: 0,
    unclassified: 0,
  };
  for (const record of records) {
    counts[triageOf(record).category] += 1;
  }
  return counts;
}

/** A view of the queue, in the queue's own order. Nothing is removed from anything. */
export function recordsInCategory(
  records: readonly V2EvidenceRecord[],
  category: TriageCategory,
): V2EvidenceRecord[] {
  return records.filter((record) => triageOf(record).category === category);
}
