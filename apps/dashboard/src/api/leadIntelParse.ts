import type {
  LeadProspectDetailResponseUi,
  LeadProspectsListUi,
  LeadResearchSummaryUi,
} from "./leadIntelTypes";

const FORBIDDEN_KEYS = new Set([
  "evidence_email_id",
  "transfer_id",
  "operation_id",
  "source_file",
  "gmail_url",
  "body",
  "input_file_name",
  "batch_key",
]);

function assertNoForbiddenKeys(obj: unknown, path = ""): void {
  if (Array.isArray(obj)) {
    obj.forEach((item, i) => assertNoForbiddenKeys(item, `${path}[${i}]`));
    return;
  }
  if (!obj || typeof obj !== "object") {
    return;
  }
  for (const [key, value] of Object.entries(obj as Record<string, unknown>)) {
    if (FORBIDDEN_KEYS.has(key)) {
      throw new Error(`Forbidden field in lead intel response: ${path}.${key}`);
    }
    assertNoForbiddenKeys(value, path ? `${path}.${key}` : key);
  }
}

export function parseLeadProspectsListResponse(raw: unknown): LeadProspectsListUi {
  assertNoForbiddenKeys(raw);
  const body = raw as LeadProspectsListUi;
  return {
    table_available: Boolean(body.table_available),
    items: Array.isArray(body.items) ? body.items : [],
    total: Number(body.total) || 0,
    data_source: String(body.data_source ?? "postgres_mirror"),
    read_only: body.read_only !== false,
    disclaimer: String(body.disclaimer ?? ""),
  };
}

export function parseLeadProspectDetailResponse(raw: unknown): LeadProspectDetailResponseUi {
  assertNoForbiddenKeys(raw);
  const body = raw as LeadProspectDetailResponseUi;
  return {
    table_available: Boolean(body.table_available),
    prospect: body.prospect ?? null,
    evidence: Array.isArray(body.evidence) ? body.evidence : [],
    recommendation: body.recommendation ?? null,
    block_reasons: Array.isArray(body.block_reasons) ? body.block_reasons : [],
    data_source: String(body.data_source ?? "postgres_mirror"),
    read_only: body.read_only !== false,
    disclaimer: String(body.disclaimer ?? ""),
  };
}

export function parseLeadResearchSummaryResponse(raw: unknown): LeadResearchSummaryUi {
  assertNoForbiddenKeys(raw);
  const body = raw as LeadResearchSummaryUi;
  return {
    table_available: Boolean(body.table_available),
    total: Number(body.total) || 0,
    review_count: Number(body.review_count) || 0,
    blocked_count: Number(body.blocked_count) || 0,
    net_new_safe: Number(body.net_new_safe) || 0,
    gmail_historico: Number(body.gmail_historico) || 0,
    followup_antiguo: Number(body.followup_antiguo) || 0,
    caso_activo: Number(body.caso_activo) || 0,
    public_tender_review: Number(body.public_tender_review) || 0,
    same_domain_review: Number(body.same_domain_review) || 0,
    research_needed: Number(body.research_needed) || 0,
    ready_to_contact: Number(body.ready_to_contact) || 0,
    already_contacted: Number(body.already_contacted) || 0,
    needs_email_enrichment: Number(body.needs_email_enrichment) || 0,
    tender_opportunity: Number(body.tender_opportunity) || 0,
    review_history: Number(body.review_history) || 0,
    followup_eligible: Number(body.followup_eligible) || 0,
    data_source: String(body.data_source ?? "postgres_mirror"),
    read_only: body.read_only !== false,
    disclaimer: String(body.disclaimer ?? ""),
  };
}
