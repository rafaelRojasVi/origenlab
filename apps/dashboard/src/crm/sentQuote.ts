/**
 * «Completar con la cotización del correo» (2026-10-13): what the dashboard reads before it asks the
 * API to make a sent quote a case in «Enviada» (the command lives in `caseCommands.ts`).
 *
 * `institutionFromQuoteFile` mirrors `mail_rules.institution_segment` (apps/api): the Cotizador
 * names a quote `CN<número>-<institución>.pdf` or `CN<número>-<persona> - <institución>.pdf`; the
 * one dash-separated part with a legal form, a kind of institution or a «(SIGLA)» is the
 * institution. It only prefills a field a person confirms; the API never trusts it.
 */
import type { CaseMailMessage } from "./crmTypes";

const FILE = /^\s*(?:CN|COT)\s*-?\s*\d+(?:\s*-\s*\d{2}(?!\d))?\s*[-_–]\s*(.+?)\s*\.pdf\s*$/i;
const MODEL_CODE = /^[A-Z]{1,5}\d[A-Z0-9]*$/;
const MARKER =
  /\b(?:s\.?\s?a\.?|spa|ltda\.?|limitada|eirl|inc\.?|llc|corp\.?|group|universidad|instituto|laboratorios?|lab|labs|hospital|clinica|facultad|centro|servicio|ministerio|municipalidad|corporacion|fundacion|empresas?|agricola|sociedad|chile|foods|industrial|comercial|asociacion|cooperativa|escuela|colegio|liceo)(?=\s|$|[.,)])/;

const fold = (s: string) => s.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/\s+/g, " ").trim();

export function institutionFromQuoteFile(filename: string | null | undefined): string | null {
  const m = FILE.exec(filename ?? "");
  if (!m) return null;
  const parts = m[1]
    .split(/\s*[-–—]\s*/)
    .map((p) => p.replace(/^[\s_,]+|[\s_,]+$/g, ""))
    .filter((p) => fold(p).length >= 3 && !MODEL_CODE.test(p));
  const marked = parts.filter((p) => /\(([^()]*)\)/.test(p) || MARKER.test(fold(p)));
  if (marked.length !== 1) return null;
  // One part with no dash may still hold a person («Persona Universidad X»): it must start with
  // what it is (`mail_rules.institution_segment`).
  if (parts.length === 1 && MARKER.exec(fold(marked[0]))?.index !== 0) return null;
  return marked[0];
}

export interface QuotePdf {
  sourceRecordId: string;
  filename: string;
  token: string;
}

/** The one quote PDF, not yet recorded anywhere, that the case's emails carry — or null. */
export function quotePdfOf(messages: readonly CaseMailMessage[]): QuotePdf | null {
  const found: QuotePdf[] = [];
  for (const m of messages) {
    for (const d of m.documents) {
      if (d.recorded || !d.filename?.toLowerCase().endsWith(".pdf") || !d.cn_tokens.length) continue;
      found.push({ sourceRecordId: m.source_record_id, filename: d.filename, token: d.cn_tokens[0] });
    }
  }
  return found.length === 1 ? found[0] : null;
}
