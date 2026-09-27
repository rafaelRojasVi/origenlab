/**
 * How a contact and an institution read as a *operator* surface rather than as a dump of
 * the durable core.
 *
 * Pure. No fetch, no client, no URL, no React — everything is a function of a response the
 * read boundary already returned, so each of these decisions is testable without a
 * database, a network or a browser.
 *
 * Three rules shape every function here, and they are the reason this module exists at all
 * instead of the components formatting their own rows:
 *
 * 1. **A channel is not a person.** An address with no recorded owner is rendered as a
 *    pending channel, with the reason attached. Deriving a name from a local part would be
 *    an identity inference presented as a fact.
 * 2. **Receiving mail is not consent.** `outbound.contact_control` records blocks,
 *    cooldowns and prior contact — never permission — so the marketing line says what is
 *    recorded and never upgrades silence into an opt-in.
 * 3. **A relation is only as strong as the row behind it.** Cases and quotes are attached
 *    to a contact *through its institution* and through the case's own id, never by
 *    matching names, and the wording says which link was used.
 */

import type {
  V2AddressControl,
  V2CaseOrganizationRole,
  V2CardEvidence,
  V2CommercialCase,
  V2ContactCard,
  V2ContactUsage,
  V2OrganizationCard,
  V2OrganizationCase,
  V2OrganizationCardChannel,
  V2Quote,
} from "../api/v2Types";
import { noPersonExplanation, sourceKindLabel, usageLabel } from "./crmV2Browser";
import { CASE_ROLE_ORDER, caseRoleLabel } from "./commercialCase";

// ------------------------------------------------------------------ identity

export interface Crm360Identity {
  /** `pending_channel` is a state, not a missing person. */
  kind: "person" | "pending_channel";
  title: string;
  /** The one-line qualifier under the title: what kind of channel this is. */
  qualifier: string;
  /** Present only for a pending channel: why nobody is named. */
  note: string | null;
}

const CHANNEL_KIND_LABELS: Record<string, string> = {
  email: "Correo",
  phone: "Teléfono",
  mobile: "Teléfono móvil",
  fax: "Fax",
};

export function channelKindLabel(kind: string): string {
  return CHANNEL_KIND_LABELS[kind] ?? kind;
}

export function confirmationText(confirmation: string): string {
  return confirmation === "confirmed" ? "Confirmado por una persona" : "Propuesta de máquina";
}

/**
 * Who this card is about — a person, or an address whose owner is not established.
 *
 * The title of a pending channel is the address itself. Showing the address as the heading
 * and saying "canal pendiente" beside it is the honest reading; a heading that read like a
 * name would be the inference this whole model refuses to make.
 */
export function contactIdentity(card: V2ContactCard): Crm360Identity {
  const qualifier = `${channelKindLabel(card.channel_kind)} · ${usageLabel(
    card.usage,
  )} · ${confirmationText(card.confirmation)}`;
  if (card.person_display_name) {
    return { kind: "person", title: card.person_display_name, qualifier, note: null };
  }
  return {
    kind: "pending_channel",
    title: card.address,
    qualifier,
    note: noPersonExplanation(card.usage),
  };
}

// ------------------------------------------------------------------ channels

export interface Crm360Channel {
  contactPointId: string;
  address: string;
  channelKind: string;
  kindLabel: string;
  usageText: string;
  /** The channel this card was opened on. */
  isPrimary: boolean;
}

/**
 * Every way to reach this person: the card's own channel first, then its siblings.
 *
 * Siblings exist only when a person is recorded, which is exactly why a pending channel
 * shows one row and not a short list — there is no identity to gather the others under.
 */
export function contactChannels(card: V2ContactCard): Crm360Channel[] {
  const primary: Crm360Channel = {
    contactPointId: card.contact_point_id,
    address: card.address,
    channelKind: card.channel_kind,
    kindLabel: channelKindLabel(card.channel_kind),
    usageText: usageLabel(card.usage),
    isPrimary: true,
  };
  const siblings = card.sibling_contact_points.map((row) => ({
    contactPointId: row.contact_point_id,
    address: row.address,
    channelKind: row.channel_kind,
    kindLabel: channelKindLabel(row.channel_kind),
    usageText: usageLabel(row.usage),
    isPrimary: false,
  }));
  return [primary, ...siblings];
}

/** The channel kinds absent from a card, named so a blank is read as "none recorded". */
export function missingChannelKinds(channels: readonly Crm360Channel[]): string[] {
  const present = new Set(channels.map((row) => row.channelKind));
  return ["email", "phone"].filter((kind) => !present.has(kind)).map(channelKindLabel);
}

// ----------------------------------------------------------------- marketing

export interface Crm360Marketing {
  tone: "danger" | "warn" | "neutral";
  headline: string;
  /** Always says, in one sentence, what the state does *not* mean. */
  detail: string;
}

const CONTROL_SOURCE_LABELS: Record<string, string> = {
  wave1a_union: "Unión de seguridad Wave 1A",
  wave1a_rfc2047_addendum: "Adenda RFC2047 Wave 1A",
  wave1a_suppression: "Supresión Wave 1A",
  wave1a_investigation: "Investigación Wave 1A",
  send_accepted: "Envío aceptado",
  ndr_handler: "Rebote",
  complaint_handler: "Reclamo",
  unsubscribe_handler: "Baja solicitada",
  operator_command: "Decisión de un operador",
};

export function controlSourceLabel(source: string): string {
  return CONTROL_SOURCE_LABELS[source] ?? source;
}

const CONTROL_KIND_LABELS: Record<string, string> = {
  block: "Bloqueo",
  cooldown: "En espera",
  prior_contact: "Contacto previo",
};

/** `outbound.contact_control.kind` in words. None of them is a permission. */
export function controlKindLabel(kind: string): string {
  return CONTROL_KIND_LABELS[kind] ?? kind;
}

/**
 * What may be sent to this address, from the controls recorded against it.
 *
 * **There is no "permitido" branch and there must not be one.** `outbound.contact_control`
 * holds blocks, cooldowns and the fact of prior contact; it holds no permission, because
 * the permission model requires an explicit opt-in that nothing in the durable core records
 * yet. The neutral state is therefore "sin permiso registrado", never "se puede contactar".
 */
export function marketingStance(
  controls: readonly V2AddressControl[],
  now: Date = new Date(),
): Crm360Marketing {
  const block = controls.find((row) => row.control_kind === "block");
  if (block) {
    return {
      tone: "danger",
      headline: block.purpose === "all" ? "No contactar por ningún motivo" : "No contactar por marketing",
      detail: `${block.reason ?? "Sin motivo registrado"} · ${controlSourceLabel(block.source)}`,
    };
  }
  const cooldown = controls.find(
    (row) =>
      row.control_kind === "cooldown" &&
      row.until_at !== null &&
      new Date(row.until_at).getTime() > now.getTime(),
  );
  if (cooldown) {
    return {
      tone: "warn",
      headline: `En espera hasta ${formatDate(cooldown.until_at)}`,
      detail: `${cooldown.reason ?? "Sin motivo registrado"} · ${controlSourceLabel(cooldown.source)}`,
    };
  }
  const prior = controls.find((row) => row.control_kind === "prior_contact");
  if (prior) {
    return {
      tone: "neutral",
      headline: "Sin permiso registrado · hubo contacto previo",
      detail:
        "Un contacto previo es un hecho de correspondencia, no un consentimiento: no habilita una campaña.",
    };
  }
  return {
    tone: "neutral",
    headline: "Sin permiso registrado",
    detail:
      "Haber recibido o enviado un correo no es consentimiento. Nada en el núcleo durable registra todavía una aceptación explícita.",
  };
}

// ------------------------------------------------------------------ activity

export interface Crm360Activity {
  key: string;
  when: string | null;
  headline: string;
  detail: string | null;
  /** True while the document behind it is still unreviewed. */
  pending: boolean;
}

/**
 * The evidence trail read as activity, newest first.
 *
 * The same rows the technical panel shows as assertions, said as events: an operator asks
 * "when did we last hear from them", not "which assertion kinds resolved".
 */
export function activityFromEvidence(rows: readonly V2CardEvidence[]): Crm360Activity[] {
  return [...rows]
    .sort((a, b) => timestamp(b.observed_at) - timestamp(a.observed_at))
    .map((row) => ({
      key: row.assertion_id,
      when: row.observed_at,
      headline: sourceKindLabel(row.source_kind),
      detail: row.ambiguity_note ?? row.value_norm,
      pending: row.source_review_status !== "reviewed" || row.source_is_quarantined,
    }));
}

// ----------------------------------------------------------------- relations

/**
 * The parts an institution holds on one case, said in words, current apart from ended.
 *
 * The two are never merged. "Es proveedor" and "fue proveedor hasta marzo" are different
 * facts, and a chip row that flattened them would quietly present a closed row as today's
 * arrangement. `crm.opportunity_organization` never rewrites a part — it closes one row
 * and opens another — so both readings are always available and both are shown.
 */
export function caseRoleLabels(row: V2OrganizationCase): {
  current: string[];
  ended: string[];
} {
  const current: string[] = [];
  const ended: string[] = [];
  for (const part of row.roles) {
    (part.is_current ? current : ended).push(caseRoleLabel(part.role));
  }
  return { current, ended };
}

/**
 * The follow-up quotes belonging to a set of cases.
 *
 * Joined through `opportunity_id` only. `V2Quote` carries an organization *name* and no id,
 * and matching institutions by name is exactly the identity shortcut `docs/DOMAIN.md`
 * forbids, so a quote whose case is not in the list is not claimed here.
 */
export function quotesForCases(
  quotes: readonly V2Quote[],
  cases: readonly V2CommercialCase[],
): V2Quote[] {
  const ids = new Set(cases.map((row) => row.opportunity_id));
  return quotes.filter((row) => row.opportunity_id !== null && ids.has(row.opportunity_id));
}

/**
 * Whether a paged list on screen could be the whole answer.
 *
 * Cases now come from `GET /v2/organizations/{id}/cases`, which filters on the server and
 * pages; quotes are still one page of `/v2/quotes/followup` crossed in the browser. Either
 * can be a subset, and a short list that is really a first page must say so rather than be
 * read as "there are none".
 */
export function relationCoverageNote(loaded: number, total: number): string | null {
  if (total <= loaded) {
    return null;
  }
  return `Se revisaron ${loaded.toLocaleString("es-CL")} de ${total.toLocaleString(
    "es-CL",
  )}: puede haber más que no se alcanzaron a cruzar.`;
}

// ---------------------------------------------------------------- institution

export const ORGANIZATION_RELATIONSHIP_LABELS: Record<string, string> = {
  customer: "Cliente",
  supplier: "Proveedor",
  manufacturer: "Fabricante",
  prospect: "Prospecto",
  partner: "Socio",
  competitor: "Competidor",
};

export function organizationRelationshipLabel(role: string): string {
  return ORGANIZATION_RELATIONSHIP_LABELS[role] ?? role;
}

export interface Crm360CaseRoleTally {
  role: V2CaseOrganizationRole;
  label: string;
  /** How many cases the institution currently holds this part on. */
  caseCount: number;
}

export interface Crm360InstitutionRoles {
  /** Roles somebody recorded on `crm.organization_relationship`, currently valid. */
  recorded: string[];
  /**
   * The parts this institution currently holds across its cases, counted per part.
   *
   * All seven parts, not just `requesting_institution`. An institution that supplies or
   * manufactures in ten cases and asks in none is deeply involved, and the previous
   * reading — which counted only the cases it was asking in — showed it as involved in
   * nothing.
   */
  onCases: Crm360CaseRoleTally[];
  /** True when neither side says anything — shown as a state, not as an empty box. */
  empty: boolean;
}

/**
 * What an institution is to OrigenLab commercially — kept apart from what it *is*.
 *
 * The two readings never merge into one badge. A recorded supplier relationship and "asks
 * for quotes in three cases" are different facts from different tables, and an institution
 * can legitimately be both.
 */
export function institutionRoles(
  card: V2OrganizationCard,
  cases: readonly V2OrganizationCase[],
): Crm360InstitutionRoles {
  const recorded = card.relationships
    .filter((row) => row.valid_to === null)
    .map((row) => organizationRelationshipLabel(row.role));

  /*
    Counted per part over distinct cases, and only over current part rows. A closed row is
    what this institution *was* on that case; rolling it into the same number would make a
    relationship that ended read as one that continues.
  */
  const byRole = new Map<V2CaseOrganizationRole, Set<string>>();
  for (const row of cases) {
    for (const part of row.roles) {
      if (!part.is_current) {
        continue;
      }
      const seen = byRole.get(part.role) ?? new Set<string>();
      seen.add(row.opportunity_id);
      byRole.set(part.role, seen);
    }
  }
  const onCases = CASE_ROLE_ORDER.filter((role) => byRole.has(role)).map((role) => ({
    role,
    label: caseRoleLabel(role),
    caseCount: byRole.get(role)!.size,
  }));

  return {
    recorded,
    onCases,
    empty: recorded.length === 0 && onCases.length === 0,
  };
}

/**
 * An institution's channels, people first.
 *
 * A channel with a recorded owner and a role mailbox are different things to an operator
 * looking for somebody to write to, so they are two lists rather than one sorted one.
 */
export function splitInstitutionChannels(card: V2OrganizationCard): {
  named: V2OrganizationCardChannel[];
  pending: V2OrganizationCardChannel[];
} {
  return {
    named: card.contact_points.filter((row) => row.person_display_name !== null),
    pending: card.contact_points.filter((row) => row.person_display_name === null),
  };
}

// ------------------------------------------------------------------- shared

export function formatDate(value: string | null): string {
  if (!value) {
    return "—";
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString("es-CL");
}

function timestamp(value: string | null): number {
  if (!value) {
    return 0;
  }
  const parsed = new Date(value).getTime();
  return Number.isNaN(parsed) ? 0 : parsed;
}

/** Re-exported so a page never imports the browser console's module for one label. */
export { usageLabel, sourceKindLabel };
export type { V2ContactUsage };
