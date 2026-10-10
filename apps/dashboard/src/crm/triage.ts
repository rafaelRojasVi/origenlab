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
import type { OpportunityCardData, RevisionCard } from "./crmTypes";
import { STAGE_LABEL } from "./stage";
import { mayBeWonFrom, stagePath, winnableRevisions } from "./caseCommands";

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
  /** When the case ended, for a closed one; absent from an older API. */
  closed_at?: string | null;
  close_reason?: string | null;
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
  /** The sender's domain belongs to a registered supplier or manufacturer. */
  sender_is_supplier?: boolean;
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

/**
 * A purchase order the triage read on a case that can still be won: the «¿Marcar ganada?» row on
 * «Hoy». The reading must be a purchase order (class or intent), the thread on exactly one case,
 * that case on the board at «Cotizando» or «Conversación», and the case holding at least one sent,
 * unreplaced revision to win against. One revision: Aceptar wins against it. Several: the row asks
 * which (owner decision 2026-10-10). A won or closed case, or a case with nothing sent, is counted
 * «en un caso» as before — nothing is proposed that the API would refuse.
 */
export interface WonProposal {
  case: TriageCase;
  card: OpportunityCardData;
  revisions: (RevisionCard & { quote_id: string })[];
}

export function isPurchaseOrder(r: Pick<TriageReading, "class" | "intent">): boolean {
  return r.class === "purchase_order" || r.intent === "purchase_order";
}

export function wonProposal(
  r: Pick<TriageReading, "class" | "intent" | "cases">,
  cards: ReadonlyMap<string, OpportunityCardData>,
): WonProposal | null {
  if (!isPurchaseOrder(r) || r.cases.length !== 1) return null;
  const only = r.cases[0];
  const card = cards.get(only.opportunity_id);
  if (!card || !mayBeWonFrom(card.stage) || card.closed_at) return null;
  const revisions = winnableRevisions(card);
  if (revisions.length === 0) return null;
  return { case: { ...only, stage: card.stage, version: card.version ?? only.version }, card, revisions };
}

/**
 * Days after a case closed during which a reply on its thread is the same deal coming back
 * (owner decision 2026-10-10): the row asks «¿Reabrir?» and the new case opens at «Conversación».
 * Older: the row still offers to reopen, but the new case opens at «Solicitada».
 */
export const REOPEN_WINDOW_DAYS = 90;

export interface ReopenProposal {
  /** The closed case the thread belongs to (the most recently closed, if several). */
  case: TriageCase;
  closedAt: string;
  daysSinceClose: number;
  /** Within the window and the case had a confirmed requester: «Conversación»; else «Solicitada». */
  stage: "negotiating" | "lead";
  card: OpportunityCardData | null;
}

const CLOSED_STAGES = new Set(["lost", "abandoned"]);

/**
 * A person wrote on the thread of a case that ended «Perdida» or «Perdida · sin respuesta»:
 * the «¿Reabrir?» row on «Hoy». Every case on the thread must be closed (an open one holds the
 * email already) and none won (a won case's client is answered from «Te toca responder»). The
 * target stage follows the window and whether the closed case recorded who was asking — the
 * API refuses «Conversación» without that, so nothing is proposed it would refuse.
 */
export function reopenProposal(
  r: Pick<TriageReading, "class" | "intent" | "cases" | "sender">,
  cards: ReadonlyMap<string, OpportunityCardData>,
  now: Date = new Date(),
): ReopenProposal | null {
  if (r.cases.length === 0 || AUTOMATIC_CLASSES.has(r.class ?? "") || r.intent === "supplier_offer") return null;
  const closed = r.cases.map((c) => {
    const card = cards.get(c.opportunity_id) ?? null;
    const stage = card?.stage ?? c.stage;
    const closedAt = card?.closed_at ?? c.closed_at ?? null;
    return { c, card, stage, closedAt };
  });
  if (closed.some((x) => !CLOSED_STAGES.has(x.stage) || !x.closedAt)) return null;
  const latest = closed.sort((a, b) => (b.closedAt as string).localeCompare(a.closedAt as string))[0];
  const closedAt = latest.closedAt as string;
  const days = Math.max(0, Math.floor((now.getTime() - Date.parse(closedAt)) / 86_400_000));
  const requester = latest.card
    ? latest.card.organization !== null && latest.card.requesting_institution_confirmation === "confirmed"
    : true; // without the board's cards, trust the API: it refuses «Conversación» without a requester
  return {
    case: { ...latest.c, stage: latest.stage, closed_at: closedAt },
    closedAt,
    daysSinceClose: days,
    stage: days <= REOPEN_WINDOW_DAYS && requester ? "negotiating" : "lead",
    card: latest.card,
  };
}

/** A quote request a person wrote that no case holds: the row offers «Abrir caso». */
export function isOpenableRequest(r: Pick<TriageReading, "class" | "intent" | "cases">): boolean {
  return r.cases.length === 0 && (r.class === "quote_request" || r.intent === "quote_request");
}

const REPLY_PREFIX = /^\s*(?:(?:re|rv|fw|fwd|tr|aw|wg|sv)\s*:\s*)+/i;

/**
 * The title «Abrir caso» gives the case: the subject without its reply/forward prefixes, or the
 * sender's domain when the subject is empty. At most 400 characters, the API's limit.
 */
export function openCaseTitle(r: Pick<TriageReading, "subject" | "sender">): string {
  const subject = (r.subject ?? "").replace(REPLY_PREFIX, "").trim();
  if (subject) return subject.slice(0, 400);
  const domain = (r.sender ?? "").split("@")[1]?.trim();
  return domain ? `Solicitud por correo · ${domain}`.slice(0, 400) : "Solicitud por correo";
}

export function cardsById(cards: readonly OpportunityCardData[]): Map<string, OpportunityCardData> {
  return new Map(cards.map((c) => [c.opportunity_id, c]));
}

/** Why an email is not asked about on «Hoy». */
export type HiddenReason = "en un caso" | "proveedor" | "aviso automático" | "reenvío antiguo" | "mismo hilo";

const AUTOMATIC_SENDER = /^(no-?reply|do-?not-?reply|mensajeria|newsletter|news|marketing|notifications?|info|customer\.assistance)$/;
const AUTOMATIC_CLASSES = new Set(["bulk", "notification", "auto_reply", "bounce", "calendar", "unsubscribe", "empty", "outbound"]);

function hiddenReason(r: TriageReading, cards: ReadonlyMap<string, OpportunityCardData>, now: Date): Exclude<HiddenReason, "mismo hilo"> | null {
  if (r.cases.length > 0) return wonProposal(r, cards) || reopenProposal(r, cards, now) ? null : "en un caso";
  const [local = "", domain = ""] = (r.sender ?? "").toLowerCase().split("@");
  if (domain.includes("labdelivery")) return "reenvío antiguo";
  if (r.sender_is_supplier || r.intent === "supplier_offer") return "proveedor";
  if (AUTOMATIC_SENDER.test(local) || AUTOMATIC_CLASSES.has(r.class ?? "")) return "aviso automático";
  return null;
}

/**
 * The emails «Hoy» asks about: written by a person, on no case, not from a supplier, not an
 * automatic notice and not a forward from the old Labdelivery mailbox — one per thread, the
 * newest — plus a purchase order on a case that can be won (`wonProposal`), which asks «¿Marcar
 * ganada?» instead of being counted «en un caso», and a person's reply on the thread of a closed
 * case (`reopenProposal`), which asks «¿Reabrir?». Proposals come first, then the rest newest
 * first. What is not asked about is only counted, by reason.
 */
export function sortInbox(
  items: TriageReading[],
  cards: readonly OpportunityCardData[] | ReadonlyMap<string, OpportunityCardData> = [],
  now: Date = new Date(),
): { ask: TriageReading[]; hidden: Partial<Record<HiddenReason, number>> } {
  const byId = cards instanceof Map ? cards : cardsById(cards as readonly OpportunityCardData[]);
  const hidden: Partial<Record<HiddenReason, number>> = {};
  const count = (k: HiddenReason) => { hidden[k] = (hidden[k] ?? 0) + 1; };
  const rank = (r: TriageReading) => (wonProposal(r, byId) || reopenProposal(r, byId, now) ? 0 : 1);
  const newest = [...items].sort((a, b) => rank(a) - rank(b) || (b.sent_at ?? "").localeCompare(a.sent_at ?? ""));
  const seen = new Set<string>();
  const ask: TriageReading[] = [];
  for (const r of newest) {
    const why = hiddenReason(r, byId, now);
    if (why) { count(why); continue; }
    const thread = r.thread_id ?? r.assertion_id;
    if (seen.has(thread)) { count("mismo hilo"); continue; }
    seen.add(thread);
    ask.push(r);
  }
  return { ask, hidden };
}

export const fetchTriageReadings = (status: TriageStatus = "pending") =>
  fetchJsonGet<TriageReadings>(operatorApiUrl(TRIAGE_PATHS.readings, { status, limit: 100 }));

export type { ReviewTriageInput } from "./mailRules";
export { reviewTriage } from "./mailRules";
