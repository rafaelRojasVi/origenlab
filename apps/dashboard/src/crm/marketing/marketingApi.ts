/**
 * Marketing client: GET reads over `/v2/workspace/marketing/*`, and the only two writes the
 * dashboard makes besides logout — creating and saving a campaign **draft**.
 *
 * The two POSTs target exactly `CAMPAIGN_DRAFT_COMMAND_PATHS` (pinned by
 * `src/test/noWritePolicy.test.ts`). They can freeze no audience, approve nothing and send
 * nothing: the API has no such command. Behind the production Worker they are refused (the
 * proxy allows no POST under `/v2`), which the editor reports as "not enabled here".
 */

import { OperatorApiError, fetchJsonGet, operatorApiUrl } from "../../api/operatorClient";
import type {
  AudienceQuery,
  AudienceResponse,
  CampaignContent,
  DraftSaveResult,
  EquipmentTaxonomy,
} from "./marketingTypes";

export const MARKETING_PATHS = {
  taxonomy: "/v2/workspace/marketing/taxonomy",
  audience: "/v2/workspace/marketing/audience",
  campaign: (id: string) => `/v2/workspace/marketing/campaigns/${encodeURIComponent(id)}`,
} as const;

export const CAMPAIGN_DRAFT_COMMAND_PATHS = {
  create: "/v2/commands/create-campaign-draft",
  save: "/v2/commands/save-campaign-draft",
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
async function postDraftCommand(path: string, body: unknown, idempotencyKey: string): Promise<DraftSaveResult> {
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
  return res.json() as Promise<DraftSaveResult>;
}

export function createCampaignDraft(
  fields: DraftFields,
  duplicatedFrom: string | null,
  idempotencyKey: string = newIdempotencyKey(),
): Promise<DraftSaveResult> {
  return postDraftCommand(
    CAMPAIGN_DRAFT_COMMAND_PATHS.create,
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
  return postDraftCommand(
    CAMPAIGN_DRAFT_COMMAND_PATHS.save,
    { ...fields, campaign_id: campaignId, expected_version: expectedVersion },
    idempotencyKey,
  );
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
