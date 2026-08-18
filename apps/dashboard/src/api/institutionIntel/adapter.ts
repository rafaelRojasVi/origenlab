/**
 * Institution-intel adapter — wired to backend W1
 * (`apps/api/docs/INSTITUTION_PROSPECT_API_CONTRACT.md`, PR #475).
 *
 * This is the ONLY file components should ever import data-fetching functions
 * from for institution/prospect/licitación-intel data. Components consume the
 * clean camelCase types in `./types.ts`; this file is the seam that parses
 * W1's raw (loosely-typed, snake_case) JSON into that shape.
 *
 * `getLicitacionIntel` calls the real
 * `GET /operator/procurement/tenders/{tender_code}` route (merged W1
 * actionability + T1 ANEXO term intelligence). mockData.ts is retained only
 * for Storybook/tests, never imported on the live fetch path.
 */

import { OperatorApiError, operatorApiUrl } from "../operatorClient";
import type {
  Availed,
  ContactGapStatus,
  ContactOverlaySummary,
  CurrentOpportunityRow,
  DeliveryDaysValue,
  EquipmentHistoryEntry,
  IdentityKind,
  InstitutionAxes,
  InstitutionIdentitySummary,
  InstitutionListItem,
  InstitutionProfile,
  InstitutionTenderRow,
  LicitacionIntel,
  Paged,
  PageInfo,
  ProcurementEligibilityStatus,
  ProcurementMeta,
  ProspectQueueRow,
  QueueName,
  TenderAnnexPreview,
  TermFact,
} from "./types";
import { formatTenderTermField } from "../../lib/institutionIntelLabels";

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
  const institutionDisplayName = asString(raw.display_name, institutionId);

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
        conflictReason: asString(raw.conflict_reason),
        candidateDisplayNames: asStringArray(raw.candidate_display_names ?? raw.aliases),
        reviewClusterId: asString(raw.institution_review_cluster_id),
        resolutionStatus: asString(raw.cluster_resolution_status),
        memberProfileIds: asStringArray(raw.member_profile_ids),
        reasonCodes: asStringArray(raw.cluster_reason_codes),
        confirmedAccount: raw.confirmed_account === true,
      };
    case "line_evidence_review_queue":
      return {
        queue,
        rowId,
        institutionId,
        institutionDisplayName,
        tenderCode: asString(raw.tender_code),
        clauseText: asString(raw.clause_text),
        relevanceClass: asString(raw.relevance_class),
        equipmentScopes: asStringArray(raw.equipment_scopes),
        canonicalEquipmentClasses: asStringArray(raw.canonical_equipment_classes),
        lineDisposition: asString(raw.line_disposition),
        reasonCodes: asStringArray(raw.line_reason_codes),
        ambiguityReasonCodes: asStringArray(raw.ambiguity_reason_codes),
      };
    case "retender_review_queue":
      return {
        queue,
        rowId,
        institutionId,
        institutionDisplayName,
        tenderCodes: asStringArray(raw.member_tender_codes ?? raw.tender_codes),
        resolutionReason: asString(raw.resolution_reason),
        buyerKey: asString(raw.buyer_key),
        recurrenceStatus: asString(raw.recurrence_status),
        resolutionStatus: asString(raw.family_resolution_status),
        reasonCodes: asStringArray(raw.family_reason_codes),
        unresolvedRelationshipCount: asString(raw.unresolved_relationship_count),
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

// ---------------------------------------------------------------------------
// Tender detail (GET /operator/procurement/tenders/{tender_code}) — merges
// W1 actionability (queue_row/queue_meta) with T1 term intelligence
// (tender_facts/items/coverage). See schemas/tender_terms.py for the exact
// server-side shape this mirrors.
// ---------------------------------------------------------------------------

interface RawTenderTermsMeta {
  reduced_mode: boolean;
  published: boolean;
  note: string;
  canonical_reason: string;
  as_of_utc: string;
}

interface RawTenderEvidence {
  locator_display?: string;
  evidence_excerpt?: string;
  safe_filename?: string;
  document_role?: string;
}

interface RawTenderFact {
  field_name: string;
  state: string;
  // T1's `value` is `Any` on the wire (schemas/tender_terms.py). Most facts
  // carry a plain string/number, but `maximum_delivery_days` carries a
  // structured `{days, day_basis}` object instead — see `DeliveryDaysValue`.
  // Explicitly typed as this union (never `any`/`unknown`) so a malformed
  // shape is caught by `normalizeFactValue` below rather than ever reaching
  // template-literal stringification (`[object Object]`) in the UI.
  value: string | number | null | DeliveryDaysValue;
  unit?: string | null;
  evidence?: RawTenderEvidence[];
  candidates?: { value: string | number; evidence?: RawTenderEvidence[] }[];
}

interface RawTenderItem {
  item_id: string;
  item_label: string | null;
  item_number: string | null;
  facts: RawTenderFact[];
}

interface RawTenderCoverage {
  attachments_discovered: number;
  attachments_downloaded: number;
  is_complete: boolean;
  incomplete_reason_codes: string[];
  unread_attachment_ids: string[];
}

interface RawTenderDetailResponse {
  tender_code: string;
  queue_meta: ProcurementMeta;
  found_in_queue: boolean;
  queue_row: Record<string, unknown> | null;
  t1_meta: RawTenderTermsMeta;
  t1_published: boolean;
  tender_facts: RawTenderFact[];
  items: RawTenderItem[];
  coverage: RawTenderCoverage | null;
}

// ---------------------------------------------------------------------------
// Operator annex-bundle upload PREVIEW
// (`POST /operator/procurement/tenders/{tender_code}/annex-bundle/preview`).
// tender_facts/items/coverage reuse the exact same RawTenderFact/RawTenderItem
// /RawTenderCoverage shapes above -- same mapTermFact/mapEvidence/item mapping
// this file already uses for published T1 detail. See
// apps/api/src/origenlab_api/schemas/tender_annex_preview.py for the
// server-side contract this mirrors.
// ---------------------------------------------------------------------------

interface RawTenderAnnexAcquisition {
  source: string;
  completeness_state: string;
  completeness_reason: string;
  operator_declared_complete: boolean;
}

interface RawTenderAnnexArchive {
  sha256: string;
  attachments_discovered: number;
  attachments_downloaded: number;
  rejected_entries: string[];
}

interface RawTenderAnnexBundlePreviewResponse {
  result: string;
  tender_code: string;
  acquisition: RawTenderAnnexAcquisition;
  archive: RawTenderAnnexArchive;
  bundle_complete: boolean;
  incomplete_reason_codes: string[];
  coverage: RawTenderCoverage | null;
  tender_facts: RawTenderFact[];
  items: RawTenderItem[];
  published: boolean;
  persisted: boolean;
  contact_authorization: boolean;
  outreach_authorization: boolean;
}

const TENDER_ANNEX_COMPLETENESS_STATES: readonly string[] = ["complete", "incomplete", "unknown"];

function asTenderAnnexCompletenessState(value: string): TenderAnnexPreview["acquisition"]["completenessState"] {
  return (
    TENDER_ANNEX_COMPLETENESS_STATES.includes(value) ? value : "unknown"
  ) as TenderAnnexPreview["acquisition"]["completenessState"];
}

function mapTenderAnnexPreview(raw: RawTenderAnnexBundlePreviewResponse): TenderAnnexPreview {
  const coverageComplete = raw.coverage?.is_complete === true;
  const partialCoverage = raw.coverage !== null && !coverageComplete;
  const incompleteReasonCodes = raw.coverage?.incomplete_reason_codes ?? [];

  const terms: LicitacionIntel["terms"] =
    raw.tender_facts.length === 0
      ? { status: "available_empty" }
      : partialCoverage
        ? {
            status: "available_incomplete",
            reasonCodes: incompleteReasonCodes,
            partial: raw.tender_facts.map(mapTermFact),
          }
        : { status: "available", data: raw.tender_facts.map(mapTermFact) };

  const itemBudgetRows = raw.items.map((item) => {
    const budgetFact = item.facts.find((f) => f.field_name === "item_budget" || f.field_name === "unit_price");
    return {
      itemLabel: item.item_label ?? item.item_number ?? item.item_id,
      quantity: mapItemQuantity(item),
      amount: mapItemAmount(item),
      evidence: mapEvidence(budgetFact?.evidence?.[0]),
    };
  });
  const itemBudget: LicitacionIntel["itemBudget"] =
    itemBudgetRows.length === 0
      ? { status: "available_empty" }
      : partialCoverage
        ? { status: "available_incomplete", reasonCodes: incompleteReasonCodes, partial: itemBudgetRows }
        : { status: "available", data: itemBudgetRows };

  const coverageData = raw.coverage
    ? {
        documentsDiscovered: raw.coverage.attachments_discovered,
        documentsRead: raw.coverage.attachments_downloaded,
        incompleteReasonCodes: raw.coverage.incomplete_reason_codes,
      }
    : null;
  const coverage: LicitacionIntel["coverage"] =
    raw.coverage === null || coverageData === null
      ? { status: "not_available" }
      : partialCoverage
        ? { status: "available_incomplete", reasonCodes: incompleteReasonCodes, partial: coverageData }
        : { status: "available", data: coverageData };

  const licitacionIntel: LicitacionIntel = {
    tenderCode: raw.tender_code,
    // No W1 queue row exists for a preview -- these two fields have no
    // source of truth here and are never fabricated. LicitacionIntelBody
    // itself renders neither field, so this has no visible effect; kept
    // honestly empty/unknown rather than omitted so the type stays exact.
    buyerDisplayName: "",
    eligibilityStatus: "unknown",
    procurementMethodRaw: null,
    terms,
    itemBudget,
    totalBudgetReconciled: false,
    // T1 preview never compares against a baseline summary categorization.
    recognitionDelta: { status: "not_available" },
    coverage,
  };

  return {
    tenderCode: raw.tender_code,
    acquisition: {
      source: raw.acquisition.source,
      completenessState: asTenderAnnexCompletenessState(raw.acquisition.completeness_state),
      completenessReason: raw.acquisition.completeness_reason,
      operatorDeclaredComplete: raw.acquisition.operator_declared_complete,
    },
    archive: {
      sha256: raw.archive.sha256,
      attachmentsDiscovered: raw.archive.attachments_discovered,
      attachmentsDownloaded: raw.archive.attachments_downloaded,
      rejectedEntries: raw.archive.rejected_entries,
    },
    bundleComplete: raw.bundle_complete,
    incompleteReasonCodes: raw.incomplete_reason_codes,
    licitacionIntel,
    published: false,
    persisted: false,
    contactAuthorization: false,
    outreachAuthorization: false,
  };
}

const TERM_FACT_STATES: readonly string[] = [
  "explicit",
  "derived",
  "not_explicitly_found",
  "unknown",
  "conflicting",
];

function asTermFactState(value: string): TermFact["state"] {
  return (TERM_FACT_STATES.includes(value) ? value : "unknown") as TermFact["state"];
}

function mapEvidence(raw: RawTenderEvidence | undefined) {
  if (!raw) return undefined;
  return {
    excerpt: raw.evidence_excerpt ?? "",
    documentLabel: raw.safe_filename ?? raw.document_role ?? "",
    locator: raw.locator_display ?? "",
  };
}

/**
 * `raw.value` is `Any` on the wire — even though `RawTenderFact.value`'s TS
 * type documents the two shapes we actually expect, that's a compile-time
 * claim only. Validate the structured `{days, day_basis}` shape defensively
 * at runtime here so any malformed/unexpected object (missing `days`,
 * non-numeric `days`, or any other shape entirely) fails closed to `null`
 * rather than ever reaching a template-literal stringification of an object.
 */
function normalizeFactValue(value: unknown): TermFact["value"] {
  if (value === null || typeof value === "string" || typeof value === "number") {
    return value;
  }
  if (typeof value === "object") {
    const v = value as Record<string, unknown>;
    if (typeof v.days === "number" && Number.isFinite(v.days) && (typeof v.day_basis === "string" || v.day_basis === null)) {
      return { days: v.days, day_basis: v.day_basis as string | null };
    }
  }
  return null;
}

function mapTermFact(raw: RawTenderFact): TermFact {
  return {
    fieldName: raw.field_name,
    label: formatTenderTermField(raw.field_name),
    state: asTermFactState(raw.state),
    value: normalizeFactValue(raw.value),
    unit: raw.unit ?? undefined,
    evidence: mapEvidence(raw.evidence?.[0]),
    candidates: (raw.candidates ?? []).map((c) => ({
      value: c.value,
      evidence: mapEvidence(c.evidence?.[0]) ?? { excerpt: "", documentLabel: "", locator: "" },
    })),
  };
}

function mapItemQuantity(item: RawTenderItem): number | null {
  // T1 does not carry item quantity as a top-level field on TenderItemTerms;
  // when present it is a fact within item.facts keyed by field_name
  // "item_quantity". Only a numeric explicit/derived value counts —
  // unknown/not_explicitly_found/conflicting facts must never surface a
  // fabricated number.
  const quantityFact = item.facts.find((f) => f.field_name === "item_quantity");
  if (!quantityFact) return null;
  if (quantityFact.state !== "explicit" && quantityFact.state !== "derived") return null;
  return typeof quantityFact.value === "number" ? quantityFact.value : null;
}

function mapItemAmount(item: RawTenderItem): number | null {
  // Same rule as mapItemQuantity: only an explicit/derived item_budget (or
  // unit_price) fact counts as a real amount. unknown/not_explicitly_found/
  // conflicting facts can still carry a raw numeric `value` on the wire (e.g.
  // one of several conflicting candidates) — that value must never be
  // presented as a confirmed amount.
  const budgetFact = item.facts.find((f) => f.field_name === "item_budget" || f.field_name === "unit_price");
  if (!budgetFact) return null;
  if (budgetFact.state !== "explicit" && budgetFact.state !== "derived") return null;
  return typeof budgetFact.value === "number" ? budgetFact.value : null;
}

function mapTenderDetail(raw: RawTenderDetailResponse): LicitacionIntel {
  const queueRow = raw.queue_row ?? {};
  const itemBudgetRows = raw.items.map((item) => {
    const budgetFact = item.facts.find((f) => f.field_name === "item_budget" || f.field_name === "unit_price");
    return {
      itemLabel: item.item_label ?? item.item_number ?? item.item_id,
      quantity: mapItemQuantity(item),
      amount: mapItemAmount(item),
      evidence: mapEvidence(budgetFact?.evidence?.[0]),
    };
  });

  // The current_opportunity_queue read model does not publish an
  // authoritative per-row coverage-completeness flag; that lives on the T1
  // coverage object. Partial T1 extraction must never be presented as fully
  // "available".
  const coverageComplete = raw.coverage?.is_complete === true;
  const partialCoverage = raw.t1_published && raw.coverage !== null && !coverageComplete;

  const incompleteReasonCodes = raw.coverage?.incomplete_reason_codes ?? [];

  const terms: Availed<readonly TermFact[]> = !raw.t1_published
    ? { status: "not_available" }
    : partialCoverage
      ? { status: "available_incomplete", reasonCodes: incompleteReasonCodes, partial: raw.tender_facts.map(mapTermFact) }
      : raw.tender_facts.length === 0
        ? { status: "available_empty" }
        : { status: "available", data: raw.tender_facts.map(mapTermFact) };

  const itemBudget: Availed<readonly typeof itemBudgetRows[number][]> = !raw.t1_published
    ? { status: "not_available" }
    : partialCoverage
      ? { status: "available_incomplete", reasonCodes: incompleteReasonCodes, partial: itemBudgetRows }
      : itemBudgetRows.length === 0
        ? { status: "available_empty" }
        : { status: "available", data: itemBudgetRows };

  // T1 does not yet publish an "anexo recognition delta" field on the wire —
  // never fabricate one; render as honestly not-available until it exists.
  const recognitionDelta: LicitacionIntel["recognitionDelta"] = { status: "not_available" };

  const coverageData = raw.coverage
    ? {
        documentsDiscovered: raw.coverage.attachments_discovered,
        documentsRead: raw.coverage.attachments_downloaded,
        incompleteReasonCodes: raw.coverage.incomplete_reason_codes,
      }
    : null;

  const coverage: LicitacionIntel["coverage"] =
    !raw.t1_published || raw.coverage === null || coverageData === null
      ? { status: "not_available" }
      : partialCoverage
        ? { status: "available_incomplete", reasonCodes: incompleteReasonCodes, partial: coverageData }
        : { status: "available", data: coverageData };

  // current_opportunity_queue membership already guarantees open_public
  // eligibility per W1's own queue-building rules (see listQueueRows'
  // `eligibilityStatus: "open_public"` mapping above) — the queue row does
  // not publish procurement_eligibility_status/procurement_method_raw as
  // authoritative fields, so eligibility here is derived from queue
  // membership itself, and procurementMethodRaw stays null (never inferred
  // from tender_code suffix patterns like "LP"/"LE"/"LR").
  return {
    tenderCode: raw.tender_code,
    buyerDisplayName: asString(queueRow.display_name),
    eligibilityStatus: raw.found_in_queue ? "open_public" : "unknown",
    procurementMethodRaw: null,
    terms,
    itemBudget,
    totalBudgetReconciled: false,
    recognitionDelta,
    coverage,
  };
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

  /**
   * Real T1+W1 merged tender detail (`GET /operator/procurement/tenders/{tender_code}`).
   * W1's current_opportunity_queue remains the sole actionability authority:
   * a 404 here means "not actionable per W1" and this returns null — T1
   * facts (if any exist) are never surfaced for a tender W1 doesn't list.
   * `mockData.ts`'s MOCK_LICITACION_TALCA is kept only for Storybook/tests,
   * it is not reachable from this live path any more.
   */
  async getLicitacionIntel(tenderCode: string): Promise<LicitacionIntel | null> {
    let raw: RawTenderDetailResponse;
    try {
      raw = await fetchProcurementJson<RawTenderDetailResponse>(
        `/operator/procurement/tenders/${encodeURIComponent(tenderCode)}`,
      );
    } catch (err) {
      if (err instanceof OperatorApiError && err.status === 404) {
        return null;
      }
      throw err;
    }
    return mapTenderDetail(raw);
  },

  /**
   * Operator annex-bundle upload PREVIEW
   * (`POST /operator/procurement/tenders/{tender_code}/annex-bundle/preview`).
   * Never persists, never publishes, never authorizes contact/outreach --
   * see TenderAnnexPreview's own field-level docs. `declareComplete`
   * defaults to false and is only ever sent when the caller explicitly
   * passed `true`; it is never inferred here. Raw ZIP bytes are sent as the
   * request body (not multipart) with an explicit `application/zip`
   * Content-Type, matching the server route's contract exactly. Throws
   * `OperatorApiError` on any non-2xx response (404 tender not actionable,
   * 422 rejected/corrupt ZIP, 413 too large, 415 wrong type, 503 W1
   * degraded) -- callers branch on `.status`, this never swallows an error
   * into a fabricated empty preview.
   */
  async previewTenderAnnexBundle(
    tenderCode: string,
    file: Blob,
    options: { declareComplete?: boolean } = {},
  ): Promise<TenderAnnexPreview> {
    const url = operatorApiUrl(
      `/operator/procurement/tenders/${encodeURIComponent(tenderCode)}/annex-bundle/preview`,
      options.declareComplete ? { declare_complete: true } : undefined,
    );
    const res = await fetch(url, {
      method: "POST",
      credentials: "include",
      headers: { Accept: "application/json", "Content-Type": "application/zip" },
      body: file,
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new OperatorApiError(text || res.statusText || `HTTP ${res.status}`, res.status);
    }
    const raw = (await res.json()) as RawTenderAnnexBundlePreviewResponse;
    return mapTenderAnnexPreview(raw);
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

  /**
   * The single authoritative source for actionable/current procurement
   * opportunities (TendersPage). Unlike `listQueueRows`, this surfaces the
   * response `meta` as an `Availed<...>` so a genuinely empty queue is never
   * confused with an unavailable feed (contract requirement: reduced_mode /
   * stale must stay visibly distinct from an empty result).
   *
   * Deliberately reads only the pre-filtered `current_opportunity_queue`
   * route — never an institution profile's `currentOpportunities` array,
   * which intentionally still includes non-actionable (e.g. restricted-
   * eligibility) tenders for historical/profile context. Falling back to
   * that array here would silently reintroduce tenders W1 has already
   * excluded from the actionable queue.
   */
  async getCurrentOpportunities(
    params: { page?: number; pageSize?: number } = {},
  ): Promise<{ availability: Availed<readonly CurrentOpportunityRow[]>; pageInfo: PageInfo }> {
    const page = params.page ?? 1;
    const pageSize = params.pageSize ?? 20;
    const query: Record<string, string | number> = { limit: pageSize, offset: toOffset(page, pageSize) };

    const raw = await fetchProcurementJson<RawListResponse>(
      "/operator/procurement/queues/current_opportunity",
      query,
    );
    const items = raw.items
      .map((r) => mapQueueRow(r as Record<string, unknown>))
      .filter((r): r is CurrentOpportunityRow => r !== null && r.queue === "current_opportunity_queue");

    return {
      availability: metaToAvailed(raw.meta, items),
      pageInfo: { page, pageSize, totalItems: raw.total },
    };
  },
};

export type InstitutionIntelAdapter = typeof institutionIntelAdapter;
