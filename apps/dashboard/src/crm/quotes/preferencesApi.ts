/**
 * «Preferencias» of the Cotizador: the company's cost parameters, read through the catalog
 * (`fetchCostParameters`, `GET /v2/catalog/parameters`: each key's current row and the history)
 * and set one at a time with `set-cost-parameter` — this module's one POST site, pinned by
 * `src/test/noWritePolicy.test.ts`. Sales and admin upstream (a viewer gets 403
 * `role_may_not_view_costs`); only administration sets `quote_number_seed` (403
 * `role_may_not_set_numbering`).
 *
 * Every command carries an `Idempotency-Key` and the current row the operator saw
 * (`expected_current_id`); another row current since is a 409 `parameter_changed`. A refusal is a
 * `QuoteRefusal`, so the Cotizador's Spanish texts (`quoteRefusalText`) and its key-retention
 * rule (`definitive`) apply as they do to the quote commands.
 *
 * The API's envelope is `{error: {code, message, details: {code, message, …}}}`: the HTTP layer's
 * generic code (`conflict`, `forbidden`) is in `error.code` and the command's own in
 * `error.details.code`, which wins; `{detail: {code, message}}` (FastAPI's raw form) is read too.
 */
import { notifyIfSessionRefused, operatorApiUrl } from "../../api/operatorClient";
import { QuoteRefusal, newQuoteCommandKey, type Decimal } from "./quoteApi";
import { quoteRefusalText } from "./quoteRefusal";

export const SET_COST_PARAMETER_PATH = "/v2/commands/set-cost-parameter";

/**
 * `params` of the quote workspace (`inputs.quote_preferences`): each the current parameter, else
 * the engine's fallback (40 % equipment, 45 % the rest; a 25–45 % band). Ratios as decimal strings
 * ("0.25" = 25 %). The deviation warning has no fallback: null, nothing warns.
 */
export interface QuotePreferences {
  markups?: Partial<Record<"equipment" | "accessory" | "consumable" | "spare_part" | "service" | "other", Decimal>>;
  margin_target_min?: Decimal | null;
  margin_target_max?: Decimal | null;
  price_deviation_warn_pct?: Decimal | null;
}

export interface SetCostParameterBody {
  key: string;
  /** Numbers, as a decimal string. */
  value?: Decimal;
  /** The printed `quote_default_*` texts. */
  value_text?: string;
  reason: string;
  /** The current row shown; null when the parameter was unset. */
  expected_current_id: string | null;
}

export interface SetCostParameterResult {
  ok: true;
  cost_parameter_id: string;
  key: string;
}

type Part = { code?: unknown; message?: unknown; details?: unknown };

/** The command's code and message: `error.details` first, then `error`, then FastAPI's `detail`. */
export function refusalEnvelope(raw: unknown): { code: string | null; message: string | null; details: Record<string, unknown> | null } {
  if (!raw || typeof raw !== "object") return { code: null, message: null, details: null };
  const body = raw as { error?: unknown; detail?: unknown };
  for (const part of [body.error, body.detail]) {
    if (!part || typeof part !== "object") continue;
    const p = part as Part;
    const details = p.details && typeof p.details === "object" ? (p.details as Part & Record<string, unknown>) : null;
    const inner = details && typeof details.code === "string" && details.code ? details : null;
    const code = inner?.code ?? (typeof p.code === "string" && p.code ? p.code : null);
    if (!code) continue;
    const message = typeof inner?.message === "string" ? inner.message : typeof p.message === "string" ? p.message : null;
    return { code: String(code), message, details };
  }
  return { code: null, message: null, details: null };
}

export async function setCostParameter(
  body: SetCostParameterBody,
  idempotencyKey: string = newQuoteCommandKey(),
): Promise<SetCostParameterResult> {
  const res = await fetch(operatorApiUrl(SET_COST_PARAMETER_PATH), {
    method: "POST",
    credentials: "include",
    headers: { Accept: "application/json", "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    notifyIfSessionRefused(res.status);
    const text = await res.text().catch(() => "");
    let parsed: unknown = null;
    try {
      parsed = JSON.parse(text);
    } catch {
      /* not JSON */
    }
    const { code, message, details } = refusalEnvelope(parsed);
    throw new QuoteRefusal(res.status, code, message || text || res.statusText || `HTTP ${res.status}`, details);
  }
  return (await res.json()) as SetCostParameterResult;
}

/** Codes of `set-cost-parameter` the quote texts do not name (to join `quoteRefusal.ts` later). */
const SPANISH: Record<string, string> = {
  parameter_changed: "Otra persona lo cambió; recarga.",
  role_may_not_set_numbering: "Sólo administración fija el inicio de la numeración.",
  role_may_not_view_costs: "Sólo ventas y administración pueden cambiar las preferencias.",
  validation_error: "El servidor no aceptó el valor; revísalo y vuelve a intentarlo.",
  forbidden: "Tu rol no puede cambiar esta preferencia.",
  conflict: "Otra persona lo cambió; recarga.",
};

const FALLBACK = "No se pudo guardar. Intenta de nuevo.";

/** What to say when saving a preference failed, in Spanish; a body that is not the API's says the fallback. */
export function preferenceRefusalText(e: unknown): string {
  if (e instanceof QuoteRefusal && e.code && SPANISH[e.code]) return SPANISH[e.code];
  return quoteRefusalText(e, FALLBACK);
}
