/**
 * Institution-intel adapter — wired to backend W1
 * (`apps/api/docs/INSTITUTION_PROSPECT_API_CONTRACT.md`, PR #475).
 *
 * This is the ONLY file components should ever import data-fetching functions
 * from for institution/prospect/licitación-intel data. Components consume the
 * clean camelCase types in `./types.ts`; this file is the seam that parses
 * W1's raw (loosely-typed, snake_case) JSON into that shape.
 *
 * `getLicitacionIntel` stays mock-backed: the contract's own "T1 / ANEXO
 * status" section states nothing annex/term-related is exposed by W1 yet.
 *
 * Do not import mockData.ts from outside this file (getLicitacionIntel only).
 */

import { OperatorApiError, operatorApiUrl } from "../operatorClient";
import type {
  Availed,
  ContactGapStatus,
  ContactOverlaySummary,
  EquipmentHistoryEntry,
  IdentityKind,
  InstitutionAxes,
  InstitutionIdentitySummary,
  InstitutionListItem,
  InstitutionProfile,
  InstitutionTenderRow,
  LicitacionIntel,
  Paged,
  ProcurementEligibilityStatus,
  ProcurementMeta,
  ProspectQueueRow,
  QueueName,
} from "./types";
import { MOCK_LICITACION_TALCA } from "./mockData";

async function fetchProcurementJson<T>(path: string, params?: Record<string, string | number>): Promise<T> {
  const url = operatorApiUrl(path, params);
  const res = await fetch(url, {
    method: "GET",
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new OperatorApiError(text || res.statusText || `HTTP ${res.status}`, res.status);
  }
  return res.json() as Promise<T>;
}

/** Route-facing short names (contract's `queue_name` enum) vs. the long form used in row.queue / QueueName. */
const QUEUE_ROUTE_SEGMENT: Record<QueueName, string> = {
  current_opportunity_queue: "current_opportunity",
  historical_prospect_queue: "historical_prospect",
  contact_gap_queue: "contact_gap",
  institution_match_review_queue: "institution_match_review",
  line_evidence_review_queue: "line_evidence_review",
  retender_review_queue: "retender_review",
};

/**
 * The four-state availability derivation from the contract: never inferred
 * from an empty `items` array alone.
 */
function metaToAvailed<T>(meta: ProcurementMeta, items: readonly T[]): Availed<readonly T[]> {
  if (meta.reduced_mode) {
    return { status: "not_available" };
  }
  if (items.length === 0) {
    return { status: "available_empty" };
  }
  if (meta.stale) {
    return {
      status: "available_incomplete",
      reasonCodes: meta.note ? [meta.note] : ["stale"],
      partial: items,
    };
  }
  return { status: "available", data: items };
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function asStringOrNull(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function asStringArray(value: unknown): readonly string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

const IDENTITY_KINDS: readonly IdentityKind[] = [
  "origenlab_account",
  "chilecompra_buyer_source_id",
  "normalized_buyer_name",
  "unresolved_buyer",
  "identity_review",
];
function asIdentityKind(value: unknown): IdentityKind {
  return typeof value === "string" && (IDENTITY_KINDS as readonly string[]).includes(value)
    ? (value as IdentityKind)
    : "unresolved_buyer";
}

const ELIGIBILITY_STATUSES: readonly ProcurementEligibilityStatus[] = [
  "open_public",
  "restricted_invitation_unconfirmed",
  "unknown",
];
function asEligibility(value: unknown): ProcurementEligibilityStatus {
  return typeof value === "string" && (ELIGIBILITY_STATUSES as readonly string[]).includes(value)
    ? (value as ProcurementEligibilityStatus)
    : "unknown";
}

const CONTACT_GAP_STATUSES: readonly ContactGapStatus[] = [
  "existing_verified_contact",
  "existing_contact_needs_role_review",
  "role_known_email_missing",
  "contacts_present_but_blocked",
  "linked_account_no_contact",
  "account_unlinked",
  "account_ambiguous",
];
function asContactGapStatus(value: unknown): ContactGapStatus {
  return typeof value === "string" && (CONTACT_GAP_STATUSES as readonly string[]).includes(value)
    ? (value as ContactGapStatus)
    : "account_unlinked";
}

function mapIdentity(raw: Record<string, unknown>): InstitutionIdentitySummary {
  return {
    institutionId: asString(raw.institution_id),
    displayName: asString(raw.display_name, "—"),
    identityKind: asIdentityKind(raw.identity_kind),
    linkedAccountPresent: raw.linked_account_present === true,
    identityReviewRequired: raw.identity_review_required === true,
  };
}

function mapAxisScore(raw: unknown): { band: InstitutionAxes["prospectStrength"]["band"]; score: number; reasonCodes: readonly string[] } {
  const r = (raw ?? {}) as Record<string, unknown>;
  const band = asString(r.band, "none");
  const validBand = band === "low" || band === "medium" || band === "high" ? band : "none";
  return { band: validBand, score: asNumber(r.score), reasonCodes: asStringArray(r.reason_codes) };
}

function mapAxes(raw: Record<string, unknown>): InstitutionAxes {
  return {
    prospectStrength: mapAxisScore(raw.prospect_strength),
    opportunityUrgency: mapAxisScore(raw.opportunity_urgency),
    contactReadiness: mapAxisScore(raw.contact_readiness),
  };
}

function mapContactOverlay(raw: Record<string, unknown>): ContactOverlaySummary {
  const overlay = (raw.account_contact_overlay ?? {}) as Record<string, unknown>;
  return {
    contactGapStatus: asContactGapStatus(overlay.contact_gap_status),
    knownContactCount: asNumber(overlay.known_contact_count),
    suitableContactCount: asNumber(overlay.suitable_contact_count),
    verifiedContactCount: asNumber(overlay.verified_contact_count),
    nextAction: asString(overlay.contact_next_action, "none"),
  };
}

function mapTenderRow(raw: Record<string, unknown>): InstitutionTenderRow {
  return {
    tenderCode: asString(raw.tender_code),
    categoryOrTitle: asString(raw.canonical_equipment_category, "—"),
    lifecycleClass: asString(raw.lifecycle_class),
    closeTimestamp: asStringOrNull(raw.close_timestamp),
    eligibilityStatus: asEligibility(raw.procurement_eligibility_status),
  };
}

function mapEquipmentHistoryEntry(raw: Record<string, unknown>): EquipmentHistoryEntry {
  const snippets = asStringArray(raw.line_description_snippets);
  const tenderCodes = asStringArray(raw.tender_codes);
  const demandRecurrence = asString(raw.demand_recurrence, "recurrence_not_established");
  const validRecurrence =
    demandRecurrence === "observed_once_confirmed" ||
    demandRecurrence === "repeated_observed_demand_confirmed" ||
    demandRecurrence === "repeated_confirmed_with_unresolved_relationships"
      ? demandRecurrence
      : "recurrence_not_established";
  return {
    category: asString(raw.canonical_equipment_category, "—"),
    distinctTenderCount: asNumber(raw.distinct_tender_count),
    firstObservedDate: asStringOrNull(raw.first_observed_date),
    mostRecentObservedDate: asStringOrNull(raw.most_recent_observed_date),
    demandRecurrence: validRecurrence,
    openCurrentTenderCount: asNumber(raw.open_current_tender_count),
    historicalTenderCount: asNumber(raw.historical_tender_count),
    // T1/anexo evidence isn't in W1's scope yet — no signal exists to ever set this true from real data.
    fromAnexoEvidence: false,
    // line_description_snippets carries no per-snippet document/locator provenance in W1; best-effort
    // pairing with tender_codes by index (same accumulation order) rather than a precise evidence link.
    snippets: snippets.map((excerpt, i) => ({
      excerpt,
      documentLabel: tenderCodes[i] ?? "licitación",
      locator: "",
    })),
  };
}

function mapInstitutionProfile(raw: Record<string, unknown>, meta: ProcurementMeta): InstitutionProfile {
  const equipmentHistoryRaw = Array.isArray(raw.equipment_history) ? raw.equipment_history : [];
  const currentOpportunitiesRaw = Array.isArray(raw.current_opportunities) ? raw.current_opportunities : [];
  const historicalSignalsRaw = Array.isArray(raw.historical_signals) ? raw.historical_signals : [];

  const currentOpportunities = currentOpportunitiesRaw.map((r) => mapTenderRow(r as Record<string, unknown>));
  const historicalSignals = historicalSignalsRaw.map((r) => mapTenderRow(r as Record<string, unknown>));

  const queueMembership: QueueName[] = [];
  if (currentOpportunities.length > 0) queueMembership.push("current_opportunity_queue");
  if (historicalSignals.length > 0) queueMembership.push("historical_prospect_queue");
  const overlay = mapContactOverlay(raw);
  if (overlay.contactGapStatus !== "existing_verified_contact") {
    queueMembership.push("contact_gap_queue");
  }
  const identity = mapIdentity(raw.identity as Record<string, unknown>);
  if (identity.identityReviewRequired) {
    queueMembership.push("institution_match_review_queue");
  }

  return {
    identity,
    axes: mapAxes(raw.axes as Record<string, unknown>),
    equipmentHistory: metaToAvailed(meta, equipmentHistoryRaw.map((r) => mapEquipmentHistoryEntry(r as Record<string, unknown>))),
    currentOpportunities,
    historicalSignals,
    contactOverlay: overlay,
    queueMembership,
    // Cross-institution category-demand counts aren't part of a single institution response
    // (W1's `/status` gives run-level counts, not per-category-per-institution) — no signal to
    // populate this from yet.
    marketSignal: { status: "not_available" },
    // No canonical institution<->contact join exists in the planner yet (contract §5) — never
    // populated from real data; stays mock-only for local preview purposes.
    mockOnlyContacts: undefined,
  };
}

function mapInstitutionListItem(raw: Record<string, unknown>): InstitutionListItem {
  const equipmentHistoryRaw = Array.isArray(raw.equipment_history) ? raw.equipment_history : [];
  return {
    identity: mapIdentity(raw.identity as Record<string, unknown>),
    axes: mapAxes(raw.axes as Record<string, unknown>),
    categories: equipmentHistoryRaw.map((r) => asString((r as Record<string, unknown>).canonical_equipment_category)),
    queueMembership: [],
  };
}

function mapQueueRow(raw: Record<string, unknown>): ProspectQueueRow | null {
  const queue = asString(raw.queue) as QueueName;
  const rowId = asString(raw.queue_row_id, `${queue}-${asString(raw.institution_id)}-${asString(raw.tender_code)}`);
  const institutionId = asString(raw.institution_id);
  const institutionDisplayName = asString(raw.display_name, institutionId || "—");

  switch (queue) {
    case "current_opportunity_queue":
      return {
        queue,
        rowId,
        institutionId,
        institutionDisplayName,
        tenderCode: asString(raw.tender_code),
        categoryOrTitle: asString(raw.equipment_category, "—"),
        closeTimestamp: asStringOrNull(raw.close_timestamp),
        // Membership in this queue requires open_public eligibility (contract: queue-building rules).
        eligibilityStatus: "open_public",
        prospectStrengthBand: (asString(raw.prospect_strength_band, "none") as InstitutionAxes["prospectStrength"]["band"]) || "none",
      };
    case "historical_prospect_queue":
      return {
        queue,
        rowId,
        institutionId,
        institutionDisplayName,
        category: asString(raw.equipment_category, "—"),
        commercialSignalType: asString(raw.commercial_signal_type, "equipment_purchase_signal"),
        mostRecentObservedDate: asStringOrNull(raw.most_recent_observed_date ?? raw.close_timestamp),
        tenderCount: asNumber(raw.tender_count, 1),
      };
    case "contact_gap_queue":
      return {
        queue,
        rowId,
        institutionId,
        institutionDisplayName,
        contactGapStatus: asContactGapStatus(raw.contact_gap_status),
        prospectStrengthBand: (asString(raw.prospect_strength_band, "none") as InstitutionAxes["prospectStrength"]["band"]) || "none",
        opportunityUrgencyBand: (asString(raw.opportunity_urgency_band, "none") as InstitutionAxes["opportunityUrgency"]["band"]) || "none",
        queueEntryReason: asString(raw.queue_entry_reason, "relevant_historical_prospect"),
      };
    case "institution_match_review_queue":
      return {
        queue,
        rowId,
        institutionId,
        institutionDisplayName,
        conflictReason: asString(raw.conflict_reason, "Identidad requiere revisión manual."),
        candidateDisplayNames: asStringArray(raw.candidate_display_names ?? raw.aliases),
      };
    case "line_evidence_review_queue":
      return {
        queue,
        rowId,
        institutionId,
        institutionDisplayName,
        tenderCode: asString(raw.tender_code),
        clauseText: asString(raw.clause_text, "—"),
      };
    case "retender_review_queue":
      return {
        queue,
        rowId,
        institutionId,
        institutionDisplayName,
        tenderCodes: asStringArray(raw.tender_codes),
        resolutionReason: asString(raw.resolution_reason, "Posible reedición — no confirmado."),
      };
    default:
      return null;
  }
}

export interface QueueListParams {
  queue?: QueueName;
  page?: number;
  pageSize?: number;
  filters?: {
    institutionId?: string;
    tenderCode?: string;
    equipmentCategory?: string;
    commercialSignalType?: string;
    q?: string;
  };
}

export interface InstitutionListParams {
  page?: number;
  pageSize?: number;
  institutionId?: string;
  q?: string;
}

interface RawListResponse {
  meta: ProcurementMeta;
  limit: number;
  offset: number;
  total: number;
  count: number;
  items: unknown[];
}

function toOffset(page: number, pageSize: number): number {
  return Math.max(0, (page - 1) * pageSize);
}

export const institutionIntelAdapter = {
  async getProcurementStatus(): Promise<{
    meta: ProcurementMeta;
    operatorQueueSizes: Partial<Record<QueueName, number>>;
    summaryOk: boolean;
  }> {
    const raw = await fetchProcurementJson<{
      meta: ProcurementMeta;
      operator_queue_sizes?: Record<string, number>;
      summary_ok?: boolean;
    }>("/operator/procurement/status");
    return {
      meta: raw.meta,
      operatorQueueSizes: (raw.operator_queue_sizes ?? {}) as Partial<Record<QueueName, number>>,
      summaryOk: raw.summary_ok !== false,
    };
  },

  async listInstitutions(params: InstitutionListParams = {}): Promise<Paged<InstitutionListItem>> {
    const page = params.page ?? 1;
    const pageSize = params.pageSize ?? 50;
    const query: Record<string, string | number> = { limit: pageSize, offset: toOffset(page, pageSize) };
    if (params.institutionId) query.institution_id = params.institutionId;
    if (params.q) query.q = params.q;

    const raw = await fetchProcurementJson<RawListResponse>("/operator/procurement/institutions", query);
    return {
      items: raw.items.map((r) => mapInstitutionListItem(r as Record<string, unknown>)),
      pageInfo: { page, pageSize, totalItems: raw.total },
    };
  },

  async getInstitutionProfile(institutionId: string): Promise<InstitutionProfile | null> {
    try {
      const raw = await fetchProcurementJson<{ meta: ProcurementMeta; item: Record<string, unknown> | null }>(
        `/operator/procurement/institutions/${encodeURIComponent(institutionId)}`,
      );
      // A healthy feed with no match is a real 404 (caught below). A degraded/reduced-mode
      // feed returns 200 with item=null instead — both collapse to "not found" here since
      // InstitutionProfile has no reduced-mode wrapper of its own to distinguish them further.
      if (!raw.item) return null;
      return mapInstitutionProfile(raw.item, raw.meta);
    } catch (e) {
      if (e instanceof OperatorApiError && e.status === 404) {
        return null;
      }
      throw e;
    }
  },

  async getInstitutionListItem(institutionId: string): Promise<InstitutionListItem | null> {
    const profile = await institutionIntelAdapter.getInstitutionProfile(institutionId);
    if (!profile) return null;
    return {
      identity: profile.identity,
      axes: profile.axes,
      categories: [...profile.currentOpportunities, ...profile.historicalSignals].map((t) => t.categoryOrTitle),
      queueMembership: profile.queueMembership,
    };
  },

  /** W1 doesn't cover tender-level commercial terms / anexo recognition — stays mock-backed. */
  async getLicitacionIntel(tenderCode: string): Promise<LicitacionIntel | null> {
    await new Promise((resolve) => setTimeout(resolve, 80));
    return tenderCode === MOCK_LICITACION_TALCA.tenderCode ? MOCK_LICITACION_TALCA : null;
  },

  async listQueueRows(params: QueueListParams = {}): Promise<Paged<ProspectQueueRow>> {
    const page = params.page ?? 1;
    const pageSize = params.pageSize ?? 20;
    const queue = params.queue ?? "current_opportunity_queue";
    const routeSegment = QUEUE_ROUTE_SEGMENT[queue];

    const query: Record<string, string | number> = { limit: pageSize, offset: toOffset(page, pageSize) };
    if (params.filters?.institutionId) query.institution_id = params.filters.institutionId;
    if (params.filters?.tenderCode) query.tender_code = params.filters.tenderCode;
    if (params.filters?.equipmentCategory) query.equipment_category = params.filters.equipmentCategory;
    if (params.filters?.commercialSignalType) query.commercial_signal_type = params.filters.commercialSignalType;
    if (params.filters?.q) query.q = params.filters.q;

    const raw = await fetchProcurementJson<RawListResponse>(
      `/operator/procurement/queues/${routeSegment}`,
      query,
    );
    const items = raw.items
      .map((r) => mapQueueRow(r as Record<string, unknown>))
      .filter((r): r is ProspectQueueRow => r !== null);

    return {
      items,
      pageInfo: { page, pageSize, totalItems: raw.total },
    };
  },
};

export type InstitutionIntelAdapter = typeof institutionIntelAdapter;
