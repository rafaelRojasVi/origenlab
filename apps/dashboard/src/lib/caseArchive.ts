import type { ArchiveCase, ArchiveStatus, CrmStatus } from "../api/caseArchive";
import type { V2ChipTone } from "../components/v2/V2Chip";

/** CRM status and archive status are two facts; they are labelled, toned and filtered apart. */
export const CRM_STATUS_LABELS: Record<CrmStatus, string> = {
  not_in_plan: "Fuera del plan",
  ready_to_import: "Confirmada — lista para importar",
  pending_organization_confirmation: "Pendiente: confirmar institución",
  held: "Retenida",
  open_in_crm: "Abierta en el CRM",
  closed_won: "Cerrada: ganada",
  closed_lost: "Cerrada: perdida",
};

export const CRM_STATUS_TONES: Record<CrmStatus, V2ChipTone> = {
  not_in_plan: "danger",
  ready_to_import: "ok",
  pending_organization_confirmation: "warn",
  held: "danger",
  open_in_crm: "ok",
  closed_won: "ok",
  closed_lost: "neutral",
};

export const ARCHIVE_STATUS_LABELS: Record<ArchiveStatus, string> = {
  not_archived: "Sin copia en Drive",
  legacy_only: "Sólo en carpeta antigua",
  archived_verified: "Archivada y verificada",
  archived_unverified: "Archivada, sin verificar descarga",
  hash_mismatch: "¡Hash distinto!",
};

export const ARCHIVE_STATUS_TONES: Record<ArchiveStatus, V2ChipTone> = {
  not_archived: "neutral",
  legacy_only: "warn",
  archived_verified: "ok",
  archived_unverified: "warn",
  hash_mismatch: "danger",
};

export const FLAG_LABELS: Record<string, string> = {
  missing_organization: "Sin institución confirmada",
  number_collision: "Número compartido con otro caso",
  has_revisions: "Con revisiones",
  several_quotes: "Varias cotizaciones",
  pending_document: "Documento retenido",
};

export interface CaseArchiveFilter {
  text: string;
  crm: CrmStatus | "all";
  archive: ArchiveStatus | "all";
  flag: string | "all";
}

export const EMPTY_CASE_FILTER: CaseArchiveFilter = { text: "", crm: "all", archive: "all", flag: "all" };

function fold(value: string): string {
  return value.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().trim();
}

/** `1185`, `01185`, `1185-26` and `CN01185` all name the serial 1185. */
function serial(value: string): string | null {
  const m = fold(value).replace(/^cn/, "").match(/^0*(\d+)/);
  return m ? m[1] : null;
}

export function caseTexts(c: ArchiveCase): string[] {
  return [c.folder_name, c.printed_addressee, c.printed_organization, c.case_key].filter((v): v is string => Boolean(v));
}

export function matchesCaseFilter(c: ArchiveCase, f: CaseArchiveFilter): boolean {
  if (f.crm !== "all" && c.crm.status !== f.crm) return false;
  if (f.flag !== "all" && !c.flags.includes(f.flag)) return false;
  if (f.archive !== "all" && !c.quotes.some((q) => q.revisions.some((r) => r.archive.status === f.archive))) return false;
  const text = f.text.trim();
  if (text) {
    const s = serial(text);
    const byNumber = s !== null && c.quotes.some((q) => serial(q.quote_number)?.startsWith(s));
    const byText = caseTexts(c).some((t) => fold(t).includes(fold(text)));
    if (!byNumber && !byText) return false;
  }
  return true;
}

export function filterCases(cases: ArchiveCase[], f: CaseArchiveFilter): ArchiveCase[] {
  return cases.filter((c) => matchesCaseFilter(c, f));
}

/** Never "ready" because of Drive: the CRM chip reads only the CRM status. */
export function crmChip(c: ArchiveCase): { label: string; tone: V2ChipTone } {
  return { label: CRM_STATUS_LABELS[c.crm.status], tone: CRM_STATUS_TONES[c.crm.status] };
}

export function shortSha(sha: string | null | undefined): string {
  return sha ? sha.slice(0, 12) : "—";
}
