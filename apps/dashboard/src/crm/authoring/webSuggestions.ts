/**
 * «Sugerencias de la web»: which researched facts the CRM does not hold yet, and how each one is
 * applied — always through an existing authoring command, never by itself.
 *
 *   name, legal name, type → update-organization (`name`, `legal_name`, `kind`)
 *   RUT                    → add-organization-identifier, scheme `rut`
 *   email domain           → add-organization-domain
 *
 * «Aplicar todo y confirmar» applies, in that order, the fields of a `high`-confidence suggestion
 * the CRM does not already hold — never a RUT seen only in a directory — then
 * confirm-organization-record. Each step is its own command and receipt; the first refusal stops
 * the run and is named, and what was applied before it stays applied.
 */
import {
  addOrganizationDomain,
  addOrganizationIdentifier,
  confirmOrganizationRecord,
  refusalOf,
  updateOrganization,
  type CommandReceipt,
  type OrganizationAuthoringResponse,
  type OrgWebSuggestion,
} from "./crmAuthoringApi";

export type SuggestionField = "name" | "legal_name" | "kind" | "rut" | "domain";

export const FIELD_ORDER: readonly SuggestionField[] = ["name", "legal_name", "kind", "rut", "domain"];

export const FIELD_LABEL: Record<SuggestionField, string> = {
  name: "Nombre",
  legal_name: "Razón social",
  kind: "Tipo",
  rut: "RUT",
  domain: "Dominio de correo",
};

export const CONFIDENCE_LABEL: Record<OrgWebSuggestion["confidence"], string> = {
  high: "confianza alta",
  medium: "confianza media",
  low: "confianza baja",
  not_found: "no encontrada",
};

export interface SuggestionRow {
  field: SuggestionField;
  label: string;
  value: string;
  /** The CRM already holds this value («ya aplicado»). */
  applied: boolean;
  /** A RUT seen only in a directory: «verificar en SII antes de aplicar», never in «Aplicar todo». */
  verifyInSii: boolean;
}

/** `12.345.678-5`, `12345678-5`, `012345678 5` → `12345678-5` (the API's `rut_key`). */
export function rutKey(raw: string | null | undefined): string | null {
  const s = (raw ?? "").replace(/[^0-9kK]/g, "").toUpperCase();
  if (s.length < 2) return null;
  return `${s.slice(0, -1).replace(/^0+/, "")}-${s.slice(-1)}`;
}

export function suggestionRows(data: OrganizationAuthoringResponse, s: OrgWebSuggestion): SuggestionRow[] {
  const org = data.organization;
  const ruts = new Set(data.identifiers.filter((i) => i.scheme === "rut" && !i.removed_at).map((i) => rutKey(i.value_norm)));
  const domains = new Set(data.domains.filter((d) => !d.removed_at).map((d) => d.domain_norm));
  const rows: SuggestionRow[] = [];
  const add = (field: SuggestionField, value: string | null, applied: boolean, verifyInSii = false) => {
    if (value) rows.push({ field, label: FIELD_LABEL[field], value, applied, verifyInSii });
  };
  add("name", s.display_name, s.display_name?.trim() === org.name.trim());
  add("legal_name", s.legal_name, s.legal_name?.trim() === (org.legal_name ?? "").trim());
  add("kind", s.type, s.type === org.kind);
  add("rut", s.rut, ruts.has(rutKey(s.rut)), s.rut_source === "directory");
  add("domain", s.email_domain, domains.has(s.email_domain ?? ""));
  return rows;
}

/** What «Aplicar todo y confirmar» would apply, in order. Empty unless the suggestion is `high`. */
export function applyAllPlan(rows: SuggestionRow[], s: OrgWebSuggestion): SuggestionRow[] {
  if (s.confidence !== "high") return [];
  return FIELD_ORDER.flatMap((f) => rows.filter((r) => r.field === f && !r.applied && !r.verifyInSii));
}

/** The note every applied field carries: where it came from. */
export function suggestionNote(s: OrgWebSuggestion): string {
  return `Sugerencia web (${CONFIDENCE_LABEL[s.confidence]}): ${s.sources[0]?.url ?? "sin fuente"}`.slice(0, 2000);
}

/** One field through its command; the institution's version after it. */
export async function applyField(row: SuggestionRow, organizationId: string, version: number, note: string): Promise<number> {
  const base = { organization_id: organizationId, expected_version: version, note };
  let receipt: CommandReceipt;
  switch (row.field) {
    case "name":
      receipt = await updateOrganization({ ...base, name: row.value });
      break;
    case "legal_name":
      receipt = await updateOrganization({ ...base, legal_name: row.value });
      break;
    case "kind":
      receipt = await updateOrganization({ ...base, kind: row.value });
      break;
    case "rut":
      receipt = await addOrganizationIdentifier({ ...base, scheme: "rut", value: row.value });
      break;
    case "domain":
      receipt = await addOrganizationDomain({ ...base, domain: row.value });
      break;
  }
  return typeof receipt.version === "number" ? receipt.version : version + 1;
}

export type ApplyAllOutcome =
  | { ok: true; applied: SuggestionField[]; confirmed: boolean }
  | { ok: false; applied: SuggestionField[]; failed: SuggestionField | "confirm"; code: string; message: string };

export async function applyAllAndConfirm(args: {
  organizationId: string;
  version: number;
  alreadyConfirmed: boolean;
  plan: SuggestionRow[];
  note: string;
}): Promise<ApplyAllOutcome> {
  let version = args.version;
  const applied: SuggestionField[] = [];
  for (const row of args.plan) {
    try {
      version = await applyField(row, args.organizationId, version, args.note);
      applied.push(row.field);
    } catch (err) {
      const r = refusalOf(err);
      return { ok: false, applied, failed: row.field, code: r?.code ?? "error", message: r?.message ?? String(err) };
    }
  }
  if (args.alreadyConfirmed) return { ok: true, applied, confirmed: false };
  try {
    await confirmOrganizationRecord({ organization_id: args.organizationId, expected_version: version, note: args.note });
  } catch (err) {
    const r = refusalOf(err);
    return { ok: false, applied, failed: "confirm", code: r?.code ?? "error", message: r?.message ?? String(err) };
  }
  return { ok: true, applied, confirmed: true };
}

/** A refusal in the operator's words. */
export function refusalText(code: string, message: string): string {
  switch (code) {
    case "stale_version":
      return "Otro operador modificó esta institución; recarga y vuelve a intentar.";
    case "identifier_taken":
      return "Ese RUT ya está registrado en otra institución.";
    case "identifier_already_present":
      return "Ese RUT ya está en esta institución.";
    case "domain_already_present":
      return "Ese dominio ya está en esta institución.";
    case "exclusive_domain_taken":
      return "Otra institución tiene ese dominio como exclusivo.";
    case "invalid_domain":
      return "El dominio sugerido no es válido.";
    case "archived_subject":
      return "La institución está archivada.";
    default:
      return `${code}: ${message}`;
  }
}
