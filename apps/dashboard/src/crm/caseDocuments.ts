/**
 * A case's purchase order (owner request 2026-10-12): attach it when the case is won — from the
 * computer or from a case email — open it, and remove a wrong one.
 *
 * The API stores the file privately (bucket `crm-documents`) and records it on the case
 * (`crm.opportunity_document`). Attaching changes no case version, so «Marcar ganada» attaches the
 * OC first and then records the win against the version it was shown. Offered only when the
 * session says so (`case_documents_enabled`: case commands on and Storage configured).
 */
import type { AuthSessionState } from "../api/authClient";
import { OperatorApiError, notifyIfSessionRefused, operatorApiUrl } from "../api/operatorClient";
import { useAuthSession } from "../context/AuthSessionContext";
import { ROLES_THAT_DECIDE_CASES, newCaseCommandKey } from "./caseCommands";
import type { CaseDocument, CaseMailDocumentsResponse, CasePurchaseOrderCandidatesResponse, OpportunityCardData } from "./crmTypes";

export const CASE_DOCUMENT_PATHS = {
  upload: "/v2/commands/attach-case-document",
  fromEmail: "/v2/commands/attach-case-document-from-email",
  remove: "/v2/commands/remove-case-document",
  url: (opportunityId: string, documentId: string) =>
    `/v2/workspace/opportunities/${encodeURIComponent(opportunityId)}/documents/${encodeURIComponent(documentId)}/url`,
} as const;

/** 20 MiB — the API's limit; checked here first so a large file is refused before it is sent. */
export const MAX_DOCUMENT_BYTES = 20 * 1024 * 1024;
export const ACCEPTED_DOCUMENT_TYPES = "application/pdf,image/jpeg,image/png,.pdf,.jpg,.jpeg,.png";

/* ── gating ───────────────────────────────────────────────────────────────── */

export function mayAttachCaseDocuments(session: AuthSessionState): boolean {
  return (
    session.kind === "signed_in" &&
    session.caseCommandsEnabled === true &&
    session.caseDocumentsEnabled === true &&
    ROLES_THAT_DECIDE_CASES.has(session.operator.role)
  );
}

export function useMayAttachCaseDocuments(): boolean {
  return mayAttachCaseDocuments(useAuthSession().session);
}

/* ── what the operator chose ──────────────────────────────────────────────── */

/** An OC to attach: a file from the computer, or one attachment of a case email. */
export type OcChoice =
  | { kind: "file"; file: File }
  | { kind: "email"; source_record_id: string; document_sha256: string; filename: string | null };

export interface EmailDocumentOption {
  source_record_id: string;
  document_sha256: string;
  filename: string | null;
  subject: string | null;
  sent_at: string | null;
  /** The file name or subject reads like a purchase order («OC», «Orden de compra»). */
  looks_like_po: boolean;
}

const PO_WORDS = /(\bo\.?\s?c\b|orden\s+de\s+compra|\bord[-_\s]?co\b|purchase\s+order)/i;

export function looksLikePurchaseOrder(...texts: (string | null | undefined)[]): boolean {
  return texts.some((t) => PO_WORDS.test(t ?? ""));
}

/**
 * The PDFs a case's emails carry that are not a quotation already, plus the purchase orders the
 * API found on other threads with the same people; likely OCs first, then newest.
 */
export function emailDocumentOptions(
  mail: CaseMailDocumentsResponse | null,
  candidates: CasePurchaseOrderCandidatesResponse | null,
): EmailDocumentOption[] {
  const seen = new Set<string>();
  const out: EmailDocumentOption[] = [];
  const add = (o: EmailDocumentOption) => {
    const key = `${o.source_record_id}:${o.document_sha256}`;
    if (seen.has(key)) return;
    seen.add(key);
    out.push(o);
  };
  for (const c of candidates?.candidates ?? []) {
    add({ source_record_id: c.source_record_id, document_sha256: c.document_sha256, filename: c.filename,
          subject: c.subject, sent_at: c.sent_at, looks_like_po: true });
  }
  for (const m of mail?.messages ?? []) {
    for (const d of m.documents) {
      if (d.recorded) continue; // a quotation already, never its purchase order
      if (!/\.(pdf|jpe?g|png)$/i.test(d.filename ?? "")) continue;
      add({ source_record_id: m.source_record_id, document_sha256: d.sha256, filename: d.filename,
            subject: m.subject, sent_at: m.sent_at, looks_like_po: looksLikePurchaseOrder(d.filename, m.subject) });
    }
  }
  return out.sort((a, b) =>
    a.looks_like_po === b.looks_like_po
      ? (b.sent_at ?? "").localeCompare(a.sent_at ?? "")
      : a.looks_like_po ? -1 : 1,
  );
}

/** The OCs attached to one quote revision, and those that name none. */
export function documentsForRevision(card: Pick<OpportunityCardData, "documents">, revisionId: string | null | undefined): CaseDocument[] {
  return (card.documents ?? []).filter((d) => d.quote_revision_id && d.quote_revision_id === revisionId);
}

export function ocLabel(d: Pick<CaseDocument, "document_number" | "original_filename">): string {
  return d.document_number ? `OC Nº ${d.document_number}` : d.original_filename;
}

/* ── the API ──────────────────────────────────────────────────────────────── */

export interface AttachReceipt {
  document: { document_id: string; document_number: string | null; original_filename: string };
  replayed: boolean;
  [key: string]: unknown;
}

async function send(path: string, init: RequestInit, idempotencyKey: string): Promise<AttachReceipt> {
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
  return (await res.json()) as AttachReceipt;
}

export interface AttachFields {
  opportunity_id: string;
  quote_revision_id?: string | null;
  document_number?: string | null;
  note: string;
}

/** Attach one OC, from the computer or from a case email. A file over 20 MiB never leaves. */
export async function attachPurchaseOrder(
  choice: OcChoice,
  fields: AttachFields,
  idempotencyKey: string = newCaseCommandKey(),
): Promise<AttachReceipt> {
  const number = fields.document_number?.trim() || null;
  if (choice.kind === "file") {
    if (choice.file.size > MAX_DOCUMENT_BYTES) {
      throw new OperatorApiError(JSON.stringify({ error: { details: { code: "document_too_large" } } }), 413);
    }
    const form = new FormData();
    form.append("opportunity_id", fields.opportunity_id);
    form.append("note", fields.note);
    if (fields.quote_revision_id) form.append("quote_revision_id", fields.quote_revision_id);
    if (number) form.append("document_number", number);
    form.append("file", choice.file, choice.file.name);
    return send(CASE_DOCUMENT_PATHS.upload, { body: form }, idempotencyKey);
  }
  return send(
    CASE_DOCUMENT_PATHS.fromEmail,
    {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        opportunity_id: fields.opportunity_id,
        source_record_id: choice.source_record_id,
        document_sha256: choice.document_sha256,
        quote_revision_id: fields.quote_revision_id || null,
        document_number: number,
        note: fields.note,
      }),
    },
    idempotencyKey,
  );
}

export const removeCaseDocument = (documentId: string, reason: string, idempotencyKey: string = newCaseCommandKey()) =>
  send(
    CASE_DOCUMENT_PATHS.remove,
    { headers: { "Content-Type": "application/json" }, body: JSON.stringify({ document_id: documentId, note: reason }) },
    idempotencyKey,
  );

/** A link valid ten minutes; asked for on each «Abrir», never stored. */
export async function fetchCaseDocumentUrl(opportunityId: string, documentId: string): Promise<string> {
  const res = await fetch(operatorApiUrl(CASE_DOCUMENT_PATHS.url(opportunityId, documentId)), {
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (!res.ok) {
    notifyIfSessionRefused(res.status);
    throw new OperatorApiError(await res.text().catch(() => res.statusText), res.status);
  }
  const body = (await res.json()) as { url?: unknown };
  if (typeof body.url !== "string" || !/^https:\/\//.test(body.url)) throw new OperatorApiError("no url", 502);
  return body.url;
}

/* ── refusals, in Spanish ─────────────────────────────────────────────────── */

const DOCUMENT_REFUSAL_ES: Record<string, string> = {
  document_too_large: "El archivo pesa más de 20 MB.",
  unsupported_document: "Sólo se aceptan PDF, JPG o PNG.",
  document_already_attached: "Esta OC ya está adjunta al caso.",
  document_not_in_message: "Ese correo ya no tiene ese adjunto.",
  message_not_stored: "El correo original no está guardado; descarga el PDF desde Gmail y súbelo.",
  attachment_not_in_eml: "No se encontró el adjunto en el correo guardado; descarga el PDF desde Gmail y súbelo.",
  quote_revision_not_on_case: "Esa cotización no es de este caso.",
  storage_unavailable: "El almacenamiento de documentos no está disponible ahora. Intenta más tarde.",
  document_already_removed: "Ese documento ya se había quitado.",
  document_not_found: "Ya no se encuentra ese documento.",
  case_not_found: "Ya no se encuentra el caso.",
};

export function documentRefusalCode(err: unknown): string | null {
  if (!(err instanceof OperatorApiError)) return null;
  try {
    const parsed = JSON.parse(err.message) as { detail?: { code?: unknown }; error?: { code?: unknown; details?: { code?: unknown } } };
    const code = parsed.error?.details?.code ?? parsed.detail?.code ?? parsed.error?.code;
    return typeof code === "string" ? code : null;
  } catch {
    return null;
  }
}

export function documentRefusalText(err: unknown): string {
  const code = documentRefusalCode(err);
  if (code && DOCUMENT_REFUSAL_ES[code]) return DOCUMENT_REFUSAL_ES[code];
  if (err instanceof OperatorApiError) {
    if (err.status === 401) return "La sesión expiró; vuelve a ingresar.";
    if (err.status === 403) return "Tu perfil no puede adjuntar documentos.";
    if (err.status === 404) return "Adjuntar documentos no está habilitado en este entorno.";
    return code ? `No se pudo adjuntar (código ${code}).` : `No se pudo adjuntar (HTTP ${err.status}).`;
  }
  return "No se pudo adjuntar: revisa la conexión y vuelve a intentarlo.";
}
