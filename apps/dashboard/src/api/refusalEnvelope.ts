/**
 * The refusal envelope of the API's commands, read in one place for every screen.
 *
 * The API's error handler (apps/api `errors.py`) wraps every refusal as
 * `{error:{code:<generic: forbidden, conflict, not_found, validation_error…>, message,
 * details:{code, message, …details}, request_id}}`: the command's own code and message are in
 * `details`, and the generic code is only a fallback (a body validation error, a route that does not
 * exist, the Worker's `{error:{code,message}}`). An older `{detail:{code, message, …details}}` is read
 * too. The details kept (`candidates`, `validation_errors`, `next_allowed_at`…) leave out the code,
 * the message and the request id.
 */
import { OperatorApiError } from "./operatorClient";

export interface RefusalEnvelope {
  code: string | null;
  message: string | null;
  details: Record<string, unknown> | null;
}

const NOT_DETAILS = new Set(["code", "message", "details", "request_id"]);

/** The keys of `objs` that are details (not the code, the message or the request's id). */
function detailsOf(...objs: unknown[]): Record<string, unknown> | null {
  const out: Record<string, unknown> = {};
  for (const obj of objs) {
    if (!obj || typeof obj !== "object" || Array.isArray(obj)) continue;
    for (const [k, v] of Object.entries(obj as Record<string, unknown>)) if (!NOT_DETAILS.has(k)) out[k] = v;
  }
  return Object.keys(out).length > 0 ? out : null;
}

/** The refusal's own code and message: `error.details.code`, else `error.code`, else `detail.code`. */
export function parseRefusalEnvelope(raw: unknown): RefusalEnvelope {
  if (!raw || typeof raw !== "object") return { code: null, message: null, details: null };
  const body = raw as { detail?: unknown; error?: unknown };
  for (const part of [body.error, body.detail]) {
    if (!part || typeof part !== "object") continue;
    const { code, message, details } = part as { code?: unknown; message?: unknown; details?: unknown };
    const inner = details && typeof details === "object" ? (details as { code?: unknown; message?: unknown }) : null;
    if (inner && typeof inner.code === "string" && inner.code) {
      const said = typeof inner.message === "string" ? inner.message : typeof message === "string" ? message : null;
      return { code: inner.code, message: said, details: detailsOf(inner) };
    }
    if (code) {
      return { code: String(code), message: typeof message === "string" ? message : null, details: detailsOf(part, inner) };
    }
  }
  return { code: null, message: null, details: null };
}

/** The envelope inside an `OperatorApiError` (its message is the response's text); none for anything else. */
export function envelopeOfError(err: unknown): RefusalEnvelope {
  if (!(err instanceof OperatorApiError)) return { code: null, message: null, details: null };
  try {
    return parseRefusalEnvelope(JSON.parse(err.message));
  } catch {
    return { code: null, message: null, details: null };
  }
}

/**
 * The refusal an `OperatorApiError` carries, as the CRM screens show it: the envelope's code and
 * message (`parseRefusalEnvelope`) and the HTTP status. The Worker's `path_not_allowed` is
 * recognised in any shape; a body that is no envelope is `http_<status>` with its text (an older
 * `{detail: "text"}`, its text). Null for an error that is not the API's.
 */
export function refusalOfError(err: unknown): { code: string; message: string; status: number } | null {
  if (!(err instanceof OperatorApiError)) return null;
  const status = err.status;
  if (err.message.includes("path_not_allowed")) return { code: "path_not_allowed", message: err.message, status };
  const env = envelopeOfError(err);
  if (env.code) return { code: env.code, message: env.message ?? err.message, status };
  try {
    const d = (JSON.parse(err.message) as { detail?: unknown })?.detail;
    if (typeof d === "string") return { code: `http_${status}`, message: d, status };
  } catch {
    /* not JSON */
  }
  return { code: `http_${status}`, message: err.message, status };
}

/**
 * The refusal says this environment has no such command: the Worker's `path_not_allowed`, a route
 * the API lacks (its generic 404, or a 404 that is no envelope), or a 405. A command's own 404
 * (`campaign_not_found`: what it looked for is gone) is not.
 */
export function commandMissing(r: { code: string; status: number } | null): boolean {
  if (!r) return false;
  return r.code === "path_not_allowed" || r.status === 405 || (r.status === 404 && (r.code === "not_found" || r.code === "http_404"));
}
