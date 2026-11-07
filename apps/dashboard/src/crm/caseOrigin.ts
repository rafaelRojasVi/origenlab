/**
 * The channels a commercial case can come from (`crm.opportunity.origin`), said once for the
 * dashboard. Mirrors `apps/api/src/origenlab_api/v2/case_origin.py` (a test reads that file, so
 * the two cannot drift). A case nobody classified has no origin: «Sin origen».
 */

export const CASE_ORIGINS = [
  "correo",
  "web",
  "whatsapp",
  "campana",
  "mercado_publico",
  "fabricante",
  "recomendacion",
  "llamada",
  "ex_labdelivery",
] as const;

export type CaseOrigin = (typeof CASE_ORIGINS)[number];

export const ORIGIN_LABEL: Record<CaseOrigin, string> = {
  correo: "Correo",
  web: "Web",
  whatsapp: "WhatsApp",
  campana: "Campaña",
  mercado_publico: "Mercado Público",
  fabricante: "Fabricante",
  recomendacion: "Recomendación",
  llamada: "Llamada",
  ex_labdelivery: "Ex Labdelivery",
};

export const NO_ORIGIN_LABEL = "Sin origen";

/** What an operator reads for a case's origin; never the raw key. */
export function originLabel(origin: string | null | undefined): string {
  if (!origin) return NO_ORIGIN_LABEL;
  return (ORIGIN_LABEL as Record<string, string>)[origin] ?? "Otro origen";
}

/** The pipeline's origin filter: every case, one channel, or the cases with none. */
export type OriginFilter = "all" | "none" | CaseOrigin;

export function matchesOrigin(card: { origin?: string | null }, filter: OriginFilter): boolean {
  if (filter === "all") return true;
  if (filter === "none") return !card.origin;
  return card.origin === filter;
}
