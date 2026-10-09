/**
 * Mail triage review (`apps/api` v2/triage_review.py): the worker's suggestions for the mail a
 * person wrote, and a person's verdict on each — approve, correct or reject, with an optional note.
 * Every verdict is kept (append-only) so the triage can be measured and improved.
 *
 * «Aprobar» on a stage suggestion also moves the case, through the same `advance-case-stage`
 * steps as «Cambiar estado» (`moveCase`): the verdict is recorded first, the move after, each with
 * its own receipt.
 */
import { fetchJsonGet, operatorApiUrl } from "../api/operatorClient";
import { STAGE_LABEL } from "./stage";
import { stagePath } from "./caseCommands";

/** The queue (a read). The verdict is posted by `mailRules.ts`, the Revisión mail tools' one write client. */
export const TRIAGE_PATHS = {
  readings: "/v2/workspace/triage-readings",
} as const;

export type TriageVerdict = "approved" | "corrected" | "rejected";
export type TriageStatus = "pending" | "reviewed" | "all";

export interface TriageProduct {
  description: string;
  brand?: string | null;
  model: string | null;
  quantity: number | null;
  catalog_product_id: string | null;
}

export interface TriageCase {
  opportunity_id: string;
  title: string | null;
  stage: string;
  version: number;
}

export interface TriageReview {
  verdict: TriageVerdict;
  corrected: TriageCorrection;
  note: string | null;
  reviewed_at: string;
  reviewed_by: string | null;
}

export interface TriageCorrection {
  class?: string;
  stage?: string;
  intent?: string;
  products?: TriageProduct[];
}

export interface TriageReading {
  assertion_id: string;
  source_record_id: string;
  version: string;
  subject: string | null;
  sender: string | null;
  sent_at: string | null;
  thread_id: string | null;
  class: string | null;
  reasons: string[];
  model_state: string | null;
  stage: string | null;
  intent: string | null;
  urgency: string | null;
  summary_es: string | null;
  needs_reply: boolean | null;
  requester_organization: string | null;
  products: TriageProduct[];
  candidates: { product_id: string; model_number: string | null; how: string }[];
  cases: TriageCase[];
  transitions: { opportunity_id: string; current_stage: string; transition_allowed: boolean }[];
  review: TriageReview | null;
}

export interface TriageReadings {
  status: TriageStatus;
  items: TriageReading[];
  vocabulary?: { classes: string[]; stages: string[]; intents: string[] };
}

export const CLASS_LABEL: Record<string, string> = {
  purchase_order: "Orden de compra",
  lost: "Perdida (otro proveedor)",
  quote_followup: "Seguimiento de cotización",
  quote_request: "Solicitud de cotización",
  business_other: "Otro (persona)",
  tender_notice: "Licitación",
  outbound: "Enviado por OrigenLab",
  bounce: "Rebote",
  auto_reply: "Respuesta automática",
  calendar: "Invitación",
  unsubscribe: "Baja",
  bulk: "Masivo",
  notification: "Notificación",
  empty: "Vacío",
};

export const INTENT_LABEL: Record<string, string> = {
  quote_request: "Pide cotización",
  purchase_order: "Envía orden de compra",
  quote_followup: "Pregunta por una cotización",
  negotiation: "Negocia",
  lost: "Compró a otro / cancela",
  technical_question: "Consulta técnica",
  service_or_repair: "Servicio o reparación",
  supplier_offer: "Proveedor ofrece o cotiza",
  logistics: "Logística",
  administrative: "Administrativo",
  not_commercial: "No comercial",
};

/** A suggested stage in the board's words; `not_a_case` and `unclear` have their own. */
export function stageLabel(stage: string | null | undefined): string {
  if (!stage) return "—";
  if (stage === "not_a_case") return "No es un caso";
  if (stage === "unclear") return "No se sabe";
  return STAGE_LABEL[stage] ?? stage;
}

/**
 * The case «Aprobar» would move, and how: the one case the thread is on, when the suggested stage
 * differs from its current one and the board can walk there (`stagePath`; «Ganada» is never walked
 * — it needs «Marcar ganada» with its revision). Null when approving moves nothing.
 */
export function approvalMove(reading: Pick<TriageReading, "stage" | "cases">): { case: TriageCase; to: string } | null {
  const to = reading.stage;
  if (!to || to === "not_a_case" || to === "unclear" || reading.cases.length !== 1) return null;
  const only = reading.cases[0];
  if (only.stage === to) return null;
  return stagePath(only.stage, to) ? { case: only, to } : null;
}

export const fetchTriageReadings = (status: TriageStatus = "pending") =>
  fetchJsonGet<TriageReadings>(operatorApiUrl(TRIAGE_PATHS.readings, { status, limit: 100 }));

export type { ReviewTriageInput } from "./mailRules";
export { reviewTriage } from "./mailRules";
