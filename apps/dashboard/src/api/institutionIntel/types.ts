/**
 * UI-facing types for institution/prospect intelligence, backed by W1
 * (`apps/api/docs/INSTITUTION_PROSPECT_API_CONTRACT.md`, PR #475).
 *
 * These stay a clean, camelCase UI contract — the adapter is responsible for
 * defensively parsing W1's loose/snake_case JSON into this shape, not this
 * file. Institutions/queues/status are real; `LicitacionIntel` (tender-level
 * commercial terms, anexo recognition) has NO backend coverage in W1 per the
 * contract's own "T1 / ANEXO status" section — it stays mock-backed in the
 * adapter until a future phase adds it.
 */

/** Distinguishes "no data" from "read, and there's genuinely nothing" from "we couldn't finish reading." */
export type AvailabilityStatus =
  | "not_available"
  | "available_empty"
  | "available_incomplete"
  | "available";

export type Availed<T> =
  | { status: "not_available" }
  | { status: "available_empty" }
  | { status: "available_incomplete"; reasonCodes: readonly string[]; partial?: T }
  | { status: "available"; data: T };

/**
 * Raw `meta` object every W1 response carries. Availability state is derived
 * from `reduced_mode` + `canonical_reason` + `stale` — never from an empty
 * `items` array alone. See adapter.ts's `metaToAvailed`.
 */
export interface ProcurementMeta {
  data_source: string;
  read_only: boolean;
  contract_version: string;
  supported_contract_version: boolean;
  reduced_mode: boolean;
  stale: boolean;
  canonical_reason: string;
  note: string;
  as_of_utc: string | null;
  not_persisted: boolean;
  contact_authorization: boolean;
  outreach_authorization: boolean;
}

/** A fact traced back to its exact source — used everywhere a claim needs backing. */
export interface EvidenceRef {
  excerpt: string;
  documentLabel: string;
  locator: string;
  sourceHint?: string;
}

// ---------------------------------------------------------------------------
// Three independent axes — never combine into one score (product decision).
// ---------------------------------------------------------------------------

export type AxisBand = "none" | "low" | "medium" | "high";

export interface AxisScore {
  band: AxisBand;
  score: number;
  reasonCodes: readonly string[];
}

export interface InstitutionAxes {
  prospectStrength: AxisScore;
  opportunityUrgency: AxisScore;
  contactReadiness: AxisScore;
}

// ---------------------------------------------------------------------------
// Identity
// ---------------------------------------------------------------------------

export type IdentityKind =
  | "origenlab_account"
  | "chilecompra_buyer_source_id"
  | "normalized_buyer_name"
  | "unresolved_buyer"
  | "identity_review";

export interface InstitutionIdentitySummary {
  institutionId: string;
  displayName: string;
  identityKind: IdentityKind;
  linkedAccountPresent: boolean;
  identityReviewRequired: boolean;
}

// ---------------------------------------------------------------------------
// Equipment history
// ---------------------------------------------------------------------------

export type DemandRecurrence =
  | "observed_once_confirmed"
  | "repeated_observed_demand_confirmed"
  | "repeated_confirmed_with_unresolved_relationships"
  | "recurrence_not_established";

export interface EquipmentHistoryEntry {
  category: string;
  distinctTenderCount: number;
  firstObservedDate: string | null;
  mostRecentObservedDate: string | null;
  demandRecurrence: DemandRecurrence;
  openCurrentTenderCount: number;
  historicalTenderCount: number;
  fromAnexoEvidence: boolean;
  snippets: readonly EvidenceRef[];
}

// ---------------------------------------------------------------------------
// Tenders / eligibility
// ---------------------------------------------------------------------------

export type ProcurementEligibilityStatus =
  | "open_public"
  | "restricted_invitation_unconfirmed"
  | "unknown";

export interface InstitutionTenderRow {
  tenderCode: string;
  categoryOrTitle: string;
  lifecycleClass: string;
  closeTimestamp: string | null;
  eligibilityStatus: ProcurementEligibilityStatus;
}

// ---------------------------------------------------------------------------
// Contact overlay — summary/counts/status only.
// No named-contact records here on purpose: institution<->contact joining is
// explicitly W1's call, not the frontend's. Real named contacts (name/email/
// Gmail history) stay mock-only until W1 decides whether that join exists.
// ---------------------------------------------------------------------------

export type ContactGapStatus =
  | "existing_verified_contact"
  | "existing_contact_needs_role_review"
  | "role_known_email_missing"
  | "contacts_present_but_blocked"
  | "linked_account_no_contact"
  | "account_unlinked"
  | "account_ambiguous";

export interface ContactOverlaySummary {
  contactGapStatus: ContactGapStatus;
  knownContactCount: number;
  suitableContactCount: number;
  verifiedContactCount: number;
  nextAction: string;
}

/** Mock-only. Real app must not compute this via fuzzy org/domain matching — see file header. */
export interface MockOnlyNamedContact {
  name: string | null;
  email: string;
  roleStatus: string;
  gmailHistory: { sentCount: number; lastSentDaysAgo: number; replied: boolean } | null;
}

// ---------------------------------------------------------------------------
// Market signal — category demand only. No region/sector: that data does not
// exist anywhere in the ChileCompra pipeline as of this writing.
// ---------------------------------------------------------------------------

export interface MarketCategorySignal {
  category: string;
  institutionCount: number;
  repeatInstitutionCount: number;
}

// ---------------------------------------------------------------------------
// Institution profile — the full assembled shape a profile screen needs.
// ---------------------------------------------------------------------------

export type QueueName =
  | "current_opportunity_queue"
  | "historical_prospect_queue"
  | "contact_gap_queue"
  | "institution_match_review_queue"
  | "line_evidence_review_queue"
  | "retender_review_queue";

export interface InstitutionProfile {
  identity: InstitutionIdentitySummary;
  axes: InstitutionAxes;
  equipmentHistory: Availed<readonly EquipmentHistoryEntry[]>;
  currentOpportunities: readonly InstitutionTenderRow[];
  historicalSignals: readonly InstitutionTenderRow[];
  contactOverlay: ContactOverlaySummary;
  queueMembership: readonly QueueName[];
  marketSignal: Availed<MarketCategorySignal>;
  /** Mock-only preview of what a contact list *could* show — see MockOnlyNamedContact. */
  mockOnlyContacts?: readonly MockOnlyNamedContact[];
}

export interface InstitutionListItem {
  identity: InstitutionIdentitySummary;
  axes: InstitutionAxes;
  categories: readonly string[];
  queueMembership: readonly QueueName[];
}

// ---------------------------------------------------------------------------
// Licitación (tender) intel — extends the existing equipment-opportunity data
// with terms/recognition/coverage. Not a replacement for EquipmentOpportunityItem.
// ---------------------------------------------------------------------------

export type TermFactState = "explicit" | "derived" | "not_explicitly_found" | "unknown" | "conflicting";

export interface TermFactCandidate {
  value: string | number;
  evidence: EvidenceRef;
}

/**
 * Structured `value` shape T1 publishes for `maximum_delivery_days` instead
 * of a plain number — `{ days, day_basis }`, where `day_basis` is
 * `"business"` (días hábiles), `"calendar"` (días corridos), or `null` when
 * T1's extractor could not confirm a basis (see
 * apps/email-pipeline's commercial_procurement_anexo_tender_terms/extract.py
 * `_parse_duration_days_match`). Named explicitly here — never widened to
 * `any`/`unknown` — so a malformed/unrecognized shape can fail closed to
 * "—" instead of ever being stringified (`[object Object]`) in the UI.
 */
export interface DeliveryDaysValue {
  days: number;
  day_basis: string | null;
}

export interface TermFact {
  fieldName: string;
  label: string;
  state: TermFactState;
  value: string | number | null | DeliveryDaysValue;
  unit?: string;
  evidence?: EvidenceRef;
  candidates?: readonly TermFactCandidate[];
}

export interface ItemBudgetRow {
  itemLabel: string;
  quantity: number | null;
  amount: number | null;
  currency?: string;
  evidence?: EvidenceRef;
}

export interface AnexoRecognitionDelta {
  changeClass: string;
  baselineCategories: readonly string[];
  augmentedCategories: readonly string[];
  addedCategories: readonly string[];
  confidenceBand: "low" | "medium" | "high";
  evidence?: EvidenceRef;
}

export interface CoverageStatus {
  documentsDiscovered: number;
  documentsRead: number;
  incompleteReasonCodes: readonly string[];
}

export interface LicitacionIntel {
  tenderCode: string;
  buyerDisplayName: string;
  eligibilityStatus: ProcurementEligibilityStatus;
  procurementMethodRaw: string | null;
  terms: Availed<readonly TermFact[]>;
  itemBudget: Availed<readonly ItemBudgetRow[]>;
  totalBudgetReconciled: boolean;
  recognitionDelta: Availed<AnexoRecognitionDelta>;
  coverage: Availed<CoverageStatus>;
}

// ---------------------------------------------------------------------------
// Prospect queue rows — one flat, discriminated-union feed the "Colas W1"
// preview screen groups/sorts client-side. Grouping/sorting is a frontend
// decision; backend just needs to expose sortable fields (see data-requirements doc).
// ---------------------------------------------------------------------------

interface QueueRowBase {
  rowId: string;
  institutionId: string;
  institutionDisplayName: string;
}

export interface CurrentOpportunityRow extends QueueRowBase {
  queue: "current_opportunity_queue";
  tenderCode: string;
  categoryOrTitle: string;
  closeTimestamp: string | null;
  eligibilityStatus: ProcurementEligibilityStatus;
  prospectStrengthBand: AxisBand;
}

export interface HistoricalProspectRow extends QueueRowBase {
  queue: "historical_prospect_queue";
  category: string;
  commercialSignalType: string;
  mostRecentObservedDate: string | null;
  tenderCount: number;
}

export interface ContactGapRow extends QueueRowBase {
  queue: "contact_gap_queue";
  contactGapStatus: ContactGapStatus;
  prospectStrengthBand: AxisBand;
  opportunityUrgencyBand: AxisBand;
  queueEntryReason: string;
}

export interface InstitutionMatchReviewRow extends QueueRowBase {
  queue: "institution_match_review_queue";
  // Legacy/mock preview fields.
  conflictReason?: string;
  candidateDisplayNames?: readonly string[];
  // W1 production cluster-grain fields.
  reviewClusterId?: string;
  resolutionStatus?: string;
  memberProfileIds?: readonly string[];
  reasonCodes?: readonly string[];
  confirmedAccount?: boolean;
}

export interface LineEvidenceReviewRow extends QueueRowBase {
  queue: "line_evidence_review_queue";
  tenderCode: string;
  // Legacy/mock preview field. Production W1 does not expose raw clause text.
  clauseText?: string;
  relevanceClass?: string;
  equipmentScopes?: readonly string[];
  canonicalEquipmentClasses?: readonly string[];
  lineDisposition?: string;
  reasonCodes?: readonly string[];
  ambiguityReasonCodes?: readonly string[];
}

export interface RetenderReviewRow extends QueueRowBase {
  queue: "retender_review_queue";
  tenderCodes: readonly string[];
  // Legacy/mock preview field.
  resolutionReason?: string;
  // W1 production family-grain fields.
  buyerKey?: string;
  recurrenceStatus?: string;
  resolutionStatus?: string;
  reasonCodes?: readonly string[];
  unresolvedRelationshipCount?: string;
}

export type ProspectQueueRow =
  | CurrentOpportunityRow
  | HistoricalProspectRow
  | ContactGapRow
  | InstitutionMatchReviewRow
  | LineEvidenceReviewRow
  | RetenderReviewRow;

export interface PageInfo {
  page: number;
  pageSize: number;
  totalItems: number;
}

export interface Paged<T> {
  items: readonly T[];
  pageInfo: PageInfo;
}
