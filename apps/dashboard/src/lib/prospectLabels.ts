/** Operator-friendly labels for Prospectos (Phase 10D.1). */

import type { LeadProspectDetailUi, LeadProspectListItemUi } from "../api/leadIntelTypes";

export const CLASSIFICATION_LABELS: Record<string, string> = {
  net_new_safe_review: "Prospecto nuevo seguro",
  same_domain_contacted_review: "Revisar historial",
  contacted_by_domain_review: "Contactado por dominio",
  public_tender_review: "Licitación / compra pública",
  research_only_contact_needed: "Falta contacto directo",
  old_gmail_prospect_review: "Gmail histórico — revisión",
  old_followup_review: "Follow-up antiguo — revisión",
  active_case_hold: "Caso activo — hold personalizado",
  already_contacted_block: "No contactar: ya contactado",
  bounced_block: "No contactar: rebote",
  suppressed_block: "No contactar: suprimido",
  manual_outreach_sent: "Contactado (outreach manual)",
  bounced_suppressed: "Rebotado / suprimido",
  supplier_or_internal_block: "No contactar: proveedor / interno",
  legacy_contact_review: "Base antigua 2016–2019 — revisión",
};

export const SOURCE_TYPE_LABELS: Record<string, string> = {
  deepsearch: "Investigación DeepSearch",
  gmail_historico: "Gmail histórico",
  followup_antiguo: "Follow-up antiguo",
  caso_activo: "Caso activo",
  legacy_2016_2019: "Base antigua 2016–2019",
};

export const BUYER_TYPE_LABELS: Record<string, string> = {
  laboratorio_acuicola: "Laboratorio acuícola",
  laboratorio_alimentos: "Laboratorio de alimentos",
  laboratorio_privado: "Laboratorio privado",
  laboratorio_agua: "Laboratorio de agua",
  laboratorio_ambiental: "Laboratorio ambiental",
  centro_ensayos: "Centro de ensayos / calibración",
  centro_investigacion: "Centro de investigación",
  laboratorio_universitario: "Laboratorio universitario",
  instituto_investigacion: "Instituto de investigación",
  instituto_publico_investigacion: "Instituto público de investigación",
  public_tender: "Compra pública / licitación",
  public_tender_opportunity: "Compra pública / licitación",
  qc_alimentos: "Control de calidad alimentos",
};

export const CAMPAIGN_BUCKET_LABELS: Record<string, string> = {
  private_lab: "Laboratorio privado",
  university: "Universidad / investigación",
  same_domain: "Revisar historial",
  public_tender: "Licitación",
  water_environmental: "Agua / ambiental",
  aquaculture_salmon: "Acuícola / salmones",
  blocked: "Bloqueado",
  other: "Otro",
};

export const RISK_FLAG_LABELS: Record<string, string> = {
  lead_status_net_new_candidate: "Nuevo según investigación",
  "lead_status=net_new_candidate": "Nuevo según investigación",
  dominio_en_historial_origenlab: "Dominio con historial OrigenLab",
  sin_email_publico: "Sin email público",
  validar_contacto_publico: "Validar contacto",
  mismo_dominio_ya_contactado: "Mismo dominio ya contactado",
  dominio_con_envios_previos: "Dominio con envíos previos",
  same_organization_review: "Revisar organización similar",
  low_fit: "Encaje bajo",
  prospecto_nuevo_seguro: "Prospecto nuevo seguro",
  licitacion_publica: "Licitación pública",
  falta_contacto: "Falta contacto directo",
};

export type ProspectDecisionTone = "safe" | "caution" | "blocked";

export interface ProspectDecisionBanner {
  label: string;
  tone: ProspectDecisionTone;
  testId: string;
}

export function prospectClassificationLabel(code: string): string {
  return CLASSIFICATION_LABELS[code] ?? code.replaceAll("_", " ");
}

export function prospectSourceTypeLabel(sourceType: string | null | undefined): string {
  if (!sourceType?.trim()) return "—";
  return SOURCE_TYPE_LABELS[sourceType] ?? sourceType.replaceAll("_", " ");
}

export function prospectOriginChip(row: LeadProspectListItemUi): ProspectTableBadge {
  const source = row.source_type?.trim();
  if (source && SOURCE_TYPE_LABELS[source]) {
    switch (source) {
      case "gmail_historico":
        return { label: "Gmail histórico", className: "bg-sky-100 text-sky-950 border-sky-200" };
      case "followup_antiguo":
        return { label: "Follow-up antiguo", className: "bg-violet-100 text-violet-950 border-violet-200" };
      case "caso_activo":
        return { label: "Caso activo", className: "bg-slate-200 text-slate-900 border-slate-300" };
      case "deepsearch":
        return { label: "DeepSearch", className: "bg-teal-50 text-teal-900 border-teal-200" };
      case "legacy_2016_2019":
        return {
          label: "Base antigua 2016–2019",
          className: "bg-amber-50 text-amber-950 border-amber-200",
        };
      default:
        break;
    }
  }
  return { label: prospectSourceTypeLabel(source), className: "bg-slate-100 text-slate-800 border-slate-200" };
}

export function prospectBuyerTypeLabel(buyerType: string | null | undefined): string {
  if (!buyerType?.trim()) return "Sin clasificar";
  return BUYER_TYPE_LABELS[buyerType] ?? buyerType.replaceAll("_", " ");
}

export function prospectCampaignBucketLabel(bucket: string | null | undefined): string {
  if (!bucket?.trim()) return "—";
  return CAMPAIGN_BUCKET_LABELS[bucket] ?? bucket.replaceAll("_", " ");
}

export function parseRiskFlagChips(raw: string | null | undefined): { code: string; label: string }[] {
  if (!raw?.trim()) return [];
  const seen = new Set<string>();
  const chips: { code: string; label: string }[] = [];
  for (const part of raw.split(",")) {
    const code = part.trim();
    if (!code || seen.has(code)) continue;
    seen.add(code);
    const normalized = code.replace(/^lead_status=/, "lead_status_");
    chips.push({
      code,
      label: RISK_FLAG_LABELS[code] ?? RISK_FLAG_LABELS[normalized] ?? humanizeToken(code),
    });
  }
  return chips;
}

function humanizeToken(token: string): string {
  return token
    .replace(/^lead_status=/, "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export function evidenceSourceLabel(
  source: string | null | undefined,
  evidenceUrl: string | null | undefined,
): string {
  const src = (source ?? "").toLowerCase();
  const url = (evidenceUrl ?? "").toLowerCase();
  if (src.includes("sernapesca") || url.includes("sernapesca")) return "SERNAPESCA";
  if (src.includes("sag") || url.includes("sag.gob")) return "SAG";
  if (src.includes("inn") || url.includes("inn.cl")) return "INN";
  if (src.includes("anid") || url.includes("anid")) return "ANID";
  if (src.includes("mercadopublico") || url.includes("mercadopublico")) return "Mercado Público";
  if (src.includes("sitio_oficial") || src.includes("sitio oficial")) return "Sitio oficial";
  if (url.includes(".gob.cl")) return "Fuente pública (.gob.cl)";
  if (src) return src.replaceAll("_", " ");
  if (evidenceUrl) {
    try {
      const host = new URL(evidenceUrl).hostname.replace(/^www\./, "");
      return host || "Sitio web";
    } catch {
      return "Sitio web";
    }
  }
  return "Evidencia pública";
}

export function prospectDecisionBanner(
  classification: string,
  isBlocked: boolean,
): ProspectDecisionBanner {
  if (isBlocked) {
    return {
      label: "No contactar",
      tone: "blocked",
      testId: "prospect-decision-banner",
    };
  }
  switch (classification) {
    case "net_new_safe_review":
      return {
        label: "Acción sugerida: preparar correo personalizado",
        tone: "safe",
        testId: "prospect-decision-banner",
      };
    case "same_domain_contacted_review":
      return {
        label: "Acción sugerida: revisar historial antes de escribir",
        tone: "caution",
        testId: "prospect-decision-banner",
      };
    case "public_tender_review":
      return {
        label: "Acción sugerida: revisar bases / preparar ficha técnica",
        tone: "caution",
        testId: "prospect-decision-banner",
      };
    case "research_only_contact_needed":
      return {
        label: "Acción sugerida: buscar contacto directo",
        tone: "caution",
        testId: "prospect-decision-banner",
      };
    case "old_gmail_prospect_review":
      return {
        label: "Acción sugerida: revisar historial Gmail antes de presentación",
        tone: "caution",
        testId: "prospect-decision-banner",
      };
    case "old_followup_review":
      return {
        label: "Acción sugerida: seguimiento personalizado (no correo frío)",
        tone: "caution",
        testId: "prospect-decision-banner",
      };
    case "active_case_hold":
      return {
        label: "Hold personalizado — no envío genérico ni campaña masiva",
        tone: "caution",
        testId: "prospect-decision-banner",
      };
    case "manual_outreach_sent":
      return {
        label: "Contactado — esperar respuesta; no reenviar ahora",
        tone: "caution",
        testId: "prospect-decision-banner",
      };
    case "bounced_suppressed":
      return {
        label: "No contactar",
        tone: "blocked",
        testId: "prospect-decision-banner",
      };
    default:
      return {
        label: "Acción sugerida: revisar antes de contactar",
        tone: "caution",
        testId: "prospect-decision-banner",
      };
  }
}

export type ProspectTableBadge = {
  label: string;
  className: string;
};

export function prospectTableBadge(row: LeadProspectListItemUi): ProspectTableBadge | null {
  if (row.is_blocked) {
    return {
      label: "No contactar",
      className: "bg-red-100 text-red-900 border-red-200",
    };
  }
  if (row.classification === "net_new_safe_review") {
    return { label: "Nuevo seguro", className: "bg-teal-100 text-teal-900 border-teal-200" };
  }
  if (row.classification === "same_domain_contacted_review") {
    return { label: "Revisar historial", className: "bg-amber-100 text-amber-950 border-amber-200" };
  }
  if (row.classification === "public_tender_review") {
    return { label: "Licitación", className: "bg-amber-100 text-amber-950 border-amber-200" };
  }
  if (row.classification === "research_only_contact_needed") {
    return { label: "Falta email", className: "bg-amber-100 text-amber-950 border-amber-200" };
  }
  if (row.classification === "old_gmail_prospect_review") {
    return { label: "Gmail histórico", className: "bg-sky-100 text-sky-950 border-sky-200" };
  }
  if (row.classification === "old_followup_review") {
    return { label: "Follow-up antiguo", className: "bg-violet-100 text-violet-950 border-violet-200" };
  }
  if (row.classification === "active_case_hold") {
    return { label: "Hold personalizado", className: "bg-slate-200 text-slate-900 border-slate-300" };
  }
  if (row.classification === "manual_outreach_sent") {
    return { label: "Contactado", className: "bg-emerald-100 text-emerald-950 border-emerald-200" };
  }
  if (row.classification === "bounced_suppressed") {
    return { label: "Rebotado", className: "bg-red-100 text-red-900 border-red-200" };
  }
  return null;
}

export function prospectEmptyEmailDisplayHint(classification: string): string | null {
  if (classification === "same_domain_contacted_review") {
    return "No hay email en la fila, pero existe contacto previo con el mismo dominio.";
  }
  return null;
}

export function prospectContactCell(row: LeadProspectListItemUi): string {
  if (!row.email?.trim()) {
    if (row.classification === "same_domain_contacted_review") {
      return "Contactado por dominio — sin email en fila";
    }
    return "Sin email — investigar contacto";
  }
  const name = row.contact_name?.trim();
  return name ? `${name} · ${row.email}` : row.email;
}

export function hasProspectEmail(prospect: LeadProspectDetailUi | LeadProspectListItemUi): boolean {
  return Boolean(prospect.email?.trim());
}

export function decisionBannerClassName(tone: ProspectDecisionTone): string {
  switch (tone) {
    case "safe":
      return "border-teal-200 bg-teal-50 text-teal-950";
    case "blocked":
      return "border-red-200 bg-red-50 text-red-900";
    default:
      return "border-amber-200 bg-amber-50 text-amber-950";
  }
}

export function prospectOperationalNextAction(row: LeadProspectListItemUi): string {
  if (row.operational_next_action?.trim()) {
    return row.operational_next_action.trim();
  }
  if (row.recommended_next_action?.trim()) {
    return row.recommended_next_action.trim();
  }
  return "—";
}

export function prospectGmailHistoryCell(row: LeadProspectListItemUi): string {
  const sent = row.gmail_sent_count ?? 0;
  const received = row.gmail_received_count ?? 0;
  const lastContacted = row.gmail_last_contacted_at?.trim();
  const subject = row.gmail_latest_subject_safe?.trim();
  if (!sent && !received && !lastContacted && !subject) {
    return "—";
  }
  const counts: string[] = [];
  if (sent > 0) counts.push(`↑${sent}`);
  if (received > 0) counts.push(`↓${received}`);
  const countLabel = counts.join(" ");
  if (subject) {
    const clipped = subject.length > 48 ? `${subject.slice(0, 45)}…` : subject;
    return countLabel ? `${countLabel} · ${clipped}` : clipped;
  }
  if (lastContacted) {
    return countLabel ? `${countLabel} · ${lastContacted}` : lastContacted;
  }
  return countLabel || "—";
}
