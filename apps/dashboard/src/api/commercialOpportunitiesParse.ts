import type {
  CommercialOpportunitiesMeta,
  CommercialOpportunitiesResponse,
  CommercialOpportunityConflict,
  CommercialOpportunityDataSource,
  CommercialOpportunityDetailResponse,
  CommercialOpportunityEvent,
  CommercialOpportunityEvidence,
  CommercialOpportunityItem,
} from "./commercialOpportunitiesTypes";

type Row = Record<string, unknown>;

function row(value: unknown, label: string): Row {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
  return value as Row;
}

function requiredString(value: unknown, field: string): string {
  if (typeof value !== "string") {
    throw new Error(`${field} must be a string`);
  }
  return value;
}

function nullableString(value: unknown, field: string): string | null {
  if (value === null || value === undefined) {
    return null;
  }
  return requiredString(value, field);
}

function requiredBoolean(value: unknown, field: string): boolean {
  if (typeof value !== "boolean") {
    throw new Error(`${field} must be a boolean`);
  }
  return value;
}

function requiredNumber(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`${field} must be a finite number`);
  }
  return value;
}

function nullableNumber(value: unknown, field: string): number | null {
  if (value === null || value === undefined) {
    return null;
  }
  return requiredNumber(value, field);
}

function dataSource(value: unknown): CommercialOpportunityDataSource {
  if (value !== "sqlite_pr3" && value !== "postgres_mirror") {
    throw new Error("data_source must be sqlite_pr3 or postgres_mirror");
  }
  return value;
}

function parseItem(raw: unknown): CommercialOpportunityItem {
  const value = row(raw, "commercial opportunity");

  return {
    opportunity_id: requiredString(value.opportunity_id, "opportunity_id"),
    record_kind: requiredString(value.record_kind, "record_kind"),
    account_id: nullableString(value.account_id, "account_id"),
    primary_contact_id: nullableString(
      value.primary_contact_id,
      "primary_contact_id",
    ),
    contact_display_email: nullableString(
      value.contact_display_email,
      "contact_display_email",
    ),
    account_display_domain: nullableString(
      value.account_display_domain,
      "account_display_domain",
    ),
    source_kind: requiredString(value.source_kind, "source_kind"),
    source_key: requiredString(value.source_key, "source_key"),
    deal_key: nullableString(value.deal_key, "deal_key"),
    canonical_stage: requiredString(
      value.canonical_stage,
      "canonical_stage",
    ),
    source_stage: requiredString(value.source_stage, "source_stage"),
    stage_reason_code: requiredString(
      value.stage_reason_code,
      "stage_reason_code",
    ),
    stage_confidence: requiredString(
      value.stage_confidence,
      "stage_confidence",
    ),
    stage_is_current: requiredBoolean(
      value.stage_is_current,
      "stage_is_current",
    ),
    stage_is_terminal: requiredBoolean(
      value.stage_is_terminal,
      "stage_is_terminal",
    ),
    stage_evidence_at: nullableString(
      value.stage_evidence_at,
      "stage_evidence_at",
    ),
    stage_evidence_id: nullableString(
      value.stage_evidence_id,
      "stage_evidence_id",
    ),
    first_activity_at: nullableString(
      value.first_activity_at,
      "first_activity_at",
    ),
    last_activity_at: nullableString(
      value.last_activity_at,
      "last_activity_at",
    ),
    identity_link_status: requiredString(
      value.identity_link_status,
      "identity_link_status",
    ),
    review_status: requiredString(value.review_status, "review_status"),
    synced_at: nullableString(value.synced_at, "synced_at"),
  };
}

function parseEvent(raw: unknown): CommercialOpportunityEvent {
  const value = row(raw, "commercial opportunity event");

  return {
    event_id: requiredString(value.event_id, "event_id"),
    opportunity_id: requiredString(
      value.opportunity_id,
      "opportunity_id",
    ),
    canonical_event_type: requiredString(
      value.canonical_event_type,
      "canonical_event_type",
    ),
    source_event_type: requiredString(
      value.source_event_type,
      "source_event_type",
    ),
    event_at: nullableString(value.event_at, "event_at"),
    source_table: requiredString(value.source_table, "source_table"),
    source_record_id: requiredString(
      value.source_record_id,
      "source_record_id",
    ),
    source_email_id: nullableNumber(
      value.source_email_id,
      "source_email_id",
    ),
    source_attachment_id: nullableNumber(
      value.source_attachment_id,
      "source_attachment_id",
    ),
    confidence: requiredString(value.confidence, "confidence"),
    operator_confirmed: requiredBoolean(
      value.operator_confirmed,
      "operator_confirmed",
    ),
    detail_json: value.detail_json ?? null,
    synced_at: nullableString(value.synced_at, "synced_at"),
  };
}

function parseEvidence(raw: unknown): CommercialOpportunityEvidence {
  const value = row(raw, "commercial opportunity evidence");

  return {
    evidence_id: requiredString(value.evidence_id, "evidence_id"),
    opportunity_id: requiredString(
      value.opportunity_id,
      "opportunity_id",
    ),
    subject_kind: requiredString(value.subject_kind, "subject_kind"),
    source_table: requiredString(value.source_table, "source_table"),
    source_record_id: requiredString(
      value.source_record_id,
      "source_record_id",
    ),
    evidence_type: requiredString(
      value.evidence_type,
      "evidence_type",
    ),
    evidence_at: nullableString(value.evidence_at, "evidence_at"),
    confidence: requiredString(value.confidence, "confidence"),
    reason_code: requiredString(value.reason_code, "reason_code"),
    source_email_id: nullableNumber(
      value.source_email_id,
      "source_email_id",
    ),
    source_attachment_id: nullableNumber(
      value.source_attachment_id,
      "source_attachment_id",
    ),
    detail_json: value.detail_json ?? null,
    synced_at: nullableString(value.synced_at, "synced_at"),
  };
}

function parseConflict(raw: unknown): CommercialOpportunityConflict {
  const value = row(raw, "commercial opportunity conflict");

  return {
    conflict_id: requiredString(value.conflict_id, "conflict_id"),
    opportunity_id: nullableString(
      value.opportunity_id,
      "opportunity_id",
    ),
    conflict_type: requiredString(
      value.conflict_type,
      "conflict_type",
    ),
    reason_code: requiredString(value.reason_code, "reason_code"),
    subject_keys_json: value.subject_keys_json,
    evidence_pointers_json: value.evidence_pointers_json,
    review_status: requiredString(
      value.review_status,
      "review_status",
    ),
    detail_json: value.detail_json ?? null,
    synced_at: nullableString(value.synced_at, "synced_at"),
  };
}

function parseMeta(raw: unknown): CommercialOpportunitiesMeta {
  const value = row(raw, "commercial opportunities meta");

  return {
    data_source: dataSource(value.data_source),
    read_only: requiredBoolean(value.read_only, "read_only"),
    count: requiredNumber(value.count, "count"),
    total_count: requiredNumber(value.total_count, "total_count"),
    limit: requiredNumber(value.limit, "limit"),
    offset: requiredNumber(value.offset, "offset"),
    reduced_mode: requiredBoolean(
      value.reduced_mode,
      "reduced_mode",
    ),
    note: requiredString(value.note, "note"),
  };
}

function array(value: unknown, field: string): unknown[] {
  if (!Array.isArray(value)) {
    throw new Error(`${field} must be an array`);
  }
  return value;
}

export function parseCommercialOpportunitiesResponse(
  raw: unknown,
): CommercialOpportunitiesResponse {
  const value = row(raw, "commercial opportunities response");

  return {
    meta: parseMeta(value.meta),
    items: array(value.items, "items").map(parseItem),
  };
}

export function parseCommercialOpportunityDetailResponse(
  raw: unknown,
): CommercialOpportunityDetailResponse {
  const value = row(raw, "commercial opportunity detail response");
  const meta = row(value.meta, "commercial opportunity detail meta");

  return {
    meta: {
      data_source: dataSource(meta.data_source),
      read_only: requiredBoolean(meta.read_only, "read_only"),
    },
    opportunity: parseItem(value.opportunity),
    events: array(value.events, "events").map(parseEvent),
    evidence: array(value.evidence, "evidence").map(parseEvidence),
    conflicts: array(value.conflicts, "conflicts").map(parseConflict),
  };
}
