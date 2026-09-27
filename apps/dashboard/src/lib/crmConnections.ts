/**
 * Labels and small readings for what the CRM lists and cards reach through recorded rows.
 *
 * Pure, like `crm360.ts`: every function is a reading of a response the read boundary
 * already returned. Two rules:
 *
 * 1. **Identity comes from foreign keys.** A channel is a person's, an institution's
 *    mailbox, or unattributed according to `person_id` and `organization_id` — never its
 *    address, local part or domain. An avatar's initials come from the recorded *name*
 *    and decorate it; they identify nothing.
 * 2. **Absent is "not recorded yet", not "does not exist".** The durable core is young and
 *    most of what an operator knows is not in it yet, so an empty list says so.
 */

import type {
  V2CardConnections,
  V2CaseOrganizationRole,
  V2CaseParticipant,
  V2CaseQuoteState,
  V2ConnectedInterest,
  V2ConnectedQuote,
  V2CommercialCase,
  V2Contact,
  V2ContactIdentity,
  V2Organization,
  V2OrganizationFilter,
  V2OrganizationSegment,
} from "../api/v2Types";
import { CASE_ROLE_ORDER, caseRoleLabel } from "./commercialCase";

// -------------------------------------------------------------------- identity

/** The recorded identity of a channel, read from its two foreign keys and nothing else. */
export function contactIdentityOf(
  row: Pick<V2Contact, "person_id" | "organization_id">,
): V2ContactIdentity {
  if (row.person_id) {
    return "person";
  }
  return row.organization_id ? "organization_mailbox" : "unattributed";
}

export const CONTACT_IDENTITY_LABELS: Record<V2ContactIdentity, string> = {
  person: "Persona confirmada",
  organization_mailbox: "Buzón de institución",
  unattributed: "Dirección sin atribuir",
};

export const CONTACT_IDENTITY_HINTS: Record<V2ContactIdentity, string> = {
  person: "Hay una persona registrada como titular de esta dirección.",
  organization_mailbox:
    "La dirección está registrada a nombre de una institución; no hay persona registrada.",
  unattributed:
    "Nadie ha registrado a quién pertenece. El dominio no basta para atribuirla.",
};

/**
 * Up to two letters for an avatar, from the recorded name.
 *
 * Words shorter than three letters (`de`, `la`, `y`) are skipped so "Universidad de Chile"
 * reads "UC" rather than "UD". A name with no letters at all falls back to "·".
 */
export function organizationInitials(name: string): string {
  const words = name
    .split(/[\s\-—–/.,()]+/)
    .map((word) => word.replace(/[^\p{L}\p{N}]/gu, ""))
    .filter((word) => word.length > 0);
  const significant = words.filter((word) => word.length >= 3);
  const pool = significant.length > 0 ? significant : words;
  const letters = pool
    .slice(0, 2)
    .map((word) => word[0]!.toUpperCase())
    .join("");
  return letters || "·";
}

// ---------------------------------------------------------------- organizations

export const ORGANIZATION_FILTER_LABELS: Record<V2OrganizationFilter, string> = {
  contacts: "Con contactos",
  people: "Con personas",
  cases: "Con casos",
  open_cases: "Con casos abiertos",
  interests: "Con intereses",
  quotes: "Con cotizaciones",
};

export const ORGANIZATION_FILTER_ORDER: readonly V2OrganizationFilter[] = [
  "open_cases",
  "cases",
  "interests",
  "quotes",
  "contacts",
  "people",
];

/**
 * The commercial side the institution list shows, `customers` first because the list exists
 * to find who asks OrigenLab for equipment. `null` is every organization.
 *
 * A supplier or manufacturer named on a case is not a customer and is never listed as one;
 * it has its own segment. An organization recorded on both sides appears in both.
 */
export const ORGANIZATION_SEGMENT_ORDER: readonly (V2OrganizationSegment | null)[] = [
  "customers",
  "suppliers",
  "others",
  null,
];

export const DEFAULT_ORGANIZATION_SEGMENT: V2OrganizationSegment = "customers";

export function organizationSegmentLabel(segment: V2OrganizationSegment | null): string {
  switch (segment) {
    case "customers":
      return "Clientes / instituciones solicitantes";
    case "suppliers":
      return "Proveedores y fabricantes";
    case "others":
      return "Otras instituciones participantes";
    default:
      return "Todas";
  }
}

export function organizationSegmentHint(segment: V2OrganizationSegment | null): string {
  switch (segment) {
    case "customers":
      return "Piden equipos a OrigenLab en al menos un caso, o están registradas como clientes.";
    case "suppliers":
      return "Proveedoras o fabricantes en algún caso, o registradas como tales. No son clientes por aparecer en un caso.";
    case "others":
      return "Usuarias finales, agentes de compra, financiadoras o mencionadas en un caso.";
    default:
      return "Todas las instituciones registradas, tengan o no un papel en algún caso.";
  }
}

export interface OrganizationRoleCount {
  key: string;
  label: string;
  count: number;
}

/**
 * What an institution is on cases, one count per part, in the order the list card reads
 * them. The five the operator asked to see always appear (a zero is a measured zero); the
 * two rarer buyer-side parts only when they are held.
 */
export function organizationRoleCounts(row: V2Organization): OrganizationRoleCount[] {
  const counts: OrganizationRoleCount[] = [
    { key: "requesting", label: "Pide", count: row.cases_as_requesting_institution },
    { key: "supplier", label: "Proveedor", count: row.cases_as_supplier },
    { key: "manufacturer", label: "Fabricante", count: row.cases_as_manufacturer },
    { key: "funder", label: "Financia", count: row.cases_as_funder },
    { key: "mentioned", label: "Mencionada", count: row.cases_as_mentioned },
  ];
  if (row.cases_as_end_user_institution > 0) {
    counts.splice(1, 0, {
      key: "end_user",
      label: "Usa el equipo",
      count: row.cases_as_end_user_institution,
    });
  }
  if (row.cases_as_purchasing_agent > 0) {
    counts.splice(1, 0, {
      key: "purchasing_agent",
      label: "Compra por otra",
      count: row.cases_as_purchasing_agent,
    });
  }
  return counts;
}

/** The windows offered for "actividad reciente", in days. */
export const RECENT_ACTIVITY_WINDOWS: readonly number[] = [30, 90, 365];

const ORGANIZATION_KIND_LABELS: Record<string, string> = {
  unknown: "Sin clasificar",
  institution: "Institución",
  university: "Universidad",
  company: "Empresa",
  government: "Organismo público",
  hospital: "Hospital",
  laboratory: "Laboratorio",
};

/** `kind` is an open vocabulary; an unknown value is shown as written, never guessed at. */
export function organizationKindLabel(kind: string): string {
  return ORGANIZATION_KIND_LABELS[kind] ?? kind;
}

// ------------------------------------------------------------------------ cases

/** A person's part on a case (`crm.opportunity_participant.role`), not an institution's. */
export const PARTICIPANT_ROLE_LABELS: Record<string, string> = {
  end_user: "Usuario final",
  technical: "Contacto técnico",
  purchasing: "Compras",
  finance: "Finanzas",
  approver: "Aprueba",
  quote_recipient: "Recibe la cotización",
  signatory: "Firma",
  other: "Otro papel",
};

export function participantRoleLabel(role: string): string {
  return PARTICIPANT_ROLE_LABELS[role] ?? role;
}

/**
 * An institution's part on a case, from a connected-case row whose roles are strings.
 *
 * The organization card sends `crm.opportunity_organization` roles; anything this build
 * does not know is shown as written rather than mapped onto a part it might not be.
 */
export function organizationRoleLabel(role: string): string {
  return (CASE_ROLE_ORDER as readonly string[]).includes(role)
    ? caseRoleLabel(role as V2CaseOrganizationRole)
    : role;
}

export interface CaseParticipantGroup {
  role: V2CaseOrganizationRole;
  label: string;
  organizations: V2CaseParticipant[];
}

/**
 * A case's current parts grouped by exact role, in `CASE_ROLE_ORDER`.
 *
 * `mentioned` stays its own group at the end — it is the only part a machine may propose,
 * and folding it into "otras" would make a named supplier and a passing mention look alike.
 */
export function groupParticipants(
  participants: readonly V2CaseParticipant[],
): CaseParticipantGroup[] {
  return CASE_ROLE_ORDER.map((role) => ({
    role,
    label: caseRoleLabel(role),
    organizations: participants.filter((row) => row.role === role),
  })).filter((group) => group.organizations.length > 0);
}

export const QUOTE_STATUS_LABELS: Record<string, string> = {
  draft: "Borrador",
  in_review: "En revisión",
  approved: "Aprobada",
  sent: "Enviada",
  void: "Anulada",
};

export function quoteStatusLabel(status: string | null): string {
  if (!status) {
    return "Sin revisión todavía";
  }
  return QUOTE_STATUS_LABELS[status] ?? status;
}

export const QUOTE_STATE_FILTER_LABELS: Record<V2CaseQuoteState, string> = {
  none: "Sin cotización",
  any: "Con cotización",
  draft: "Última revisión en borrador",
  in_review: "Última revisión en revisión",
  approved: "Última revisión aprobada",
  sent: "Última revisión enviada",
  void: "Última revisión anulada",
};

/** "3 · 1 abierta" style quote line for a case list row, or null when not carried. */
export function caseQuoteLine(
  quoteCount: number | null,
  latestStatus: string | null,
): string | null {
  if (quoteCount === null) {
    return null;
  }
  if (quoteCount === 0) {
    return "Sin cotización registrada todavía";
  }
  const noun = quoteCount === 1 ? "1 cotización" : `${quoteCount} cotizaciones`;
  return latestStatus ? `${noun} · última: ${quoteStatusLabel(latestStatus)}` : noun;
}

export function connectedQuoteLine(quote: V2ConnectedQuote): string {
  const parts = [quote.quote_number ?? "Sin número"];
  if (quote.latest_revision_no !== null) {
    parts.push(`rev. ${quote.latest_revision_no}`);
  }
  parts.push(quoteStatusLabel(quote.latest_status));
  if (quote.grand_total !== null && quote.quote_currency) {
    parts.push(`${quote.grand_total.toLocaleString("es-CL")} ${quote.quote_currency}`);
  }
  return parts.join(" · ");
}

/** What an interest names, most specific first. Never empty for a row the CHECK accepted. */
export function connectedInterestHeadline(interest: V2ConnectedInterest): string {
  const parts: string[] = [];
  if (interest.product_name) {
    parts.push(interest.product_name);
  }
  if (interest.model_text && interest.model_text !== interest.product_name) {
    parts.push(interest.model_text);
  }
  if (interest.manufacturer_organization_name) {
    parts.push(interest.manufacturer_organization_name);
  }
  if (parts.length === 0 && interest.description) {
    parts.push(interest.description);
  }
  return parts.length > 0 ? parts.join(" · ") : "Interés sin asunto legible";
}

export const ACTIVITY_KIND_LABELS: Record<string, string> = {
  call: "Llamada",
  meeting: "Reunión",
  note: "Nota",
  email: "Correo",
};

export function activityKindLabel(kind: string): string {
  return ACTIVITY_KIND_LABELS[kind] ?? kind;
}

/** The stage presets for the case list. `prospectos` is two stages, not an entity. */
export const CASE_STAGE_PRESETS: readonly { id: string; label: string; stages: string[] }[] = [
  { id: "all", label: "Todas las etapas", stages: [] },
  { id: "prospects", label: "Prospectos (contacto inicial + calificando)", stages: ["lead", "qualifying"] },
  { id: "lead", label: "Contacto inicial", stages: ["lead"] },
  { id: "qualifying", label: "Calificando", stages: ["qualifying"] },
  { id: "qualified", label: "Calificado", stages: ["qualified"] },
  { id: "quoting", label: "Cotizando", stages: ["quoting"] },
  { id: "negotiating", label: "Negociando", stages: ["negotiating"] },
  { id: "won", label: "Ganado", stages: ["won"] },
  { id: "lost", label: "Perdido", stages: ["lost"] },
  { id: "abandoned", label: "Abandonado", stages: ["abandoned"] },
];

/**
 * Fill a case row's quote and activity facts from a card that reached the same cases.
 *
 * `GET /v2/organizations/{id}/cases` does not carry them, but the organization and contact
 * cards do. They are copied only when the card's list is **complete** (its length equals its
 * uncapped count): from a capped list, "no quote on this case" could be a quote that simply
 * fell past the cap, and the row would then claim an absence it never measured.
 */
export function withCardFacts<T extends V2CommercialCase>(
  row: T,
  card: Pick<V2CardConnections, "quotes" | "activities" | "connection_summary">,
): T {
  const next = { ...row };
  if (next.quote_count === null && card.quotes.length === card.connection_summary.quotes) {
    const mine = card.quotes.filter((quote) => quote.opportunity_id === row.opportunity_id);
    const latest = [...mine].sort((a, b) =>
      (b.updated_at ?? "").localeCompare(a.updated_at ?? ""),
    )[0];
    next.quote_count = mine.length;
    next.latest_quote_status = latest?.latest_status ?? null;
  }
  if (
    next.last_activity_at === null &&
    card.activities.length === card.connection_summary.activities
  ) {
    const times = card.activities
      .filter((item) => item.opportunity_id === row.opportunity_id && item.occurred_at)
      .map((item) => item.occurred_at as string)
      .sort();
    next.last_activity_at = times.length > 0 ? times[times.length - 1]! : null;
  }
  return next;
}
