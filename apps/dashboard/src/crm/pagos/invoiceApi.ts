/**
 * The one client for «Pagos»: the SII sales register (`GET /v2/workspace/invoices`), the CSV upload
 * (`import-invoices`, multipart) and three JSON commands — a document's case, «Pagada» and its undo.
 *
 * The commands need an `Idempotency-Key`; the JSON ones also a non-blank `note` (always `PAGOS_NOTE`)
 * and the version shown. This module owns their paths (pinned by `src/test/noWritePolicy.test.ts`)
 * through one internal `post()`. A stale document is refused as `invoice_version_conflict`.
 */
import { OperatorApiError, fetchJsonGet, notifyIfSessionRefused, operatorApiUrl } from "../../api/operatorClient";
import { refusalOfError } from "../../api/refusalEnvelope";
import { newCaseCommandKey } from "../caseCommands";

export const PAGOS_NOTE = "Desde «Pagos».";
export const MAX_SII_CSV_BYTES = 2 * 1024 * 1024;

export type InvoiceStatus = "unpaid" | "paid" | "credited" | "nota";
export type AgeBucket = "0-30" | "31-60" | "61-90" | "90+";

export interface InvoiceCase {
  id: string;
  title: string;
  stage: string;
  quote_number: string | null;
}

export interface InvoiceNote {
  invoice_id: string;
  doc_type: number;
  folio: number;
  issued_on: string;
  total_amount: number;
}

export interface Invoice {
  invoice_id: string;
  version: number;
  /** 33 factura, 34 exenta, 56 nota de débito, 61 nota de crédito. */
  doc_type: number;
  folio: number;
  issued_on: string;
  days_open: number;
  age_bucket: AgeBucket | null;
  client_rut: string;
  client_name: string;
  organization: { id: string; name: string } | null;
  net_amount: number;
  vat_amount: number;
  exempt_amount: number;
  total_amount: number;
  /** Total less its notas de crédito, plus its notas de débito; null on a nota. */
  balance: number | null;
  reference: { doc_type: number; folio: number } | null;
  notes: InvoiceNote[];
  status: InvoiceStatus;
  paid_on: string | null;
  /** «machine» when the upload attached the case by its quote's total. */
  case_link: "machine" | "operator" | null;
  opportunity: InvoiceCase | null;
}

export interface InvoiceList {
  items: Invoice[];
  summary: {
    por_cobrar: number;
    unpaid_count: number;
    by_age: Record<AgeBucket, number>;
    without_case: number;
    last_issued_on: string | null;
  };
  /** The day in Santiago, as the API counted ages. */
  today: string;
}

export interface ImportResult {
  imported: number;
  already_on_record: number;
  attached_to_case: number;
  without_organization: number;
  skipped_types: Record<string, number>;
}

/** The API's refusal (code, message, status) behind an error; null for an error that is not the API's. */
export const invoiceRefusal = refusalOfError;

export const fetchInvoices = (opportunityId?: string) =>
  fetchJsonGet<InvoiceList>(operatorApiUrl("/v2/workspace/invoices", opportunityId ? { opportunity_id: opportunityId } : undefined));

async function post(path: string, init: { body: BodyInit; json: boolean }, idempotencyKey: string): Promise<Record<string, unknown>> {
  const res = await fetch(operatorApiUrl(path), {
    method: "POST",
    credentials: "include",
    headers: {
      Accept: "application/json",
      "Idempotency-Key": idempotencyKey,
      ...(init.json ? { "Content-Type": "application/json" } : {}),
    },
    body: init.body,
  });
  if (!res.ok) {
    notifyIfSessionRefused(res.status);
    const text = await res.text().catch(() => "");
    throw new OperatorApiError(text || res.statusText || `HTTP ${res.status}`, res.status);
  }
  return (await res.json()) as Record<string, unknown>;
}

const json = (body: Record<string, unknown>) => ({ body: JSON.stringify({ ...body, note: PAGOS_NOTE }), json: true });

interface InvoiceRef {
  invoice_id: string;
  invoice_version: number;
}

/** «Subir CSV del SII»: one month's «Registro de Ventas» detail (RCV_VENTA_…). */
export async function importInvoices(file: File, idempotencyKey: string = newCaseCommandKey()): Promise<ImportResult> {
  if (file.size > MAX_SII_CSV_BYTES) {
    throw new OperatorApiError(JSON.stringify({ detail: { code: "sii_file_too_large", message: "the file is larger than 2 MiB" } }), 413);
  }
  const form = new FormData();
  form.append("file", file, file.name);
  return (await post("/v2/commands/import-invoices", { body: form, json: false }, idempotencyKey)) as unknown as ImportResult;
}

export const setInvoiceCase = (body: InvoiceRef & { opportunity_id: string | null }, idempotencyKey: string = newCaseCommandKey()) =>
  post("/v2/commands/set-invoice-case", json({ ...body }), idempotencyKey);

export const markInvoicePaid = (body: InvoiceRef & { paid_on: string }, idempotencyKey: string = newCaseCommandKey()) =>
  post("/v2/commands/mark-invoice-paid", json({ ...body }), idempotencyKey);

export const markInvoiceUnpaid = (body: InvoiceRef, idempotencyKey: string = newCaseCommandKey()) =>
  post("/v2/commands/mark-invoice-unpaid", json({ ...body }), idempotencyKey);
