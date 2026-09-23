/**
 * How a commercial case reads on screen: its words, and the two questions a card asks of it.
 *
 * Pure. No fetch, no client, no URL, no React. Everything here is a function of a response
 * the read boundary already returned, so the whole vocabulary of a case can be unit-tested
 * against fixtures without a database, a network or a browser.
 *
 * **It holds no rule the database holds.** The stage machine is served by the API from
 * `origenlab_api.v2.case_commands.STAGE_TRANSITIONS` — the same table
 * `crm.opportunity_stage_transition_allowed` enforces — and this module reads that response
 * rather than restating it. A third copy in a browser is a third thing that can disagree,
 * and it would be the copy nobody re-reads.
 */

import type {
  V2CaseEvidence,
  V2CaseEvidenceRelation,
  V2CaseInterest,
  V2CaseOrganization,
  V2CaseOrganizationRole,
  V2CaseStage,
  V2CommercialCaseCard,
} from "../api/v2Types";

export const CASE_STAGE_LABELS: Record<V2CaseStage, string> = {
  lead: "Contacto inicial",
  qualifying: "Calificando",
  qualified: "Calificado",
  quoting: "Cotizando",
  negotiating: "Negociando",
  won: "Ganado",
  lost: "Perdido",
  abandoned: "Abandonado",
};

export function caseStageLabel(stage: V2CaseStage): string {
  return CASE_STAGE_LABELS[stage] ?? stage;
}

export const CASE_ROLE_LABELS: Record<V2CaseOrganizationRole, string> = {
  requesting_institution: "Institución que pide",
  end_user_institution: "Institución que usará el equipo",
  purchasing_agent: "Agente de compras",
  funder: "Financia",
  supplier: "Proveedor",
  manufacturer: "Fabricante",
  mentioned: "Mencionada",
};

/**
 * The parts in the order a screen reads them, mirroring the API's `CASE_ROLE_ORDER`.
 *
 * Who asks first, who will use it second, the rest after, and `mentioned` last — the one
 * part a machine may propose sits at the end rather than in the middle of the human
 * decisions. Alphabetical order would put it third.
 */
export const CASE_ROLE_ORDER: readonly V2CaseOrganizationRole[] = [
  "requesting_institution",
  "end_user_institution",
  "purchasing_agent",
  "funder",
  "supplier",
  "manufacturer",
  "mentioned",
];

export function caseRoleLabel(role: V2CaseOrganizationRole): string {
  return CASE_ROLE_LABELS[role] ?? role;
}

export const CASE_RELATION_LABELS: Record<V2CaseEvidenceRelation, string> = {
  origin: "Origen del caso",
  supports_requesting_institution: "Respalda quién pide",
  supports_interest: "Respalda qué busca",
  supports_participant: "Respalda a un participante",
  mentions: "Lo menciona",
  contradicts: "Lo contradice",
};

export function caseRelationLabel(relation: V2CaseEvidenceRelation): string {
  return CASE_RELATION_LABELS[relation] ?? relation;
}

export const CASE_SUBJECT_LABELS: Record<V2CaseEvidence["subject_kind"], string> = {
  source_record: "Registro de evidencia",
  assertion: "Afirmación",
  message: "Mensaje",
  notice: "Aviso de compra pública",
};

/** Whether a case is closed. `closed_at` and the terminal stages are one fact, by CHECK. */
export function isCaseClosed(card: { closed_at: string | null }): boolean {
  return card.closed_at !== null;
}

/** The parts an institution currently holds. A closed row is history and is not current. */
export function currentOrganizations(
  organizations: readonly V2CaseOrganization[],
): V2CaseOrganization[] {
  return organizations.filter((row) => row.is_current);
}

/** The parts that have been closed — the audit trail a card must not hide. */
export function historicalOrganizations(
  organizations: readonly V2CaseOrganization[],
): V2CaseOrganization[] {
  return organizations.filter((row) => !row.is_current);
}

/**
 * The institution that is asking, or null.
 *
 * Null is an answer and not a gap: §3.6.5 says a case from an inbound message routinely
 * does not know who is asking, and a card that filled the blank with the first institution
 * it found would be inventing the one fact the whole table exists to record deliberately.
 */
export function requestingInstitution(
  organizations: readonly V2CaseOrganization[],
): V2CaseOrganization | null {
  return (
    currentOrganizations(organizations).find(
      (row) => row.role === "requesting_institution",
    ) ?? null
  );
}

export function openInterests(interests: readonly V2CaseInterest[]): V2CaseInterest[] {
  return interests.filter((row) => row.withdrawn_at === null);
}

export function linkedEvidence(evidence: readonly V2CaseEvidence[]): V2CaseEvidence[] {
  return evidence.filter((row) => row.unlinked_at === null);
}

/**
 * The evidence a case has collected *against* itself.
 *
 * Pulled out by name because it is the reading most worth seeing and the easiest to lose in
 * a list: a case that has recorded what contradicts it is a case that can be closed
 * honestly rather than quietly.
 */
export function contradictingEvidence(
  evidence: readonly V2CaseEvidence[],
): V2CaseEvidence[] {
  return linkedEvidence(evidence).filter((row) => row.relation === "contradicts");
}

/**
 * What an interest is seeking, in words, from whichever of its three subjects exist.
 *
 * At least one of product, manufacturer and model text is present by CHECK, so this never
 * returns an empty string for a row the database accepted.
 */
export function interestHeadline(interest: V2CaseInterest): string {
  const parts: string[] = [];
  if (interest.product_name) {
    parts.push(interest.product_name);
  }
  if (interest.model_text) {
    parts.push(interest.model_text);
  }
  if (interest.manufacturer_organization_name) {
    parts.push(interest.manufacturer_organization_name);
  }
  if (parts.length === 0) {
    // Only reachable when the catalogue product or manufacturer was named by id and the
    // join found no name — a broken row, said as such rather than rendered as blank.
    return "Interés sin asunto legible";
  }
  return parts.join(" · ");
}

/** The quantity as an operator reads it, or null when none was recorded. */
export function interestQuantity(interest: V2CaseInterest): string | null {
  if (interest.quantity === null) {
    return null;
  }
  const amount = String(interest.quantity);
  return interest.quantity_unit ? `${amount} ${interest.quantity_unit}` : amount;
}

/**
 * What the case cannot yet say, named one by one.
 *
 * These are **not** blockers on a command: they are gaps in the case itself, and they are
 * what makes the screen worth opening. A case with no institution and no interest is a
 * legitimate case at `lead`, and this is the list that says so out loud rather than leaving
 * three empty sections to be read as an error.
 */
export function caseGaps(card: V2CommercialCaseCard): string[] {
  const gaps: string[] = [];
  if (!requestingInstitution(card.organizations)) {
    gaps.push(
      "Nadie ha dicho quién pide. Es un estado legítimo hasta «Calificado»: desde ahí el " +
        "caso no puede avanzar sin institución solicitante.",
    );
  }
  if (openInterests(card.interests).length === 0) {
    gaps.push("El caso no registra todavía qué busca.");
  }
  if (linkedEvidence(card.evidence).length === 0) {
    gaps.push(
      "El caso no tiene evidencia vinculada. Un caso abierto por un comando siempre tiene " +
        "al menos su documento de origen, así que esto indica que se desvinculó.",
    );
  }
  const contradictions = contradictingEvidence(card.evidence);
  if (contradictions.length > 0) {
    gaps.push(
      `Hay ${contradictions.length} documento(s) vinculados como «lo contradice»: léelos ` +
        "antes de avanzar la etapa.",
    );
  }
  return gaps;
}

/**
 * What reaching `stage` would still need, given what the case and the operator have so far.
 *
 * Both lists come from the API's `stage_machine`, so this is a lookup and not a second
 * statement of the rule. It reports a requirement; it never says a move should happen.
 *
 * Only **unmet** requirements are returned. A satisfied one is not a warning to be shown
 * beside an action — it is silence — so a caller can push the whole list straight into its
 * blockers without having to re-check each entry. `closeReason` is what the operator has
 * typed so far, blank when they have typed nothing.
 */
export function stageRequirements(
  card: V2CommercialCaseCard,
  stage: V2CaseStage,
  closeReason = "",
): string[] {
  const machine = card.stage_machine;
  const requirements: string[] = [];
  if (
    machine.stages_requiring_a_requesting_institution.includes(stage) &&
    !requestingInstitution(card.organizations)
  ) {
    requirements.push(
      `«${caseStageLabel(stage)}» exige una institución solicitante, y el caso no tiene una.`,
    );
  }
  if (machine.stages_requiring_a_close_reason.includes(stage) && !closeReason.trim()) {
    requirements.push(
      `«${caseStageLabel(stage)}» cierra el caso y exige un motivo: escríbelo.`,
    );
  }
  return requirements;
}

// --------------------------------------------------------------- the summary

/**
 * The six things an operator reads about a case before deciding whether to open it.
 *
 * Deliberately a *reading*, not a projection of every column: a case screen that reprints
 * the schema teaches the schema, and the operator was asking who is asking, for what, from
 * whom, and what happened last.
 *
 * Each field is nullable and each null is a state with a name, never a blank: "nobody has
 * said who is asking" is the single most important thing this model records on purpose.
 */
export interface CaseSummary {
  title: string;
  stage: V2CaseStage;
  stageLabel: string;
  closed: boolean;
  /** Null means nobody has said who is asking — legitimate up to «Calificado». */
  requesting: V2CaseOrganization | null;
  /** What the case is seeking, in words. Empty when nothing is recorded yet. */
  interests: string[];
  /** Current supplier parts, by name. */
  suppliers: string[];
  /** Current manufacturer parts plus the manufacturers named on open interests. */
  manufacturers: string[];
  /** The most recent thing that happened to the case, or null when only its creation has. */
  lastActivityAt: string | null;
  lastActivityLabel: string | null;
}

function namesForRole(
  organizations: readonly V2CaseOrganization[],
  role: V2CaseOrganizationRole,
): string[] {
  return currentOrganizations(organizations)
    .filter((row) => row.role === role)
    .map((row) => row.name);
}

function latest(values: readonly (string | null)[]): string | null {
  let best: string | null = null;
  let bestMs = Number.NEGATIVE_INFINITY;
  for (const value of values) {
    if (!value) {
      continue;
    }
    const ms = new Date(value).getTime();
    if (!Number.isNaN(ms) && ms > bestMs) {
      bestMs = ms;
      best = value;
    }
  }
  return best;
}

export function caseSummary(card: V2CommercialCaseCard): CaseSummary {
  const open = openInterests(card.interests);
  const linked = linkedEvidence(card.evidence);
  const lastLink = latest(linked.map((row) => row.linked_at));
  // `updated_at` moves on every durable write, so it is the floor for "something happened".
  // The evidence link is preferred when it is newer because it is the one an operator can
  // actually read; a bare timestamp with no document behind it says nothing.
  const lastActivityAt = latest([lastLink, card.updated_at]);
  return {
    title: card.title,
    stage: card.stage,
    stageLabel: caseStageLabel(card.stage),
    closed: isCaseClosed(card),
    requesting: requestingInstitution(card.organizations),
    interests: open.map(interestHeadline),
    suppliers: namesForRole(card.organizations, "supplier"),
    manufacturers: [
      ...namesForRole(card.organizations, "manufacturer"),
      ...open
        .map((row) => row.manufacturer_organization_name)
        .filter((name): name is string => name !== null),
    ].filter((name, index, all) => all.indexOf(name) === index),
    lastActivityAt,
    lastActivityLabel:
      lastLink && lastLink === lastActivityAt
        ? "documento vinculado"
        : lastActivityAt
          ? "última escritura del caso"
          : null,
  };
}
