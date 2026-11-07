/**
 * «Catálogo web» client: read a model sheet, publish it, and read how the deployment is going.
 *
 * This module owns every `/v2/web/...` path string and the one POST (`publish-model-sheet`) — pinned
 * by `src/test/noWritePolicy.test.ts`. The POST commits the sheet to the site's repository and starts
 * its deployment (the API checks the schema and the admin role); it writes no commercial record.
 */

import { OperatorApiError, fetchJsonGet, notifyIfSessionRefused, operatorApiUrl } from "../../api/operatorClient";
import { refusalOfError } from "../../api/refusalEnvelope";
import type { SchemaError } from "./modelSheet";

export const WEB_PUBLISH_PATH = "/v2/web/publish-model-sheet";
export const WEB_SHEET_PATH = "/v2/web/model-sheet/";
export const WEB_STATUS_PATH = "/v2/web/publish-status/";

export interface ModelSheetRead {
  model_id: string;
  /** The stored file, as JSON (unvalidated: `valid` and `errors` say whether it passes the schema). */
  sheet: Record<string, unknown>;
  sha: string;
  html_url: string;
  valid: boolean;
  errors: SchemaError[];
}

/** `null` is a model with no sheet yet: the editor starts a new one. */
export async function fetchModelSheet(modelId: string): Promise<ModelSheetRead | null> {
  try {
    return await fetchJsonGet<ModelSheetRead>(operatorApiUrl(`${WEB_SHEET_PATH}${encodeURIComponent(modelId)}`));
  } catch (err) {
    // Only the API's own «no such sheet» is a new sheet; any other 404 means publishing is switched off.
    if (err instanceof OperatorApiError && err.status === 404 && /sheet_not_found/.test(err.message)) return null;
    throw err;
  }
}

export type PublishState = "queued" | "running" | "waiting" | "success" | "failure" | "unknown";

export interface PublishStatus {
  status: PublishState;
  run_url: string | null;
  updated_at: string | null;
  head_sha: string | null;
}

export const fetchPublishStatus = (modelId: string) =>
  fetchJsonGet<PublishStatus>(operatorApiUrl(`${WEB_STATUS_PATH}${encodeURIComponent(modelId)}`));

export interface PublishReceipt {
  outcome: "dispatched";
  run_url: string;
  commit_sha: string;
}

export async function publishModelSheet(body: { model_id: string; sheet: Record<string, unknown> }): Promise<PublishReceipt> {
  const res = await fetch(operatorApiUrl(WEB_PUBLISH_PATH), {
    method: "POST",
    credentials: "include",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    notifyIfSessionRefused(res.status);
    const text = await res.text().catch(() => "");
    throw new OperatorApiError(text || res.statusText || `HTTP ${res.status}`, res.status);
  }
  return (await res.json()) as PublishReceipt;
}

export interface PublishRefusal {
  status: number;
  /** `sheet_invalid`, `slug_mismatch`, `publish_rate_limited`, `commit_failed`, `dispatch_failed`, … */
  code: string;
  errors: SchemaError[];
  commitSha: string | null;
}

function asErrors(v: unknown): SchemaError[] {
  if (!Array.isArray(v)) return [];
  return v
    .filter((e): e is Record<string, unknown> => !!e && typeof e === "object")
    .map((e) => ({ path: typeof e.path === "string" ? e.path : "", message: typeof e.message === "string" ? e.message : "" }));
}

/** What a failed publish says, wherever the envelope put it (`detail`, `error.details` or the body itself). */
export function publishRefusal(err: unknown): PublishRefusal | null {
  if (!(err instanceof OperatorApiError)) return null;
  const base = refusalOfError(err);
  let body: Record<string, unknown> = {};
  try {
    const parsed = JSON.parse(err.message);
    if (parsed && typeof parsed === "object") body = parsed as Record<string, unknown>;
  } catch {
    /* not JSON */
  }
  const nested = [body.detail, (body.error as Record<string, unknown> | undefined)?.details, body.error, body]
    .filter((p): p is Record<string, unknown> => !!p && typeof p === "object" && !Array.isArray(p));
  const part = nested.find((p) => Array.isArray(p.errors) || typeof p.code === "string" || typeof p.commit_sha === "string") ?? {};
  const code = typeof part.code === "string" ? part.code : (base?.code ?? `http_${err.status}`);
  return {
    status: err.status,
    code,
    errors: asErrors(part.errors),
    commitSha: typeof part.commit_sha === "string" ? part.commit_sha : null,
  };
}
