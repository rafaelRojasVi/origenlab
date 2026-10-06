/**
 * The four commercial-case commands the case drawer may send: «Cambiar estado»
 * (`advance-case-stage`), «Marcar ganada» (`record-case-won`), «Elegir revisión vigente»
 * (`resolve-current-revision`) and «Registrar cotización» / «Nueva revisión»
 * (`record-case-quotation`) — plus the three W11 task commands behind «En pausa hasta…»
 * (`create-task`), «Retomar ahora» (`cancel-task`) and «Hecho» (`complete-task`).
 *
 * This module owns all seven `/v2/commands/<…>` path strings — pinned by
 * `src/test/noWritePolicy.test.ts`. No other dashboard module may name them, and no other case
 * command (opening a case, linking evidence, naming an institution, recording an interest) is
 * reachable from the browser: the Worker refuses them (`CASE_COMMAND_POST_PATHS`).
 *
 * Upstream each command needs an active `sales` or `admin` operator (from the session, never the
 * body), an `Idempotency-Key`, the case version the operator was shown and a note; all mount
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
  resolveCurrentRevision: "/v2/commands/resolve-current-revision",
  recordQuotation: "/v2/commands/record-case-quotation",
  createTask: "/v2/commands/create-task",
  completeTask: "/v2/commands/complete-task",
  cancelTask: "/v2/commands/cancel-task",
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

/** A quote whose current revision is undetermined: two or more revisions neither void nor replaced. */
export interface UndeterminedQuote {
  quote_id: string;
  quote_number: string;
  revisions: RevisionCard[];
}

/** The quotes behind «Hay más de una revisión vigente» (`canonical_undetermined`), as the API counts them. */
export function undeterminedQuotes(card: OpportunityCardData): UndeterminedQuote[] {
  return card.quotes
    .map((q) => ({ quote_id: q.quote_id, quote_number: q.quote_number, revisions: q.revisions.filter((r) => r.is_active) }))
    .filter((q) => q.revisions.length > 1);
}

/** Stages at which a quote may be recorded on a case (`STAGES_THAT_MAY_CARRY_A_QUOTE`). */
export function mayCarryAQuote(stage: string): boolean {
  return stage === "quoting" || stage === "negotiating";
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
  stage?: string;
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

export interface ResolveCurrentRevisionBody {
  opportunity_id: string;
  opportunity_version: number;
  quote_id: string;
  revision_no: number;
  note: string;
}

/** «Elegir revisión vigente»: every other current revision of the quote is superseded by this one. */
export const resolveCurrentRevision = (body: ResolveCurrentRevisionBody, idempotencyKey: string = newCaseCommandKey()) =>
  postCaseCommand(CASE_COMMAND_PATHS.resolveCurrentRevision, body, idempotencyKey);

export interface RecordCaseQuotationBody {
  opportunity_id: string;
  opportunity_version: number;
  quote_number: string;
  source_record_id: string;
  document_sha256: string;
  /** «Nueva revisión»: the revision of the quote with this number that the document replaces. */
  supersedes_revision_no?: number | null;
  note: string;
}

/** «Registrar cotización» / «Nueva revisión»: a quote already sent, from a Gmail message linked to the case. */
export const recordCaseQuotation = (body: RecordCaseQuotationBody, idempotencyKey: string = newCaseCommandKey()) =>
  postCaseCommand(CASE_COMMAND_PATHS.recordQuotation, body, idempotencyKey);

/* ── tasks (W11): «En pausa hasta…», «Retomar ahora» ───────────────────────── */

export interface CreateTaskBody {
  opportunity_id: string;
  title: string;
  /** ISO 8601 with a zone; the API refuses a naive one. */
  due_at: string;
  note: string;
}

export interface TaskCommandBody {
  task_id: string;
  task_version: number;
  note: string;
}

export const createTask = (body: CreateTaskBody, idempotencyKey: string = newCaseCommandKey()) =>
  postCaseCommand(CASE_COMMAND_PATHS.createTask, body, idempotencyKey);

export const completeTask = (body: TaskCommandBody, idempotencyKey: string = newCaseCommandKey()) =>
  postCaseCommand(CASE_COMMAND_PATHS.completeTask, body, idempotencyKey);

export const cancelTask = (body: TaskCommandBody, idempotencyKey: string = newCaseCommandKey()) =>
  postCaseCommand(CASE_COMMAND_PATHS.cancelTask, body, idempotencyKey);

/* ── multi-step flows ──────────────────────────────────────────────────────── */

/** One recorded step of a flow, as the operator is told about it. */
export interface WonStep {
  label: string;
  receipt: CaseCommandReceipt;
}

/** A flow refused part-way: `done` are the steps that stayed recorded. */
export class WonFlowError extends Error {
  constructor(
    public readonly done: WonStep[],
    public readonly cause: unknown,
  ) {
    super("case flow refused");
    this.name = "WonFlowError";
  }
}

/**
 * The stages a case walks through to get from `from` to `to`, one `advance-case-stage` each
 * (shortest path over `STAGE_TRANSITIONS`, never through a terminal stage). `[]` when it is
 * already there; `null` when no path exists (a closed case, or `won`, which is «Marcar ganada»).
 */
export function stagePath(from: string, to: string): string[] | null {
  if (from === to) return [];
  if (to === "won" || TERMINAL_STAGES.has(from)) return null;
  const prev = new Map<string, string>([[from, ""]]);
  const queue = [from];
  while (queue.length) {
    const at = queue.shift() as string;
    for (const next of STAGE_TRANSITIONS[at] ?? []) {
      if (next === "won" || prev.has(next)) continue;
      prev.set(next, at);
      if (next === to) {
        const path = [next];
        for (let p = at; p !== from; p = prev.get(p) as string) path.unshift(p);
        return path;
      }
      if (!TERMINAL_STAGES.has(next)) queue.push(next);
    }
  }
  return null;
}

/**
 * Move a case to `to` in as many `advance-case-stage` steps as `stagePath` needs, each against
 * the version the previous one returned. `keys[i]` is step i's key, kept across a retry so a
 * step is never recorded twice. A refusal part-way leaves the earlier steps recorded and says
 * so in `WonFlowError.done`.
 */
export async function moveCase(
  card: Pick<OpportunityCardData, "opportunity_id" | "stage"> & { version: number },
  to: string,
  note: string,
  closeReason: string | null,
  keys: string[] = [],
): Promise<WonStep[]> {
  const path = stagePath(card.stage, to);
  if (path === null) throw new WonFlowError([], new Error("no stage path"));
  const done: WonStep[] = [];
  let version = card.version;
  try {
    for (const [i, stage] of path.entries()) {
      const closing = CLOSING_STAGES.has(stage);
      const receipt = await advanceCaseStage(
        {
          opportunity_id: card.opportunity_id,
          opportunity_version: version,
          stage,
          close_reason: closing ? closeReason : null,
          note,
        },
        keys[i],
      );
      done.push({ label: `Estado → ${STAGE_LABEL[stage] ?? stage}`, receipt });
      version = receipt.opportunity_version;
    }
    return done;
  } catch (err) {
    throw new WonFlowError(done, err);
  }
}

/** «Retomar ahora»: cancel each open task that keeps the case paused, one key per task. */
export async function resumeCase(
  tasks: { task_id: string; version: number }[],
  note: string,
  keys: Record<string, string> = {},
): Promise<WonStep[]> {
  const done: WonStep[] = [];
  try {
    for (const t of tasks) {
      const receipt = await cancelTask({ task_id: t.task_id, task_version: t.version, note }, keys[t.task_id]);
      done.push({ label: "Pausa retomada", receipt });
    }
    return done;
  } catch (err) {
    throw new WonFlowError(done, err);
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
  /** The form's keys, kept across a retry so a step is never recorded twice. */
  keys?: { advance: string; won: string },
): Promise<WonStep[]> {
  const done: WonStep[] = [];
  let version = card.version;
  try {
    if (card.stage === "quoting") {
      const moved = await advanceCaseStage(
        {
          opportunity_id: card.opportunity_id,
          opportunity_version: version,
          stage: "negotiating",
          note,
        },
        keys?.advance,
      );
      done.push({ label: `Etapa → ${STAGE_LABEL.negotiating}`, receipt: moved });
      version = moved.opportunity_version;
    }
    const won = await recordCaseWon(
      {
        opportunity_id: card.opportunity_id,
        opportunity_version: version,
        quote_id: revision.quote_id,
        revision_no: revision.revision_no,
        note,
      },
      keys?.won,
    );
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
  case_not_negotiating: "Un caso se marca ganado desde «Conversación».",
  quote_revision_not_on_case: "Esa revisión no pertenece a este caso.",
  quote_revision_not_current: "Sólo se gana contra una revisión enviada y vigente (no reemplazada).",
  case_not_found: "El caso ya no existe.",
  quote_not_on_case: "Esa cotización no pertenece a este caso.",
  nothing_to_resolve: "Esa revisión ya es la única vigente de la cotización.",
  revision_cannot_be_superseded:
    "Otra revisión vigente no puede quedar reemplazada por la elegida (un borrador, o una revisión posterior creada en el sistema).",
  message_not_on_case: "Ese correo no está vinculado a este caso.",
  not_a_gmail_message: "La cotización se registra desde el correo de Gmail que la envió.",
  source_record_does_not_carry_document: "Ese correo no trae ese documento.",
  message_has_no_sent_at: "Ese correo no tiene fecha de envío; no sirve como evidencia del envío.",
  number_on_other_opportunity: "Ese número de cotización ya pertenece a otro caso.",
  number_is_a_minted_quote: "Ese número lo generó el sistema para otra cotización; no se puede reutilizar.",
  document_already_recorded: "Ese documento ya está registrado como revisión de una cotización.",
  case_not_quoting: "Una cotización se registra en un caso «Enviada» o en «Conversación».",
  case_has_no_requesting_institution: "El caso necesita una institución solicitante confirmada para registrar una cotización.",
  superseded_revision_not_on_case: "La revisión a reemplazar no pertenece a esa cotización de este caso.",
  superseded_document_not_on_this_quote: "La revisión a reemplazar no pertenece a esa cotización.",
  already_superseded: "La revisión a reemplazar ya fue reemplazada por otra.",
  superseded_revision_not_sent: "Sólo se reemplaza una revisión enviada.",
  source_record_quarantined: "Ese correo está en cuarentena.",
  role_may_not_decide: "Tu perfil no puede decidir casos; se necesita ventas o administración.",
  idempotency_key_reused: "La solicitud se repitió con otro contenido; vuelve a intentarlo.",
  command_in_progress: "La misma acción todavía se está registrando; espera un momento.",
  stale_version: "Otra persona modificó esta institución; se recargó: vuelve a intentarlo.",
  archived_subject: "La institución está archivada.",
  organization_merged: "La institución fue fusionada con otra; ábrela desde su ficha.",
  path_not_allowed: "Esta acción no está habilitada en este entorno.",
  task_not_found: "Esa tarea ya no existe.",
  task_version_conflict: "Otra persona cambió esa tarea mientras la veías. Se recargó: vuelve a intentarlo.",
  task_is_not_open: "Esa tarea ya estaba cerrada.",
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
  return (
    code === "case_version_conflict" ||
    code === "stale_version" ||
    code === "case_is_closed" ||
    code === "task_version_conflict" ||
    code === "task_is_not_open"
  );
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
