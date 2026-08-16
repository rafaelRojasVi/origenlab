/**
 * UI label mappings for known W1 and preview tokens (institution/procurement
 * queue intel raw machine tokens → Spanish labels).
 *
 * Same pattern as operatorLabels.ts (raw machine token in, Spanish label out)
 * but kept in a separate file/module — not merged into the real
 * operatorLabels.ts, since some entries here still cover mock-only preview
 * tokens (see per-dictionary comments below) alongside the tokens confirmed
 * against live W1 production data, and mixing those into the label tables
 * that back other live production features would blur that distinction.
 */

import type {
  AxisBand,
  ContactGapStatus,
  DeliveryDaysValue,
  DemandRecurrence,
  IdentityKind,
  ProcurementEligibilityStatus,
  TermFactState,
} from "../api/institutionIntel/types";

export const AXIS_BAND_LABEL: Record<AxisBand, string> = {
  none: "Sin datos",
  low: "Baja",
  medium: "Media",
  high: "Alta",
};

export const ELIGIBILITY_LABEL: Record<ProcurementEligibilityStatus, string> = {
  open_public: "Abierto / público",
  restricted_invitation_unconfirmed: "Restringido — no confirmado",
  unknown: "Método desconocido",
};

export const IDENTITY_KIND_LABEL: Record<IdentityKind, string> = {
  origenlab_account: "Cuenta OrigenLab",
  chilecompra_buyer_source_id: "ID de comprador ChileCompra",
  normalized_buyer_name: "Nombre normalizado",
  unresolved_buyer: "Comprador sin resolver",
  identity_review: "Identidad en revisión",
};

export const CONTACT_GAP_STATUS_LABEL: Record<ContactGapStatus, string> = {
  existing_verified_contact: "Contacto verificado",
  existing_contact_needs_role_review: "Rol por confirmar",
  role_known_email_missing: "Rol conocido, falta correo",
  contacts_present_but_blocked: "Contactos bloqueados",
  linked_account_no_contact: "Cuenta vinculada sin contacto",
  account_unlinked: "Cuenta no vinculada",
  account_ambiguous: "Cuenta ambigua",
};

export const DEMAND_RECURRENCE_LABEL: Record<DemandRecurrence, string> = {
  observed_once_confirmed: "Vista una vez",
  repeated_observed_demand_confirmed: "Demanda repetida",
  repeated_confirmed_with_unresolved_relationships: "Repetida — relación por confirmar",
  recurrence_not_established: "Recurrencia no establecida",
};

export const TERM_FACT_STATE_LABEL: Record<TermFactState, string> = {
  explicit: "Confirmado",
  derived: "Calculado",
  not_explicitly_found: "No especificado",
  unknown: "Sin datos",
  conflicting: "Fuentes discrepan",
};

/**
 * Known T1 tender-term `field_name` tokens -> Spanish operator-facing
 * labels. Confirmed against T1's `commercial_procurement_anexo_tender_terms`
 * extractor rules (apps/email-pipeline/.../extract.py `_RULES`). Any field
 * name not in this map falls back to `humanizeToken()` below rather than
 * showing the raw snake_case wire token in the UI.
 */
const TENDER_TERM_FIELD_LABEL: Record<string, string> = {
  contract_duration_months: "Duración del contrato",
  faithful_performance_guarantee_percent: "Garantía de fiel cumplimiento",
  maximum_delivery_days: "Plazo máximo de entrega",
  offer_validity_days: "Vigencia de la oferta",
  payment_deadline_days: "Plazo de pago",
  total_budget: "Presupuesto total",
};

export function formatTenderTermField(raw: string): string {
  return TENDER_TERM_FIELD_LABEL[raw] ?? humanizeToken(raw);
}

/**
 * T1 wire `unit` tokens are English machine words ("days"/"months"/
 * "percent") straight from the extractor's `_Rule.unit` — localize them for
 * display rather than showing e.g. "8 months". Unknown units fall back to
 * the raw token rather than being dropped silently.
 */
const TERM_UNIT_LABEL: Record<string, string> = {
  days: "días",
  months: "meses",
  percent: "%",
};

export function formatTermUnit(raw: string): string {
  return TERM_UNIT_LABEL[raw] ?? raw;
}

/**
 * `maximum_delivery_days` publishes a structured `{days, day_basis}` value
 * instead of a plain number. Only the two real `day_basis` values T1's
 * extractor ever emits ("business"/"calendar") are recognized here — any
 * other basis, including a genuinely unconfirmed/null one, fails closed to
 * `null` so the caller renders "—" instead of guessing at an unconfirmed
 * day basis or ever stringifying the raw object.
 */
const DAY_BASIS_LABEL: Record<string, string> = {
  business: "hábiles",
  calendar: "corridos",
};

export function formatDeliveryDaysValue(value: DeliveryDaysValue): string | null {
  if (!Number.isFinite(value.days)) return null;
  if (value.day_basis === null) return null;
  const basisLabel = DAY_BASIS_LABEL[value.day_basis];
  if (!basisLabel) return null;
  return `${value.days.toLocaleString("es-CL")} días ${basisLabel}`;
}

const NEXT_ACTION_LABEL: Record<string, string> = {
  resolve_account: "Resolver cuenta",
  review_account_identity: "Revisar identidad de cuenta",
  use_existing_verified_contact: "Usar contacto verificado",
  review_existing_contacts: "Revisar contactos existentes",
  research_new_contact: "Investigar nuevo contacto",
  review_open_opportunity: "Revisar oportunidad abierta",
  monitor_historical_prospect: "Monitorear prospecto histórico",
  none: "Sin acción pendiente",
};

export function formatNextAction(raw: string): string {
  return NEXT_ACTION_LABEL[raw] ?? raw;
}

const QUEUE_LABEL: Record<string, string> = {
  current_opportunity_queue: "Oportunidad actual",
  historical_prospect_queue: "Prospecto histórico",
  contact_gap_queue: "Brecha de contacto",
  institution_match_review_queue: "Revisar identidad",
  line_evidence_review_queue: "Revisión de evidencia",
  retender_review_queue: "Revisar reoferta",
};

export function formatQueueName(raw: string): string {
  return QUEUE_LABEL[raw] ?? raw;
}

const REVIEW_TOKEN_LABEL: Record<string, string> = {
  ambiguous: "Ambigua",
  review_required: "Revisión requerida",
  retender_review_required: "Revisión de reoferta requerida",
  review_only_fragmented_identity: "Identidad fragmentada — revisión manual",
  recurrence_not_established: "Recurrencia no establecida",
};

export function formatReviewToken(raw: string): string {
  return REVIEW_TOKEN_LABEL[raw] ?? humanizeToken(raw);
}

/**
 * Axis reason codes. The "Confirmed real tokens" block below is verified
 * against live W1 production data (apps/api/docs/INSTITUTION_PROSPECT_API_CONTRACT.md);
 * the remaining entries are earlier mock-fixture placeholders kept as
 * harmless extras. Not exhaustive either way — extend as new real or preview
 * reason codes come up.
 */
const AXIS_REASON_CODE_LABEL: Record<string, string> = {
  // Confirmed real tokens (apps/api/docs/INSTITUTION_PROSPECT_API_CONTRACT.md, W1):
  has_equipment_purchase_evidence: "Evidencia de compra de equipos",
  open_purchase_tender_present: "Licitación de compra abierta",
  recent_observation: "Observación reciente",
  projected_lifecycle_precedence_applied: "Estado proyectado a partir de la mejor evidencia",
  open_tender_present: "Licitación abierta ahora",
  current_opportunity_like_evidence: "Evidencia de oportunidad actual",
  closing_soon: "Cierra pronto",
  linked_account: "Cuenta vinculada",
  linked_account_no_contact: "Cuenta vinculada sin contacto",
  // Pre-real-contract placeholders, kept as harmless extra entries:
  multiple_purchase_tenders: "Varias licitaciones con evidencia de compra",
  repeated_category_demand: "Categoría repetida",
  single_purchase_tender: "Una licitación con evidencia de compra",
  account_unlinked: "Cuenta no vinculada",
  zero_verified_contacts: "0 contactos verificados",
  verified_contacts: "Contactos verificados",
};

export function formatAxisReasonCode(raw: string): string {
  return AXIS_REASON_CODE_LABEL[raw] ?? humanizeToken(raw);
}

/** Generic fallback for any raw token without a specific dictionary yet — humanizes snake_case rather than showing it raw. */
export function humanizeToken(raw: string): string {
  return raw
    .split("_")
    .map((part) => (part.length ? part[0].toUpperCase() + part.slice(1) : part))
    .join(" ");
}

/**
 * Equipment category tokens seen across live W1 production data and mock
 * fixtures. Not exhaustive — anything missing falls back to humanizeToken()
 * rather than showing the raw token, but that fallback reads as English in
 * an otherwise Spanish UI, so extend this as new categories come up (real or
 * preview) rather than leaning on the fallback long-term.
 */
const EQUIPMENT_CATEGORY_LABEL: Record<string, string> = {
  balance: "Balanza",
  centrifuge: "Centrífuga",
  analytical_balance: "Balanza analítica",
  homogenizer: "Homogeneizador",
};

export function formatEquipmentCategory(raw: string): string {
  return EQUIPMENT_CATEGORY_LABEL[raw] ?? humanizeToken(raw);
}
