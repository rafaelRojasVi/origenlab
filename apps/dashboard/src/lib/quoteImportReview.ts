import type {
  ImportCompleteness,
  ImportOpportunity,
  ImportPlanStatus,
} from "../api/quoteImportReview";

export interface ImportReviewFilter {
  organization: string;
  quoteNumber: string;
  opportunity: string;
  status: ImportPlanStatus | "all";
  completeness: ImportCompleteness | "all";
}

export const EMPTY_IMPORT_FILTER: ImportReviewFilter = {
  organization: "",
  quoteNumber: "",
  opportunity: "",
  status: "all",
  completeness: "all",
};

export const PLAN_STATUS_LABELS: Record<ImportPlanStatus, string> = {
  ready: "Lista",
  waiting: "En espera de organización",
  held: "Retenida",
};

export const COMPLETENESS_LABELS: Record<ImportCompleteness, string> = {
  complete: "Evidencia completa",
  incomplete: "Evidencia incompleta",
  not_imported: "Sin importar",
  leaked: "Filtrada a la base",
};

function fold(value: string): string {
  return value
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .trim();
}

/** `1185`, `01185`, `1185-26` and `CN01185` all name the serial 1185. */
function quoteSerial(value: string): string {
  const m = fold(value).replace(/^cn/, "").match(/^0*(\d+)/);
  return m ? m[1] : fold(value);
}

export function organizationTexts(row: ImportOpportunity): string[] {
  return [
    row.printed_organization,
    row.printed_addressee,
    row.confirmation.organization_name,
    ...row.organizations.map((o) => o.name),
  ].filter((v): v is string => Boolean(v));
}

export function matchesImportFilter(row: ImportOpportunity, f: ImportReviewFilter): boolean {
  if (f.status !== "all" && row.plan_status !== f.status) return false;
  if (f.completeness !== "all" && row.completeness !== f.completeness) return false;
  if (f.organization.trim()) {
    const needle = fold(f.organization);
    if (!organizationTexts(row).some((t) => fold(t).includes(needle))) return false;
  }
  if (f.quoteNumber.trim()) {
    const serial = quoteSerial(f.quoteNumber);
    if (!row.quotes.some((q) => quoteSerial(q.quote_number).startsWith(serial))) return false;
  }
  if (f.opportunity.trim()) {
    const needle = fold(f.opportunity);
    const hay = [row.planned_opportunity_id, row.opportunity?.id, row.opportunity?.title]
      .filter((v): v is string => Boolean(v))
      .map(fold);
    if (!hay.some((h) => h.includes(needle))) return false;
  }
  return true;
}

export function filterImportRows(rows: ImportOpportunity[], f: ImportReviewFilter): ImportOpportunity[] {
  return rows.filter((row) => matchesImportFilter(row, f));
}

export function shortSha(sha: string | null | undefined): string {
  return sha ? sha.slice(0, 12) : "—";
}
