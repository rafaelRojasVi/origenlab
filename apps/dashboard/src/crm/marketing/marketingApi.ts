/**
 * Marketing client: GET reads over `/v2/workspace/marketing/*`, and the only six writes the
 * dashboard makes besides logout — creating and saving a campaign **draft**, freezing a draft's
 * audience into an immutable recipient snapshot, setting an unsent campaign's internal planned
 * day, and (admin only) placing or lifting a campaign safety **block**.
 *
 * The POSTs target exactly `CAMPAIGN_COMMAND_PATHS` (pinned by `src/test/noWritePolicy.test.ts`).
 * None approves, schedules or sends anything: the API has no such command, and there is no Send
 * button. A block only stops things. The Worker forwards exactly these six POSTs; each still
 * mounts upstream only behind its own API switch, and a switched-off command is reported as
 * "not enabled here".
 */

import { OperatorApiError, fetchJsonGet, notifyIfSessionRefused, operatorApiUrl } from "../../api/operatorClient";
import type {
  AudienceQuery,
  AudienceResponse,
  AuditResponse,
  CampaignArchive,
  CampaignBlockResult,
  CampaignContent,
  CampaignHoldsResponse,
  DraftSaveResult,
  EquipmentTaxonomy,
  FreezeCriteria,
  FreezePreview,
  FreezeResult,
  FrozenSnapshot,
  PlanningResult,
  RecipientPage,
  RecipientQuery,
  RecontactDecision,
  RepliesResponse,
  ReviewDecision,
  SuppressionsResponse,
  UnsubscribeReviewResult,
} from "./marketingTypes";

export const MARKETING_PATHS = {
  taxonomy: "/v2/workspace/marketing/taxonomy",
  audience: "/v2/workspace/marketing/audience",
  campaign: (id: string) => `/v2/workspace/marketing/campaigns/${encodeURIComponent(id)}`,
  freezePreview: (id: string) => `/v2/workspace/marketing/campaigns/${encodeURIComponent(id)}/freeze-preview`,
  recipients: (id: string) => `/v2/workspace/marketing/campaigns/${encodeURIComponent(id)}/recipients`,
  archive: (id: string) => `/v2/workspace/marketing/campaigns/${encodeURIComponent(id)}/archive`,
  historyRecipients: (id: string) => `/v2/workspace/marketing/campaigns/${encodeURIComponent(id)}/history/recipients`,
  historyReplies: (id: string) => `/v2/workspace/marketing/campaigns/${encodeURIComponent(id)}/history/replies`,
  historyAudit: (id: string) => `/v2/workspace/marketing/campaigns/${encodeURIComponent(id)}/history/audit`,
  suppressions: "/v2/workspace/marketing/suppressions",
  campaignBlocks: "/v2/workspace/marketing/campaign-blocks",
} as const;

export const CAMPAIGN_COMMAND_PATHS = {
  create: "/v2/commands/create-campaign-draft",
  save: "/v2/commands/save-campaign-draft",
  freeze: "/v2/commands/freeze-campaign-audience",
  plan: "/v2/commands/set-campaign-planning",
  block: "/v2/commands/block-campaign",
  unblock: "/v2/commands/unblock-campaign",
  testSend: "/v2/commands/send-campaign-test",
} as const;

export const fetchTaxonomy = () => fetchJsonGet<EquipmentTaxonomy>(operatorApiUrl(MARKETING_PATHS.taxonomy));
export const fetchCampaign = (id: string) => fetchJsonGet<CampaignContent>(operatorApiUrl(MARKETING_PATHS.campaign(id)));
/** W10 suppression status — a read. There is no unsubscribe command in the dashboard. */
export const fetchSuppressions = () => fetchJsonGet<SuppressionsResponse>(operatorApiUrl(MARKETING_PATHS.suppressions));
/** Campaign safety blocks — a read, shown to every role (a viewer's copy carries no reason). */
export const fetchCampaignBlocks = () =>
  fetchJsonGet<CampaignHoldsResponse>(operatorApiUrl(MARKETING_PATHS.campaignBlocks));
export const fetchCampaignArchive = (id: string) =>
  fetchJsonGet<CampaignArchive>(operatorApiUrl(MARKETING_PATHS.archive(id)));

/** One page of a campaign's recorded recipients, under the predicate of the total clicked. */
export function fetchCampaignRecipients(campaignId: string, query: RecipientQuery): Promise<RecipientPage> {
  return fetchJsonGet<RecipientPage>(
    operatorApiUrl(MARKETING_PATHS.historyRecipients(campaignId), {
      total: query.total,
      reason: query.reason || undefined,
      identity: query.identity || undefined,
      q: query.q?.trim() || undefined,
      page: query.page,
      page_size: query.page_size,
    }),
  );
}
export const fetchCampaignReplies = (id: string) =>
  fetchJsonGet<RepliesResponse>(operatorApiUrl(MARKETING_PATHS.historyReplies(id)));
export const fetchCampaignAudit = (id: string) => fetchJsonGet<AuditResponse>(operatorApiUrl(MARKETING_PATHS.historyAudit(id)));

export function fetchAudience(query: AudienceQuery): Promise<AudienceResponse> {
  const params: Record<string, string | string[] | undefined> = {
    family_id: query.family_id || undefined,
    brand_id: query.brand_id || undefined,
    model_id: query.model_id || undefined,
    organization_id: query.organization_id || undefined,
    recorded: query.recorded || undefined,
    q: query.q?.trim() || undefined,
    basis: query.basis && query.basis.length ? query.basis : undefined,
  };
  return fetchJsonGet<AudienceResponse>(operatorApiUrl(MARKETING_PATHS.audience, params));
}

function criteriaParams(c: FreezeCriteria): Record<string, string | string[] | undefined> {
  return {
    family_id: c.family_id || undefined,
    brand_id: c.brand_id || undefined,
    model_id: c.model_id || undefined,
    organization_id: c.organization_id || undefined,
    recorded: c.recorded || undefined,
    q: c.q?.trim() || undefined,
    basis: c.bases.length ? c.bases : undefined,
    scope: c.scope,
  };
}

export const fetchFreezePreview = (campaignId: string, criteria: FreezeCriteria) =>
  fetchJsonGet<FreezePreview>(operatorApiUrl(MARKETING_PATHS.freezePreview(campaignId), criteriaParams(criteria)));
export const fetchFrozenRecipients = (campaignId: string) =>
  fetchJsonGet<FrozenSnapshot>(operatorApiUrl(MARKETING_PATHS.recipients(campaignId)));

export interface DraftFields {
  name: string;
  subject: string;
  preheader: string;
  body_html: string;
  max_sends: number;
  recontact_interval_days: number;
}

function newIdempotencyKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `draft-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/** The single mutating request of this module. */
async function postCommand<T>(path: string, body: unknown, idempotencyKey: string): Promise<T> {
  const res = await fetch(operatorApiUrl(path), {
    method: "POST",
    credentials: "include",
    headers: { Accept: "application/json", "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    notifyIfSessionRefused(res.status);
    const text = await res.text().catch(() => "");
    throw new OperatorApiError(text || res.statusText || `HTTP ${res.status}`, res.status);
  }
  return res.json() as Promise<T>;
}

export function createCampaignDraft(
  fields: DraftFields,
  duplicatedFrom: string | null,
  idempotencyKey: string = newIdempotencyKey(),
): Promise<DraftSaveResult> {
  return postCommand<DraftSaveResult>(
    CAMPAIGN_COMMAND_PATHS.create,
    { ...fields, duplicated_from_campaign_id: duplicatedFrom },
    idempotencyKey,
  );
}

export function saveCampaignDraft(
  campaignId: string,
  expectedVersion: number,
  fields: DraftFields,
  idempotencyKey: string = newIdempotencyKey(),
): Promise<DraftSaveResult> {
  return postCommand<DraftSaveResult>(
    CAMPAIGN_COMMAND_PATHS.save,
    { ...fields, campaign_id: campaignId, expected_version: expectedVersion },
    idempotencyKey,
  );
}

/**
 * Freeze a draft's audience. Sent only from the final confirmation screen, with the preview
 * fingerprint the operator was shown and `confirmed: true`. Writes a snapshot; sends nothing.
 */
export function freezeCampaignAudience(
  body: {
    campaign_id: string;
    expected_version: number;
    expected_preview_sha256: string;
    criteria: FreezeCriteria;
    review_decisions: ReviewDecision[];
    excluded_keys: string[];
    recontact_decisions: RecontactDecision[];
  },
  idempotencyKey: string = newIdempotencyKey(),
): Promise<FreezeResult> {
  const c = body.criteria;
  const criteria = {
    family_id: c.family_id || null,
    brand_id: c.brand_id || null,
    model_id: c.model_id || null,
    organization_id: c.organization_id || null,
    bases: c.bases,
    recorded: c.recorded || null,
    q: c.q.trim() || null,
    scope: c.scope,
  };
  return postCommand<FreezeResult>(CAMPAIGN_COMMAND_PATHS.freeze, { ...body, criteria, confirmed: true }, idempotencyKey);
}

/**
 * Set, change or clear an unsent campaign's internal planned day (and optional time, both in
 * America/Santiago). Planning metadata only: it approves, freezes, schedules and sends nothing.
 */
export function setCampaignPlanning(
  body: { campaign_id: string; expected_planning_version: number; planned_for_date: string | null; planned_for_time: string | null },
  idempotencyKey: string = newIdempotencyKey(),
): Promise<PlanningResult> {
  return postCommand<PlanningResult>(CAMPAIGN_COMMAND_PATHS.plan, body, idempotencyKey);
}

/** W10 review of a «BAJA» held for review: confirm (sales/admin) or dismiss (admin only). */
export const UNSUBSCRIBE_REVIEW_PATHS = {
  resolve: "/v2/commands/resolve-unsubscribe-review",
  dismiss: "/v2/commands/dismiss-unsubscribe-review",
} as const;

export function resolveUnsubscribeReview(
  body: { assertion_id: string; expected_address: string; note: string },
  idempotencyKey: string = newIdempotencyKey(),
): Promise<UnsubscribeReviewResult> {
  return postCommand<UnsubscribeReviewResult>(UNSUBSCRIBE_REVIEW_PATHS.resolve, body, idempotencyKey);
}

export function dismissUnsubscribeReview(
  body: { assertion_id: string; expected_address: string; expected_review_sha256: string; explanation: string },
  idempotencyKey: string = newIdempotencyKey(),
): Promise<UnsubscribeReviewResult> {
  return postCommand<UnsubscribeReviewResult>(UNSUBSCRIBE_REVIEW_PATHS.dismiss, body, idempotencyKey);
}

/**
 * Admin only: block one campaign, or every campaign. It refuses freezing, approving and sending
 * while it stands; it enqueues, sends and rewrites nothing, and it never expires.
 */
export function blockCampaign(
  body: { scope: "campaign" | "all_campaigns"; campaign_id: string | null; expected_block_version: number; reason: string },
  idempotencyKey: string = newIdempotencyKey(),
): Promise<CampaignBlockResult> {
  return postCommand<CampaignBlockResult>(CAMPAIGN_COMMAND_PATHS.block, body, idempotencyKey);
}

/** Admin only: lift one active block, with its own reason. Lifting starts nothing. */
export function unblockCampaign(
  body: { block_id: string; expected_version: number; reason: string },
  idempotencyKey: string = newIdempotencyKey(),
): Promise<CampaignBlockResult> {
  return postCommand<CampaignBlockResult>(CAMPAIGN_COMMAND_PATHS.unblock, body, idempotencyKey);
}

export { newIdempotencyKey };

/** The API's refusal `{detail: {code, message}}`, when the error carries one. */
export function refusalOf(err: unknown): { code: string; message: string } | null {
  if (!(err instanceof OperatorApiError)) return null;
  if (err.message.includes("path_not_allowed")) return { code: "path_not_allowed", message: err.message };
  try {
    const parsed = JSON.parse(err.message) as { detail?: unknown };
    const d = parsed.detail;
    if (d && typeof d === "object" && "code" in d && "message" in d) {
      return { code: String((d as { code: unknown }).code), message: String((d as { message: unknown }).message) };
    }
    if (typeof d === "string") return { code: `http_${err.status}`, message: d };
  } catch {
    /* not JSON */
  }
  return { code: `http_${err.status}`, message: err.message };
}

export type TestSendTarget = { campaign_id: string } | { v1_lane_key: string };
export interface TestSendResult { status: "sent"; to: string; subject: string; gmail_message_id: string; sent_at: string }
export interface TestSendHistory {
  tests: { at: string; by: string | null; to: string; status: "sent" | "failed"; error: string | null }[];
  remaining: { hour: number; day: number };
}
/** Admin-only «Enviar prueba»: the campaign's own email to ONE address, from the shared account. */
export function sendCampaignTest(target: TestSendTarget, to: string, idempotencyKey: string = newIdempotencyKey()) {
  return postCommand<TestSendResult>(CAMPAIGN_COMMAND_PATHS.testSend, { ...target, to }, idempotencyKey);
}
export function fetchTestSendHistory(target: TestSendTarget): Promise<TestSendHistory> {
  return fetchJsonGet<TestSendHistory>(operatorApiUrl("/v2/workspace/marketing/test-send-history", target));
}
