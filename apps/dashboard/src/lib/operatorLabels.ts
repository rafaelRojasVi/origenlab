/** Etiquetas en español para tokens de la API (solo lectura). */

import { safePreviewText, truncate } from "./safeText";

export type OperatorLabelKind =
  | "warm_status"
  | "warm_category"
  | "warm_next_action"
  | "equipment_next_action"
  | "equipment_contact_status"
  | "equipment_safe_channel"
  | "equipment_category";

const WARM_STATUS: Record<string, string> = {
  new: "Nuevo",
  open: "Abierto",
  waiting: "En espera",
  quoted: "Cotizado",
  problem: "Problema",
};

/** Severity-coded badge classes for the warm-case status column (kept separate from the label map since color is presentation, not translation). */
const WARM_STATUS_BADGE_CLASS: Record<string, string> = {
  problem: "rounded bg-red-50 px-2 py-0.5 text-xs font-medium text-red-800 ring-1 ring-red-200",
  waiting: "rounded bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-800 ring-1 ring-amber-200",
  quoted: "rounded bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-800 ring-1 ring-emerald-200",
  new: "rounded bg-sky-50 px-2 py-0.5 text-xs font-medium text-sky-800 ring-1 ring-sky-200",
};

const WARM_STATUS_BADGE_CLASS_DEFAULT =
  "rounded bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-800";

export function warmStatusBadgeClass(token: string | null | undefined): string {
  return WARM_STATUS_BADGE_CLASS[normalizeToken((token || "").trim())] ?? WARM_STATUS_BADGE_CLASS_DEFAULT;
}

const WARM_CATEGORY: Record<string, string> = {
  client_opportunity: "Oportunidad de cliente",
  client_response: "Respuesta de cliente",
  supplier_quote_received: "Cotización de proveedor recibida",
  supplier_followup: "Seguimiento de proveedor",
  payment_admin: "Pago / administración",
  logistics_admin: "Logística",
  internal_admin: "Interno / administrativo",
  system_noise: "Sistema / ruido",
  bounce_problem: "Problema de entrega",
  deal_evidence_candidate: "Evidencia de negocio",
  client_reply: "Respuesta de cliente",
  supplier_reply: "Respuesta de proveedor",
  quote_sent: "Cotización enviada",
  waiting_supplier: "Esperando proveedor",
  waiting_client: "Esperando cliente",
  campaign_outreach: "Campaña / outreach",
  waiting_campaign_reply: "Campaña — esperando respuesta",
  auto_acknowledgement: "Acuse automático",
  bounce: "Rebote",
  opportunity: "Oportunidad",
  auto_reply: "Respuesta automática",
  vendor_logistics: "Logística",
};

const WARM_NEXT_ACTION: Record<string, string> = {
  auto_reply: "Ignorar respuesta automática",
  vendor_logistics: "Revisar logística o importación",
  payment_admin: "Registrar o confirmar pago",
  supplier_reply: "Revisar propuesta del proveedor",
  client_reply: "Responder al cliente",
  reply: "Responder",
  wait: "Esperar",
  review: "Revisar",
  follow: "Dar seguimiento",
};

const EQUIPMENT_NEXT_ACTION: Record<string, string> = {
  quote_now: "Cotizar ahora",
  needs_supplier_quote: "Requiere cotización de proveedor",
  monitor: "Monitorear",
  contact_after_close: "Revisar después del cierre",
};

const EQUIPMENT_CONTACT_STATUS: Record<string, string> = {
  no_verified_buyer_email: "Sin correo verificado del comprador",
  pending: "Pendiente",
  review_required: "Revisión requerida",
};

const EQUIPMENT_SAFE_CHANNEL: Record<string, string> = {
  mercado_publico_bid: "Mercado Público",
  mercado_publico_only: "Mercado Público",
  supplier_quote_request: "Cotización a proveedor",
};

const EQUIPMENT_CATEGORY: Record<string, string> = {
  centrifuge: "Centrífuga",
  balance: "Balanza",
  incubator: "Incubadora",
  lab_ultrasonic_processor: "Procesador ultrasónico",
  homogenizer: "Homogeneizador / agitador",
  osmometer: "Osmómetro",
};

const TABLES: Record<OperatorLabelKind, Record<string, string>> = {
  warm_status: WARM_STATUS,
  warm_category: WARM_CATEGORY,
  warm_next_action: WARM_NEXT_ACTION,
  equipment_next_action: EQUIPMENT_NEXT_ACTION,
  equipment_contact_status: EQUIPMENT_CONTACT_STATUS,
  equipment_safe_channel: EQUIPMENT_SAFE_CHANNEL,
  equipment_category: EQUIPMENT_CATEGORY,
};

function normalizeToken(raw: string): string {
  return raw.trim().toLowerCase().replace(/\s+/g, "_");
}

function isActionKind(kind: OperatorLabelKind): boolean {
  return kind === "warm_next_action" || kind === "equipment_next_action";
}

function looksHumanReadableAction(raw: string): boolean {
  const trimmed = raw.trim();
  if (!trimmed) {
    return false;
  }
  return (
    /\s/.test(trimmed) ||
    /[.;,:¿?¡!—-]/.test(trimmed) ||
    /[áéíóúñÁÉÍÓÚÑ]/.test(trimmed)
  );
}

const EQUIPMENT_CATEGORY_SEPARATORS = /[;,|]/;

function formatEquipmentCategory(original: string): { label: string; raw: string; title?: string } {
  const parts = original
    .split(EQUIPMENT_CATEGORY_SEPARATORS)
    .map((part) => part.trim())
    .filter(Boolean);
  if (parts.length === 0) {
    return { label: "—", raw: "" };
  }

  const labels: string[] = [];
  const unmappedTokens: string[] = [];

  for (const part of parts) {
    const token = normalizeToken(part);
    const mapped = EQUIPMENT_CATEGORY[token];
    if (mapped) {
      labels.push(mapped);
      continue;
    }
    labels.push("Sin clasificar");
    unmappedTokens.push(token);
  }

  const raw = parts.map((part) => normalizeToken(part)).join(";");
  const label = labels.join("; ");
  if (unmappedTokens.length === 0) {
    return { label, raw };
  }
  return {
    label,
    raw,
    title: `${UNMAPPED_OPERATOR_TOKEN_TITLE} (${unmappedTokens.join(", ")})`,
  };
}

/** Etiqueta visible; no expone el token técnico en la UI. */
export const UNMAPPED_OPERATOR_TOKEN_TITLE =
  "Etiqueta no mapeada en la interfaz; revisar token técnico si se repite.";

export function formatOperatorToken(
  raw: string | null | undefined,
  kind: OperatorLabelKind,
): { label: string; raw: string; title?: string } {
  const original = (raw || "").trim();
  if (!original) {
    return { label: "—", raw: "" };
  }
  if (kind === "equipment_category") {
    return formatEquipmentCategory(original);
  }
  const token = normalizeToken(original);
  const table = TABLES[kind];
  const mapped = table[token];
  if (mapped) {
    return { label: mapped, raw: token };
  }
  if (isActionKind(kind) && looksHumanReadableAction(original)) {
    const label = truncate(safePreviewText(original, 180), 180);
    return {
      label,
      raw: token,
      title: original,
    };
  }
  return {
    label: "Sin clasificar",
    raw: token,
    title: `${UNMAPPED_OPERATOR_TOKEN_TITLE} (${token})`,
  };
}
