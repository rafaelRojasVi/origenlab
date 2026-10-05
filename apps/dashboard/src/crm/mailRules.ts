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
  undone: boolean;
  undone_at: string | null;
  undone_by: string | null;
  undo_note: string | null;
}

export interface MailRulesPreview {
  label: string;
  commands_enabled: boolean;
  emails_considered: number;
  already_applied: number;
  counts: Record<string, Partial<Record<MailRuleMode, number>>>;
  actions: PlannedMailAction[];
  applied: AppliedMailAction[];
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
