/** Client-side warm queue view presets (no API calls). */

import type { WarmCaseCategory, WarmCaseItem } from "../api/commercialTypes";
import { emailDomain } from "./clientTableView";

const CYBERDAY_SUBJECT_NEEDLE = "cyberday";

function looksLikeCyberdayCampaignRow(row: WarmCaseItem): boolean {
  return row.subject.toLowerCase().includes(CYBERDAY_SUBJECT_NEEDLE);
}

export type WarmCaseViewPreset =
  | "clientes_reales"
  | "proveedores"
  | "pagos_admin"
  | "logistica"
  | "con_senal_equipo"
  | "campanas_marketing"
  | "todo";

export const DEFAULT_WARM_VIEW_PRESET: WarmCaseViewPreset = "clientes_reales";

export const WARM_VIEW_PRESET_LABELS: Record<WarmCaseViewPreset, string> = {
  clientes_reales: "Clientes reales",
  proveedores: "Proveedores",
  pagos_admin: "Pagos/admin",
  logistica: "Logística",
  con_senal_equipo: "Con señal de equipo",
  campanas_marketing: "Campañas / marketing",
  todo: "Todo",
};

export const WARM_VIEW_PRESET_ORDER: WarmCaseViewPreset[] = [
  "clientes_reales",
  "proveedores",
  "pagos_admin",
  "logistica",
  "con_senal_equipo",
  "campanas_marketing",
  "todo",
];

const CLIENTES_REALES_CATEGORIES: ReadonlySet<WarmCaseCategory> = new Set([
  "client_opportunity",
  "client_response",
  "deal_evidence_candidate",
  "client_reply",
  "quote_sent",
  "waiting_client",
]);

const SUPPLIER_VENDOR_DOMAINS: ReadonlySet<string> = new Set([
  "ollital.com",
  "serva.de",
  "ortoalresa.com",
  "dlabsci.com",
  "crtopmachine.com",
  "asynt.com",
  "eppendorf.com",
  "ika.net.br",
  "hielscher.com",
  "valuenindustrial.com",
  "gzfanbolun.com",
  "yuanhuai.com",
]);

const REAL_CLIENT_DOMAINS: ReadonlySet<string> = new Set(["ceaf.cl"]);

/** Defense-in-depth: rows that must not show under Clientes reales. */
export function isExcludedFromClientesReales(row: WarmCaseItem): boolean {
  const domain = emailDomain(row.contact_email);
  const email = row.contact_email.trim().toLowerCase();
  const hay = [row.contact_email, row.subject, row.snippet].join(" ").toLowerCase();

  if (email === "contacto@origenlab.cl" || domain === "origenlab.cl" || domain === "labdelivery.cl") {
    return true;
  }
  if (domain === "accounts.google.com" || hay.includes("alerta de seguridad")) {
    return true;
  }
  if (SUPPLIER_VENDOR_DOMAINS.has(domain)) {
    return true;
  }
  if (hay.includes("confirm your registration") || hay.includes("please confirm your registration")) {
    return true;
  }
  if (
    row.category === "campaign_outreach" ||
    row.category === "waiting_campaign_reply" ||
    row.category === "auto_acknowledgement" ||
    looksLikeCyberdayCampaignRow(row)
  ) {
    return true;
  }
  if (
    row.category === "payment_admin" ||
    row.category === "logistics_admin" ||
    row.category === "vendor_logistics" ||
    row.category === "internal_admin" ||
    row.category === "system_noise" ||
    row.category === "supplier_quote_received" ||
    row.category === "supplier_followup" ||
    row.category === "supplier_reply"
  ) {
    return true;
  }
  if (matchesPagosAdminSignals(row)) {
    return true;
  }
  return false;
}

function matchesRealClientPostSale(row: WarmCaseItem): boolean {
  const domain = emailDomain(row.contact_email);
  if (!REAL_CLIENT_DOMAINS.has(domain)) {
    return false;
  }
  const hay = [row.subject, row.snippet].join(" ").toLowerCase();
  return hay.includes("remite oc") || hay.includes("orden de compra");
}

const LOGISTICS_DOMAINS: ReadonlySet<string> = new Set(["dhl.com"]);

function haystackIncludesLogistics(row: WarmCaseItem): boolean {
  const hay = [row.subject, row.snippet, row.account_name].join(" ").toLowerCase();
  return (
    hay.includes("dhl") ||
    hay.includes("cuenta importación") ||
    hay.includes("cuenta importacion") ||
    hay.includes("propuesta comercial dhl") ||
    hay.includes("solicitud cuenta")
  );
}

const PAYMENT_ADMIN_DOMAINS: ReadonlySet<string> = new Set(["bancochile.cl"]);

const PAYMENT_ADMIN_CATEGORIES: ReadonlySet<WarmCaseCategory> = new Set(["payment_admin"]);

function pagosAdminTextHaystack(row: WarmCaseItem): string {
  return [row.subject, row.snippet, row.account_name].join(" ").toLowerCase();
}

function matchesPagosAdminSignals(row: WarmCaseItem): boolean {
  const hay = pagosAdminTextHaystack(row);
  return (
    hay.includes("datos bancarios") ||
    hay.includes("solicita datos banc") ||
    hay.includes("cuenta corriente") ||
    hay.includes("beneficiario") ||
    hay.includes("factura n°") ||
    hay.includes("factura nº") ||
    hay.includes("factura n ") ||
    hay.includes("proceder al pago") ||
    hay.includes("comprobante de transferencia") ||
    hay.includes("transferencia") ||
    hay.includes("registrarla en nuestro sistema") ||
    hay.includes("registrar en nuestro sistema") ||
    hay.includes("factura") ||
    hay.includes("pago")
  );
}

function matchesPagosAdminPreset(row: WarmCaseItem): boolean {
  if (PAYMENT_ADMIN_CATEGORIES.has(row.category)) {
    return true;
  }
  if (PAYMENT_ADMIN_DOMAINS.has(emailDomain(row.contact_email))) {
    return true;
  }
  return matchesPagosAdminSignals(row);
}

export function matchesWarmCaseViewPreset(
  row: WarmCaseItem,
  preset: WarmCaseViewPreset,
): boolean {
  if (preset === "todo") {
    return true;
  }

  const category = row.category;
  const domain = emailDomain(row.contact_email);

  switch (preset) {
    case "clientes_reales":
      if (isExcludedFromClientesReales(row)) {
        return false;
      }
      if (CLIENTES_REALES_CATEGORIES.has(category)) {
        return true;
      }
      return category === "waiting_supplier" && matchesRealClientPostSale(row);

    case "proveedores":
      if (category === "vendor_logistics" || category === "logistics_admin") {
        return false;
      }
      if (
        category === "supplier_reply" ||
        category === "supplier_quote_received" ||
        category === "supplier_followup"
      ) {
        return true;
      }
      if (SUPPLIER_VENDOR_DOMAINS.has(domain)) {
        return true;
      }
      if (isExcludedFromClientesReales(row) && category === "client_reply") {
        return true;
      }
      return false;

    case "pagos_admin":
      return matchesPagosAdminPreset(row);

    case "logistica":
      if (category === "vendor_logistics" || category === "logistics_admin") {
        return true;
      }
      if (LOGISTICS_DOMAINS.has(domain)) {
        return true;
      }
      return (
        haystackIncludesLogistics(row) &&
        category !== "auto_reply" &&
        category !== "system_noise" &&
        !isExcludedFromClientesReales(row)
      );

    case "con_senal_equipo":
      return Boolean(row.equipment_signal?.trim());

    case "campanas_marketing":
      return (
        category === "campaign_outreach" ||
        category === "waiting_campaign_reply" ||
        category === "auto_acknowledgement" ||
        looksLikeCyberdayCampaignRow(row)
      );

    default:
      return true;
  }
}

export function filterWarmCasesByViewPreset(
  items: WarmCaseItem[],
  preset: WarmCaseViewPreset,
): WarmCaseItem[] {
  if (preset === "todo") {
    return items;
  }
  return items.filter((row) => matchesWarmCaseViewPreset(row, preset));
}
