/**
 * The one client for «Licitaciones»: every call the dashboard makes about tenders lives here (later
 * tasks add to it). The list of tenders, «En Mercado Público» (`GET /v2/tenders/discovered`) and
 * «Nueva licitación» (`open-tender-case`); one tender's page
 * (`GET /v2/tenders/{id}`, `set-tender-status`, `attach-tender-annexes` (multipart), «Quitar anexo»
 * (`remove-tender-annex`), `prepare-tender-bid`,
 * `create-tender-offer-quote` and an output file's signed link); and the bidder's data and standard
 * documents («Datos para licitaciones» in Preferencias) — `GET /v2/bidder`, `save-bidder-profile`,
 * `add-bidder-document` (multipart), `retire-bidder-document` and a document's signed link.
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

/* ── «En Mercado Público» ─────────────────────────────────────────────────── */

/** `catalogo`: names a family of the catalog; `laboratorio`: other laboratory equipment. */
export type DiscoveryTier = "catalogo" | "laboratorio";

export interface DiscoveredItem {
  name: string | null;
  description: string | null;
  quantity: number | string | null;
  unit: string | null;
  unspsc: string | null;
  category?: string | null;
  /** The curated families and the catalog words (type, maker, model) this item names. */
  families?: { key: string; label: string }[];
  terms?: string[];
  lab_equipment?: boolean;
}

export interface DiscoveredNotice {
  id: string;
  code: string;
  title: string;
  buyer: string | null;
  region: string | null;
  closes_at: string | null;
  published_at: string | null;
  first_seen_at: string | null;
  estimated_amount: { amount: number; currency: string } | null;
  tier: DiscoveryTier;
  families: { key: string; label: string }[];
  /** The catalog words that matched (a model, a maker, a type), for «calza con». */
  terms?: string[];
  items: DiscoveredItem[];
  item_count: number;
  /** False until the sweep has read the notice's detail (buyer, items). */
  detail: boolean;
  portal_url: string;
  /** The open tender case already made from it, if any. */
  opportunity_id: string | null;
}

export interface DiscoveredList {
  notices: DiscoveredNotice[];
  counts: Record<DiscoveryTier, number>;
  families: { key: string; label: string }[];
  last_checked_at: string | null;
}

/** Open Mercado Público tenders that fit the catalog (the worker's sweep, every 15 minutes). */
export const fetchDiscoveredTenders = () => fetchJsonGet<DiscoveredList>(operatorApiUrl("/v2/tenders/discovered"));

/** One notice's page: the row plus every date, every item and the head's facts. */
export interface DiscoveredNoticePage extends Omit<DiscoveredNotice, "tier"> {
  tier: DiscoveryTier | null;
  description: string | null;
  dates: { key: string; label: string; at: string }[];
  facts: { label: string; value: string }[];
  source_status: string | null;
  open: boolean;
}

export const fetchDiscoveredTender = (noticeId: string) =>
  fetchJsonGet<DiscoveredNoticePage>(operatorApiUrl(`/v2/tenders/discovered/${encodeURIComponent(noticeId)}`));

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

/* ── one tender's page ────────────────────────────────────────────────────── */

export type PreparationPhase = "read" | "fill";
export type PreparationStatus = "queued" | "running" | "ready" | "failed" | "budget_stopped";

/** A citation: the anexo (`archivo_id` is its `document_id`) and where in it (`p.3`, `Hoja1!A1:F40`). */
export interface Cita {
  archivo_id: string;
  ubicacion: string;
}

export interface ResumenField {
  texto: string;
  citas: Cita[];
  sin_cita: boolean;
}

export type ChecklistEstado = "listo" | "falta" | "firmar";

export interface ChecklistLine {
  documento: string;
  tipo?: string;
  requiere_firma?: boolean;
  archivo_formulario_id?: string | null;
  fuente: Cita | null;
  sin_cita: boolean;
  /** Set by the fill (and on the read's own «convertir a Word» lines). */
  estado?: ChecklistEstado;
  falta?: string;
  archivos?: string[];
}

export interface ReadItem {
  linea: number;
  descripcion: string;
  cantidad: string;
  unidad: string;
  requisitos: string[];
  product_id?: string | null;
  candidatos?: { product_id: string; modelo: string | null; nombre: string | null }[];
}

/** What the worker stores in `result`; every key may be missing on a failed or stopped row. */
export interface PreparationResult {
  resumen?: Record<string, ResumenField>;
  checklist?: ChecklistLine[];
  items?: ReadItem[];
  annexes?: { document_id: string; filename: string; outcome: string }[];
  match?: { estado: string; codigo?: string };
  fichas?: { product_id: string; estado: string; file_key?: string }[];
  drive?: string;
  read_preparation_id?: string;
}

export interface PreparationOutput {
  file_key: string;
  filename: string | null;
  kind: string;
  completar_count: number | null;
}

export interface Preparation {
  id: string;
  phase: PreparationPhase;
  status: PreparationStatus;
  requested_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  failure_code: string | null;
  annex_document_ids: string[];
  based_on_preparation_id: string | null;
  quote_revision_id: string | null;
  result: PreparationResult | null;
  outputs: PreparationOutput[];
}

export interface TenderAnnex {
  document_id: string;
  filename: string;
  content_type: string;
  byte_size: number;
  sha256: string;
  source: string;
  attached_at: string | null;
}

export interface TenderPageData {
  tender: TenderRow & {
    portal_url: string | null;
    notice_head_summary: { description: string | null; estimated_amount: number | null; currency: string; source_status: string | null };
  };
  annexes: TenderAnnex[];
  preparations: Record<PreparationPhase, Preparation | null>;
  offer_quote: {
    quote_id: string;
    revision_id: string;
    number: string;
    status: string;
    neto: number | null;
    iva: number | null;
    total: number | null;
  } | null;
  spend: { spent_usd: string; cap_usd: string; month: string; reached: boolean };
  bidder_ready: boolean;
}

export interface AnnexUploadResult {
  attached: { document_id: string; filename: string }[];
  skipped: { filename: string; reason: string }[];
}

/** The API takes at most 20 files (or a zip) and 60 MiB in one request. */
export const MAX_ANNEX_FILES = 20;
export const MAX_ANNEX_TOTAL_BYTES = 60 * 1024 * 1024;

export const fetchTenderPage = (id: string) => fetchJsonGet<TenderPageData>(operatorApiUrl(`/v2/tenders/${encodeURIComponent(id)}`));

export interface SetTenderStatusBody {
  opportunity_id: string;
  status: Exclude<TenderStatus, "adjudicada">;
  expected_version: number;
  reason?: string;
}

export const setTenderStatus = (body: SetTenderStatusBody, idempotencyKey: string = newTenderCommandKey()) =>
  post("/v2/commands/set-tender-status", { headers: JSON_HEADERS, body: JSON.stringify(body) }, idempotencyKey);

/** «Subir anexos»: every chosen file under `files`, one request. */
export async function attachTenderAnnexes(
  opportunityId: string,
  files: readonly File[],
  idempotencyKey: string = newTenderCommandKey(),
): Promise<AnnexUploadResult> {
  const form = new FormData();
  form.append("opportunity_id", opportunityId);
  for (const file of files) form.append("files", file, file.name);
  const receipt = await post("/v2/commands/attach-tender-annexes", { body: form }, idempotencyKey);
  return {
    attached: Array.isArray(receipt.attached) ? (receipt.attached as AnnexUploadResult["attached"]) : [],
    skipped: Array.isArray(receipt.skipped) ? (receipt.skipped as AnnexUploadResult["skipped"]) : [],
  };
}

/** «Quitar anexo»: one anexo removed, with its motivo (at least 3 characters); no new preparation takes it. */
export const removeTenderAnnex = (
  opportunityId: string,
  documentId: string,
  reason: string,
  idempotencyKey: string = newTenderCommandKey(),
) =>
  post(
    "/v2/commands/remove-tender-annex",
    { headers: JSON_HEADERS, body: JSON.stringify({ opportunity_id: opportunityId, document_id: documentId, reason }) },
    idempotencyKey,
  );

export const prepareTenderBid = (opportunityId: string, phase: PreparationPhase, idempotencyKey: string = newTenderCommandKey()) =>
  post(
    "/v2/commands/prepare-tender-bid",
    { headers: JSON_HEADERS, body: JSON.stringify({ opportunity_id: opportunityId, phase }) },
    idempotencyKey,
  );

export const createTenderOfferQuote = (opportunityId: string, preparationId: string, idempotencyKey: string = newTenderCommandKey()) =>
  post(
    "/v2/commands/create-tender-offer-quote",
    { headers: JSON_HEADERS, body: JSON.stringify({ opportunity_id: opportunityId, preparation_id: preparationId }) },
    idempotencyKey,
  );

/** A link to one output file, valid ten minutes; asked for on each click, never stored. */
export async function preparationFileUrl(tenderId: string, preparationId: string, fileKey: string): Promise<string> {
  const path = `/v2/tenders/${encodeURIComponent(tenderId)}/preparations/${encodeURIComponent(preparationId)}/files/${encodeURIComponent(fileKey)}/url`;
  const body = await fetchJsonGet<{ url?: unknown }>(operatorApiUrl(path));
  if (typeof body.url !== "string" || !/^https:\/\//.test(body.url)) throw new OperatorApiError("no url", 502);
  return body.url;
}
