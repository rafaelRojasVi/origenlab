/**
 * The two commercial-case commands the case drawer may send: «Cambiar etapa»
 * (`advance-case-stage`) and «Marcar ganada» (`record-case-won`).
 *
 * This module owns both `/v2/commands/<case command>` path strings — pinned by
 * `src/test/noWritePolicy.test.ts`. No other dashboard module may name them, and no other case
 * command (opening a case, linking evidence, naming an institution, recording an interest) is
 * reachable from the browser: the Worker refuses them (`CASE_COMMAND_POST_PATHS`).
 *
 * Upstream each command needs an active `sales` or `admin` operator (from the session, never the
 * body), an `Idempotency-Key`, the case version the operator was shown and a note; both mount
 * only behind `ORIGENLAB_V2_COMMANDS_ENABLED`, which `/auth/session` reports as
 * `case_commands_enabled`.
 */
import type { AuthSessionState } from "../api/authClient";
import { OperatorApiError, notifyIfSessionRefused, operatorApiUrl } from "../api/operatorClient";
import { useAuthSession } from "../context/AuthSessionContext";
import type { OpportunityCardData, RevisionCard } from "./crmTypes";
import { STAGE_LABEL } from "./stage";

export const CASE_COMMAND_PATHS = {
  advanceStage: "/v2/commands/advance-case-stage",
  recordWon: "/v2/commands/record-case-won",
} as const;

/** `case_commands.py` STAGE_TRANSITIONS (WORKFLOWS.md §1.1). The API refuses any other move. */
export const STAGE_TRANSITIONS: Record<string, readonly string[]> = {
  lead: ["qualifying", "abandoned", "lost"],
  qualifying: ["qualified", "lead", "abandoned", "lost"],
  qualified: ["quoting", "qualifying", "abandoned", "lost"],
  quoting: ["negotiating", "qualified", "abandoned", "lost"],
  negotiating: ["won", "lost", "quoting", "abandoned"],
  won: [],
  lost: [],
  abandoned: [],
};

export const CLOSING_STAGES: ReadonlySet<string> = new Set(["lost", "abandoned"]);
export const TERMINAL_STAGES: ReadonlySet<string> = new Set(["won", "lost", "abandoned"]);

/**
 * The moves «Cambiar etapa» offers from `stage`. `won` is never one of them: a win names a quote
 * revision, which is «Marcar ganada».
 */
export function nextStages(stage: string): string[] {
  return (STAGE_TRANSITIONS[stage] ?? []).filter((s) => s !== "won");
}

/** Stages from which «Marcar ganada» is offered: `negotiating`, or `quoting` in two steps. */
export function mayBeWonFrom(stage: string): boolean {
  return stage === "negotiating" || stage === "quoting";
}

/** A revision a case can be won against: sent, never replaced. Newest first. */
export function winnableRevisions(card: OpportunityCardData): (RevisionCard & { quote_id: string })[] {
  const out: (RevisionCard & { quote_id: string })[] = [];
  for (const q of card.quotes) {
    for (const r of q.revisions) {
      if (r.status === "sent" && r.superseded_by_revision_no == null) out.push({ ...r, quote_id: q.quote_id });
    }
  }
  return out.sort((a, b) => (b.sent_at ?? "").localeCompare(a.sent_at ?? "") || b.revision_no - a.revision_no);
}

/* ── gating ───────────────────────────────────────────────────────────────── */

export const ROLES_THAT_DECIDE_CASES: ReadonlySet<string> = new Set(["sales", "admin"]);

/** Fails closed: a viewer, an unknown role or an API without the case commands gets none. */
export function mayRunCaseCommands(session: AuthSessionState): boolean {
  return (
    session.kind === "signed_in" &&
    session.caseCommandsEnabled === true &&
    ROLES_THAT_DECIDE_CASES.has(session.operator.role)
  );
}

export function useMayRunCaseCommands(): boolean {
  return mayRunCaseCommands(useAuthSession().session);
}

/* ── commands ─────────────────────────────────────────────────────────────── */

export interface CaseCommandReceipt {
  command: string;
  opportunity_id: string;
  opportunity_version: number;
  stage: string;
  previous_stage?: string;
  idempotency_key: string;
  command_receipt_id: string;
  replayed: boolean;
  [key: string]: unknown;
}

export function newCaseCommandKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `case-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

async function postCaseCommand(path: string, body: unknown, idempotencyKey: string): Promise<CaseCommandReceipt> {
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
  return res.json() as Promise<CaseCommandReceipt>;
}

export interface AdvanceCaseStageBody {
  opportunity_id: string;
  opportunity_version: number;
  stage: string;
  close_reason?: string | null;
  note: string;
}

export const advanceCaseStage = (body: AdvanceCaseStageBody, idempotencyKey: string = newCaseCommandKey()) =>
  postCaseCommand(CASE_COMMAND_PATHS.advanceStage, body, idempotencyKey);

export interface RecordCaseWonBody {
  opportunity_id: string;
  opportunity_version: number;
  quote_id: string;
  revision_no: number;
  note: string;
}

export const recordCaseWon = (body: RecordCaseWonBody, idempotencyKey: string = newCaseCommandKey()) =>
  postCaseCommand(CASE_COMMAND_PATHS.recordWon, body, idempotencyKey);

/** One step of «Marcar ganada», as the operator is told about it. */
export interface WonStep {
  label: string;
  receipt: CaseCommandReceipt;
}

export class WonFlowError extends Error {
  constructor(
    public readonly done: WonStep[],
    public readonly cause: unknown,
  ) {
    super("won flow refused");
    this.name = "WonFlowError";
  }
}

/**
 * «Marcar ganada». From `negotiating` it is one command. From `quoting` it is two, each with its
 * own receipt: `advance-case-stage` → `negotiating`, then `record-case-won` against the version
 * the first one returned. If the second is refused the first stays recorded — the case is at
 * `negotiating` — and `WonFlowError.done` says so, so the drawer can tell the truth.
 */
export async function markCaseWon(
  card: Pick<OpportunityCardData, "opportunity_id" | "stage"> & { version: number },
  revision: { quote_id: string; revision_no: number },
  note: string,
): Promise<WonStep[]> {
  const done: WonStep[] = [];
  let version = card.version;
  try {
    if (card.stage === "quoting") {
      const moved = await advanceCaseStage({
        opportunity_id: card.opportunity_id,
        opportunity_version: version,
        stage: "negotiating",
        note,
      });
      done.push({ label: `Etapa → ${STAGE_LABEL.negotiating}`, receipt: moved });
      version = moved.opportunity_version;
    }
    const won = await recordCaseWon({
      opportunity_id: card.opportunity_id,
      opportunity_version: version,
      quote_id: revision.quote_id,
      revision_no: revision.revision_no,
      note,
    });
    done.push({ label: STAGE_LABEL.won, receipt: won });
    return done;
  } catch (err) {
    throw new WonFlowError(done, err);
  }
}

/* ── refusals, in Spanish ─────────────────────────────────────────────────── */

const REFUSAL_ES: Record<string, string> = {
  case_version_conflict: "Otra persona cambió este caso mientras lo veías. Se recargó: revisa y vuelve a intentarlo.",
  case_is_closed: "El caso ya está cerrado; un caso cerrado no se reabre.",
  case_is_already_at_that_stage: "El caso ya está en esa etapa.",
  stage_transition_not_allowed: "Ese cambio de etapa no está permitido desde la etapa actual.",
  stage_requires_a_confirmed_requesting_institution:
    "Para esa etapa el caso necesita una institución solicitante confirmada por una persona.",
  closing_a_case_needs_a_motive: "Para cerrar el caso como perdido o abandonado escribe el motivo.",
  close_reason_is_only_for_a_closing_stage: "El motivo de cierre sólo se usa al cerrar el caso.",
  won_requires_a_quote: "Un caso se gana contra una revisión de cotización: usa «Marcar ganada».",
  case_not_negotiating: "Un caso se marca ganado desde «Negociando».",
  quote_revision_not_on_case: "Esa revisión no pertenece a este caso.",
  quote_revision_not_current: "Sólo se gana contra una revisión enviada y vigente (no reemplazada).",
  case_not_found: "El caso ya no existe.",
  role_may_not_decide: "Tu perfil no puede decidir casos; se necesita ventas o administración.",
  idempotency_key_reused: "La solicitud se repitió con otro contenido; vuelve a intentarlo.",
  command_in_progress: "La misma acción todavía se está registrando; espera un momento.",
  stale_version: "Otra persona modificó esta institución; se recargó: vuelve a intentarlo.",
  archived_subject: "La institución está archivada.",
  organization_merged: "La institución fue fusionada con otra; ábrela desde su ficha.",
  path_not_allowed: "Esta acción no está habilitada en este entorno.",
};

/** The refusal code of an API or Worker error envelope (`{detail: {code}}` / `{error: {code}}`). */
export function caseRefusalCode(err: unknown): string | null {
  if (err instanceof WonFlowError) return caseRefusalCode(err.cause);
  if (!(err instanceof OperatorApiError)) return null;
  let code: string | null = err.message.includes("path_not_allowed") ? "path_not_allowed" : null;
  try {
    const parsed = JSON.parse(err.message) as { detail?: unknown; error?: { code?: unknown } };
    const d = parsed.detail;
    if (d && typeof d === "object" && "code" in d) code = String((d as { code: unknown }).code);
    else if (parsed.error && typeof parsed.error === "object" && parsed.error.code) code = String(parsed.error.code);
  } catch {
    /* not JSON */
  }
  return code;
}

/** A refusal that means "what you saw is out of date": the drawer refetches before a retry. */
export function isStaleRefusal(err: unknown): boolean {
  const code = caseRefusalCode(err);
  return code === "case_version_conflict" || code === "stale_version" || code === "case_is_closed";
}

/** The API's refusal as a sentence an operator can act on; never an English stack of codes. */
export function caseRefusalText(err: unknown): string {
  if (err instanceof WonFlowError) return caseRefusalText(err.cause);
  if (!(err instanceof OperatorApiError)) return "No se pudo registrar: revisa la conexión y vuelve a intentarlo.";
  const code = caseRefusalCode(err);
  if (code && REFUSAL_ES[code]) return REFUSAL_ES[code];
  if (err.status === 401) return "La sesión expiró; vuelve a ingresar.";
  if (err.status === 403) return "Tu perfil no puede hacer esta acción.";
  if (err.status === 404) return "Esta acción no está habilitada en este entorno.";
  if (err.status === 409) return "El caso cambió mientras lo veías; se recargó: vuelve a intentarlo.";
  if (err.status === 422) return "Faltan datos o no son válidos; revisa el formulario.";
  return code ? `No se pudo registrar (código ${code}).` : `No se pudo registrar (HTTP ${err.status}).`;
}
