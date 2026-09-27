/**
 * Marketing client: GET reads over `/v2/workspace/marketing/*`, and the only three writes the
 * dashboard makes besides logout — creating and saving a campaign **draft**, and freezing a
 * draft's audience into an immutable recipient snapshot.
 *
 * The POSTs target exactly `CAMPAIGN_COMMAND_PATHS` (pinned by `src/test/noWritePolicy.test.ts`).
 * None approves or sends anything: the API has no such command, and there is no Send button.
 * The Worker forwards exactly these three POSTs; each still mounts upstream only behind its own
 * API switch, and a switched-off command is reported as "not enabled here".
 */

import { OperatorApiError, fetchJsonGet, operatorApiUrl } from "../../api/operatorClient";
import type {
  AudienceQuery,
  AudienceResponse,
  CampaignContent,
  DraftSaveResult,
  EquipmentTaxonomy,
  FreezeCriteria,
  FreezePreview,
  FreezeResult,
  FrozenSnapshot,
  RecontactDecision,
  ReviewDecision,
} from "./marketingTypes";

export const MARKETING_PATHS = {
  taxonomy: "/v2/workspace/marketing/taxonomy",
  audience: "/v2/workspace/marketing/audience",
  campaign: (id: string) => `/v2/workspace/marketing/campaigns/${encodeURIComponent(id)}`,
  freezePreview: (id: string) => `/v2/workspace/marketing/campaigns/${encodeURIComponent(id)}/freeze-preview`,
  recipients: (id: string) => `/v2/workspace/marketing/campaigns/${encodeURIComponent(id)}/recipients`,
} as const;

export const CAMPAIGN_COMMAND_PATHS = {
  create: "/v2/commands/create-campaign-draft",
  save: "/v2/commands/save-campaign-draft",
  freeze: "/v2/commands/freeze-campaign-audience",
} as const;

export const fetchTaxonomy = () => fetchJsonGet<EquipmentTaxonomy>(operatorApiUrl(MARKETING_PATHS.taxonomy));
export const fetchCampaign = (id: string) => fetchJsonGet<CampaignContent>(operatorApiUrl(MARKETING_PATHS.campaign(id)));

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
