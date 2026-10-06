/**
 * Email → cases rules (spec 2026-10-05): the dry run, apply and undo. Admin only upstream.
 *
 * «Aplicar» sends the previewed `(evidence_id, rule_id)` pairs, ten at a time; the API re-plans and
 * applies a pair only if the rules still say the same thing, so nothing the preview did not show
 * is applied. Each POST carries a fresh `Idempotency-Key`; the API keys each action's receipt
 * by (email, rule), so a second press applies nothing new.
 */
import { OperatorApiError, fetchJsonGet, notifyIfSessionRefused, operatorApiUrl } from "../api/operatorClient";

export const MAIL_RULES_PATHS = {
  preview: "/v2/workspace/mail-rules/preview",
  apply: "/v2/commands/apply-mail-rules",
  undo: "/v2/commands/undo-mail-rule-action",
  setAuto: "/v2/commands/set-auto-mail-rules",
  /** A person's verdict on one mail-triage suggestion (`apps/api` v2/triage_review.py). */
  reviewTriage: "/v2/commands/review-triage",
} as const;

export type MailRuleMode = "auto" | "proposal" | "none";

export interface PlannedMailAction {
  evidence_id: string;
  rule_id: string;
  mode: MailRuleMode;
  reasons: string[];
  case_id: string | null;
  case_title: string | null;
  organization_id: string | null;
  organization_name: string | null;
  proposed_domain: string | null;
  quote_number: string | null;
  candidates: string[];
  commands: { command: string; inputs: Record<string, unknown> }[];
  subject?: string | null;
  sent_at?: string | null;
  direction?: string | null;
}

export interface AppliedMailAction {
  receipt_id: string;
  applied_at: string | null;
  applied_by: string | null;
  label: string;
  evidence_id: string | null;
  rule_id: string | null;
  reasons: string[] | null;
  case_id: string | null;
  case_title: string | null;
  organization_id: string | null;
  organization_name: string | null;
  quote_number: string | null;
  /** Applied by the automatic run (R1/R2), not by someone pressing «Aplicar». */
  automatic?: boolean;
  undone: boolean;
  undone_at: string | null;
  undone_by: string | null;
  undo_note: string | null;
}

/** The last automatic pass this API process ran. */
export interface AutoMailRulesRun {
  at: string;
  applied: number;
  refused: number;
  pending: number;
  /** "off" | "operator_not_admin" when the pass did nothing. */
  skipped: string | null;
}

/** The automatic R1/R2 run: the switch, who set it, the timer. */
export interface AutoMailRulesState {
  enabled: boolean;
  changed_at: string | null;
  changed_by: string | null;
  note: string | null;
  rules: string[];
  interval_seconds: number;
  timer_running: boolean;
  /** Why an «on» switch is not acting (the admin who set it is no longer one). */
  blocked: string | null;
  last_run: AutoMailRulesRun | null;
}

export interface MailRulesPreview {
  label: string;
  commands_enabled: boolean;
  emails_considered: number;
  already_applied: number;
  counts: Record<string, Partial<Record<MailRuleMode, number>>>;
  actions: PlannedMailAction[];
  applied: AppliedMailAction[];
  /** Absent or null where the commands are not mounted. */
  automatic?: AutoMailRulesState | null;
}

export interface MailRulePair {
  evidence_id: string;
  rule_id: string;
}

export interface ApplyMailRulesResult {
  applied: MailRulePair[];
  refused: (MailRulePair & { code: string; message: string })[];
}

/** The API applies at most this many pairs per call; the page drives the batches. */
export const APPLY_BATCH = 10;

/** What each rule does, in the operator's words. */
export const RULE_LABEL: Record<string, string> = {
  R1: "Mismo hilo de Gmail → vincular al caso",
  R2: "Mismo número de cotización → vincular al caso",
  R3: "Caso nuevo para una institución conocida",
  R4: "Caso nuevo e institución «por confirmar»",
  R5: "Orden de compra → caso ganado",
  R6: "Otro proveedor → caso perdido",
  R7: "Solicitud de cotización → propuesta «Nuevo caso»",
  R8: "Queda en Revisión",
};

export const fetchMailRulesPreview = () => fetchJsonGet<MailRulesPreview>(operatorApiUrl(MAIL_RULES_PATHS.preview));

function newIdempotencyKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `mail-rules-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(operatorApiUrl(path), {
    method: "POST",
    credentials: "include",
    headers: { Accept: "application/json", "Content-Type": "application/json", "Idempotency-Key": newIdempotencyKey() },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    notifyIfSessionRefused(res.status);
    const text = await res.text().catch(() => "");
    throw new OperatorApiError(text || res.statusText || `HTTP ${res.status}`, res.status);
  }
  return res.json() as Promise<T>;
}

/** One batch of previewed pairs. The API re-plans and applies only pairs it still plans the same. */
export const applyMailRules = (actions: MailRulePair[]) =>
  postJson<ApplyMailRulesResult>(MAIL_RULES_PATHS.apply, { actions });

export const undoMailRuleAction = (receiptId: string, note: string) =>
  postJson<{ undoes_receipt_id: string }>(MAIL_RULES_PATHS.undo, { receipt_id: receiptId, note });

/** Switch the automatic R1/R2 run on or off; a note says why. */
export const setAutoMailRules = (enabled: boolean, note: string) =>
  postJson<{ enabled: boolean }>(MAIL_RULES_PATHS.setAuto, { enabled, note });

/** The API's `detail.message` when there is one, else a fixed sentence. */
export function refusalMessage(err: unknown, fallback: string): string {
  if (err instanceof OperatorApiError) {
    try {
      const detail = JSON.parse(err.message)?.detail;
      if (detail?.message) return String(detail.message);
      if (typeof detail === "string") return detail;
    } catch {
      /* not JSON */
    }
    if (err.status === 403) return "Sólo un perfil de administración puede aplicar o deshacer.";
    if (err.status === 404) return "Las acciones automáticas no están habilitadas en este entorno.";
  }
  return fallback;
}

/** One verdict on one mail-triage suggestion: approve, correct (with the right answer) or reject. */
export interface ReviewTriageInput {
  assertion_id: string;
  verdict: "approved" | "corrected" | "rejected";
  corrected?: object;
  note?: string;
}

export function reviewTriage(input: ReviewTriageInput): Promise<{ review_id: string }> {
  const body: Record<string, unknown> = { assertion_id: input.assertion_id, verdict: input.verdict };
  if (input.verdict === "corrected") body.corrected = input.corrected ?? {};
  if (input.note && input.note.trim()) body.note = input.note.trim();
  return postJson<{ review_id: string }>(MAIL_RULES_PATHS.reviewTriage, body);
}
