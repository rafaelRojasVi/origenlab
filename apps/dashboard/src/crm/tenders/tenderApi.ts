/**
 * The one client for «Licitaciones»: every call the dashboard makes about tenders lives here (later
 * tasks add to it). Today: the list of tenders and «Nueva licitación» (`open-tender-case`), and the bidder's data and
 * standard documents («Datos para licitaciones» in Preferencias) — `GET /v2/bidder`, `save-bidder-profile`, `add-bidder-document` (multipart),
 * `retire-bidder-document` and a document's signed link.
 *
 * Upstream everything about the bidder is sales/admin only (a viewer gets 403
 * `role_may_not_view_bidder`) and the commands need `ORIGENLAB_V2_COMMANDS_ENABLED`. Every command
 * carries an `Idempotency-Key`; this module owns the `/v2/commands/<…>` path strings (pinned by
 * `src/test/noWritePolicy.test.ts`) through one internal `post()`.
 */
import { OperatorApiError, fetchJsonGet, notifyIfSessionRefused, operatorApiUrl } from "../../api/operatorClient";
import { refusalOfError } from "../../api/refusalEnvelope";

export const MAX_BIDDER_DOCUMENT_BYTES = 20 * 1024 * 1024;

/** A fresh key for one command attempt; the same attempt retried keeps it. */
export function newTenderCommandKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `tender-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/** The API's refusal (code, message, status) behind an error; null for an error that is not the API's. */
export const tenderRefusal = refusalOfError;

/* ── the list and «Nueva licitación» ───────────────────────────────────────── */

export type TenderStatus = "preparando" | "postulada" | "adjudicada" | "perdida";
export const TENDER_STATUSES: readonly TenderStatus[] = ["preparando", "postulada", "adjudicada", "perdida"];

export interface TenderRow {
  id: string;
  version: number;
  code: string | null;
  title: string;
  institution: { id: string | null; name: string | null; confirmation: string | null };
  closes_at: string | null;
  status: TenderStatus;
  /** The offer's neto, else the notice's estimate (`source: "estimado"`). */
  amount: { neto: number; currency: string; source: "oferta" | "estimado" } | null;
  offer_quote_number: string | null;
  preparation: { read: string | null; fill: string | null };
}

export interface TenderList {
  tenders: TenderRow[];
  counts: Record<TenderStatus, number>;
}

/** What Hoy shows for a tender that closes tomorrow (`tenders_closing` in the pipeline). */
export interface TenderClosing {
  id: string;
  code: string | null;
  title: string;
  closes_at: string | null;
}

export interface OpenTenderCaseBody {
  reference: string;
  buyer?: { name: string; rut?: string | null };
}

export const fetchTenders = (status?: TenderStatus) =>
  fetchJsonGet<TenderList>(operatorApiUrl("/v2/tenders", status ? { status } : undefined));

/* ── the bidder ───────────────────────────────────────────────────────────── */

export type BidderKind = "natural_person" | "company";
export type BankAccountType = "corriente" | "vista" | "ahorro" | "rut";
export type ChileProveedoresStatus = "habil" | "no_habil" | "no_inscrito";
export type BidderDocumentKind = "cedula" | "certificado_inscripcion" | "declaracion_jurada" | "experiencia" | "otro";

export const BIDDER_DOCUMENT_KINDS: readonly { value: BidderDocumentKind; label: string }[] = [
  { value: "cedula", label: "Cédula" },
  { value: "certificado_inscripcion", label: "Certificado de inscripción" },
  { value: "declaracion_jurada", label: "Declaración jurada" },
  { value: "experiencia", label: "Experiencia / clientes" },
  { value: "otro", label: "Otro" },
];

export interface BidderProfile {
  id: string;
  bidder_kind: BidderKind;
  legal_name: string;
  rut: string;
  giro: string | null;
  address: string | null;
  comuna: string | null;
  region: string | null;
  phone: string | null;
  email: string | null;
  legal_rep_name: string | null;
  legal_rep_rut: string | null;
  bank_name: string | null;
  bank_account_type: BankAccountType | null;
  bank_account_number: string | null;
  chileproveedores_status: ChileProveedoresStatus | null;
  chileproveedores_checked_on: string | null;
  is_emt: boolean;
  is_local_supplier: boolean;
  reason: string;
  created_at: string;
  set_by: string | null;
}

export interface BidderDocument {
  id: string;
  kind: BidderDocumentKind;
  title: string;
  sha256: string;
  content_type: string;
  byte_size: number;
  original_filename: string;
  added_at: string;
}

export interface BidderView {
  profile: BidderProfile | null;
  history: { id: string; set_by: string | null; reason: string; created_at: string }[];
  documents: BidderDocument[];
}

/** What a save sends: the profile's columns, the reason and the profile the operator was looking at. */
export interface BidderProfileBody {
  bidder_kind: BidderKind;
  legal_name: string;
  rut: string;
  giro?: string | null;
  address?: string | null;
  comuna?: string | null;
  region?: string | null;
  phone?: string | null;
  email?: string | null;
  legal_rep_name?: string | null;
  legal_rep_rut?: string | null;
  bank_name?: string | null;
  bank_account_type?: BankAccountType | null;
  bank_account_number?: string | null;
  chileproveedores_status?: ChileProveedoresStatus | null;
  chileproveedores_checked_on?: string | null;
  is_emt: boolean;
  is_local_supplier: boolean;
  reason: string;
  expected_current_id: string | null;
}

export interface TenderReceipt {
  command: string;
  event_ids?: string[];
  idempotency_key?: string;
  replayed?: boolean;
  [key: string]: unknown;
}

async function post(path: string, init: RequestInit, idempotencyKey: string): Promise<TenderReceipt> {
  const res = await fetch(operatorApiUrl(path), {
    method: "POST",
    credentials: "include",
    ...init,
    headers: { Accept: "application/json", "Idempotency-Key": idempotencyKey, ...(init.headers ?? {}) },
  });
  if (!res.ok) {
    notifyIfSessionRefused(res.status);
    const text = await res.text().catch(() => "");
    throw new OperatorApiError(text || res.statusText || `HTTP ${res.status}`, res.status);
  }
  return (await res.json()) as TenderReceipt;
}

const JSON_HEADERS = { "Content-Type": "application/json" };

export const fetchBidder = () => fetchJsonGet<BidderView>(operatorApiUrl("/v2/bidder"));

/** Open a tender case from a Mercado Público code or link; the receipt names the new case. */
export const openTenderCase = (body: OpenTenderCaseBody, idempotencyKey: string = newTenderCommandKey()) =>
  post("/v2/commands/open-tender-case", { headers: JSON_HEADERS, body: JSON.stringify(body) }, idempotencyKey);

export const saveBidderProfile = (body: BidderProfileBody, idempotencyKey: string = newTenderCommandKey()) =>
  post("/v2/commands/save-bidder-profile", { headers: JSON_HEADERS, body: JSON.stringify(body) }, idempotencyKey);

/** Add one standard document. A file over 20 MiB never leaves the computer. */
export async function addBidderDocument(
  file: File,
  kind: BidderDocumentKind,
  title: string,
  idempotencyKey: string = newTenderCommandKey(),
): Promise<TenderReceipt> {
  if (file.size > MAX_BIDDER_DOCUMENT_BYTES) {
    throw new OperatorApiError(JSON.stringify({ error: { details: { code: "document_too_large" } } }), 413);
  }
  const form = new FormData();
  form.append("kind", kind);
  form.append("title", title);
  form.append("file", file, file.name);
  return post("/v2/commands/add-bidder-document", { body: form }, idempotencyKey);
}

export const retireBidderDocument = (documentId: string, reason: string, idempotencyKey: string = newTenderCommandKey()) =>
  post(
    "/v2/commands/retire-bidder-document",
    { headers: JSON_HEADERS, body: JSON.stringify({ document_id: documentId, reason }) },
    idempotencyKey,
  );

/** A link valid ten minutes; asked for on each «Abrir», never stored. */
export async function bidderDocumentUrl(documentId: string): Promise<string> {
  const body = await fetchJsonGet<{ url?: unknown }>(operatorApiUrl(`/v2/bidder/documents/${encodeURIComponent(documentId)}/url`));
  if (typeof body.url !== "string" || !/^https:\/\//.test(body.url)) throw new OperatorApiError("no url", 502);
  return body.url;
}
