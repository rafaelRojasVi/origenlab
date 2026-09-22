/**
 * Presentation logic for the V2 CRM browser — the four durable cards.
 *
 * Kept out of the component so the decisions that matter can be tested without a DOM.
 * They are mostly one decision repeated: **never let the UI claim more than the data
 * says.** A machine-proposed organization is not a confirmed customer, an unattributed
 * address is not a person, and a list capped at the server's child limit is not the whole
 * list. Each of those is a sentence here rather than a bare value in a table cell.
 */

import type { V2CardCounts, V2ContactUsage } from "../api/v2Types";

export type CrmV2Tab = "contacts" | "organizations" | "prospects" | "evidence";

export interface CrmV2TabDefinition {
  id: CrmV2Tab;
  label: string;
  /** What this tab reads, said plainly enough that a zero is never mysterious. */
  description: string;
  searchable: boolean;
}

export const CRM_V2_TABS: readonly CrmV2TabDefinition[] = [
  {
    id: "contacts",
    label: "Contactos",
    description:
      "Canales de correo del núcleo durable V2. Un canal sin persona no es un error: es el estado honesto de una dirección cuyo dueño aún no está establecido.",
    searchable: true,
  },
  {
    id: "organizations",
    label: "Organizaciones",
    description:
      "Instituciones del núcleo durable V2. Todas llegan como propuesta de máquina hasta que un operador las confirma y clasifica.",
    searchable: true,
  },
  {
    id: "prospects",
    label: "Prospectos",
    description:
      "Oportunidades en etapa lead o qualifying. Prospecto es una etapa, no una entidad: no existe una tabla de prospectos y no debe existir.",
    searchable: false,
  },
  {
    id: "evidence",
    label: "Evidencia",
    description:
      "El rastro de observaciones y su procedencia. Es la cola de revisión en forma de lista: cada fila dice de qué registro salió.",
    searchable: true,
  },
];

const USAGE_LABELS: Record<V2ContactUsage, string> = {
  personal: "Personal",
  work: "Laboral",
  shared_mailbox: "Buzón compartido",
  unattributed: "Sin atribuir",
};

export function usageLabel(usage: V2ContactUsage): string {
  return USAGE_LABELS[usage] ?? USAGE_LABELS.unattributed;
}

export function confirmationLabel(confirmation: string): string {
  return confirmation === "confirmed" ? "Confirmado" : "Propuesta de máquina";
}

const RESOLUTION_LABELS: Record<string, string> = {
  unresolved: "Sin resolver",
  promoted: "Promovida",
  linked: "Vinculada",
  rejected: "Rechazada",
  ambiguous: "Ambigua",
};

export function resolutionLabel(resolution: string): string {
  return RESOLUTION_LABELS[resolution] ?? resolution;
}

const SOURCE_KIND_LABELS: Record<string, string> = {
  workbook_import: "Planilla",
  chilecompra_notice: "ChileCompra",
  migration_manifest: "Manifiesto de migración",
  v1_parse_failure: "Fallo de parseo V1",
  v1_evidence_edge: "Borde de evidencia V1",
  v1_supplier_candidate: "Candidato a proveedor V1",
  v1_historical_quote_candidate: "Cotización histórica V1",
  gmail_message: "Correo (Gmail)",
  drive_file: "Documento (Drive)",
};

export function sourceKindLabel(kind: string): string {
  return SOURCE_KIND_LABELS[kind] ?? kind;
}

/** The filter options the API accepts. A value outside these is a 422, not an empty page. */
export const EVIDENCE_RESOLUTIONS: readonly string[] = [
  "unresolved",
  "promoted",
  "linked",
  "rejected",
  "ambiguous",
];

export const EVIDENCE_SOURCE_KINDS: readonly string[] = [
  "workbook_import",
  "chilecompra_notice",
  "migration_manifest",
  "v1_parse_failure",
  "v1_evidence_edge",
  "v1_supplier_candidate",
  "v1_historical_quote_candidate",
  "gmail_message",
  "drive_file",
];

/**
 * "31–60 de 9.460".
 *
 * Built from `total`, which the boundary returns, never from `items.length`, which is
 * capped at the page size and would silently understate every count over 200.
 */
export function pageFooter(total: number, limit: number, offset: number): string {
  if (total <= 0) {
    return "Sin resultados";
  }
  const first = Math.min(offset + 1, total);
  const last = Math.min(offset + limit, total);
  return `${first.toLocaleString("es-CL")}–${last.toLocaleString("es-CL")} de ${total.toLocaleString("es-CL")}`;
}

/**
 * The note shown under a card list that the server truncated.
 *
 * Returns null when nothing was hidden, so a complete list carries no apologetic footnote.
 */
export function cappedListNote(shown: number, total: number): string | null {
  if (total <= shown) {
    return null;
  }
  return `Mostrando ${shown.toLocaleString("es-CL")} de ${total.toLocaleString("es-CL")}`;
}

export function cardCount(counts: V2CardCounts, key: string, shown: number): number {
  const declared = counts[key];
  // A missing count is a response shape we do not understand. Falling back to what is on
  // screen is the only number we can defend.
  return typeof declared === "number" && declared >= shown ? declared : shown;
}

/**
 * Why a contact card has no person, said in the card rather than left as a blank field.
 *
 * The Wave 1A/1B evidence carries no human name at all, so a personal-looking address is
 * recorded as an unattributed channel instead of having a name derived from its local
 * part. A blank "Persona" field would read as missing data; this says it is a decision.
 */
export function noPersonExplanation(usage: V2ContactUsage): string {
  if (usage === "shared_mailbox") {
    return "Es un buzón de rol, no una persona. No se le asigna un titular.";
  }
  return "La evidencia no trae ningún nombre: derivarlo de la dirección sería una inferencia de identidad, no un dato.";
}

/**
 * Why a contact card has no organization.
 *
 * A mail domain is a routing hint and never an identity key on its own, so no channel was
 * attached to an institution merely for sharing its domain.
 */
export const NO_ORGANIZATION_EXPLANATION =
  "El dominio de correo es una pista de ruteo, no una clave de identidad: no se vincula una institución sólo por compartirlo.";
