import type { OpportunityCardData } from "./crmTypes";
import type { Tone } from "./ui";

export const STAGE_ORDER = ["lead", "qualifying", "qualified", "quoting", "negotiating", "won", "lost", "abandoned"] as const;

export const STAGE_LABEL: Record<string, string> = {
  lead: "Lead",
  qualifying: "Calificando",
  qualified: "Calificada",
  quoting: "Cotizando",
  negotiating: "Negociando",
  won: "Ganada",
  lost: "Perdida",
  abandoned: "Abandonada",
};

export const STAGE_TONE: Record<string, Tone> = {
  lead: "neutral",
  qualifying: "neutral",
  qualified: "info",
  quoting: "brand",
  negotiating: "warn",
  won: "good",
  lost: "bad",
  abandoned: "neutral",
};

/** Board columns: open stages each get one, closed stages share one. */
export const BOARD_COLUMNS: { key: string; label: string; stages: string[] }[] = [
  { key: "lead", label: "Lead", stages: ["lead", "qualifying"] },
  { key: "qualified", label: "Calificada", stages: ["qualified"] },
  { key: "quoting", label: "Cotizando", stages: ["quoting"] },
  { key: "negotiating", label: "Negociando", stages: ["negotiating"] },
  { key: "closed", label: "Cerradas", stages: ["won", "lost", "abandoned"] },
];

export const STATUS_LABEL: Record<OpportunityCardData["status"], { label: string; tone: Tone }> = {
  blocked: { label: "Bloqueada", tone: "bad" },
  pending: { label: "Pendiente", tone: "warn" },
  ok: { label: "Al día", tone: "good" },
};

export const ORIGIN_LABEL: Record<string, string> = {
  historical_import: "Importación histórica",
  authored: "Creada en el CRM",
};

export function matchesQuery(card: OpportunityCardData, q: string): boolean {
  const needle = q.trim().toLowerCase();
  if (!needle) return true;
  const hay = [
    card.title,
    card.organization?.name ?? "",
    card.contact?.address ?? "",
    card.contact?.name ?? "",
    ...card.quote_numbers,
    ...card.other_organizations.map((o) => o.name),
  ]
    .join(" ")
    .toLowerCase();
  return hay.includes(needle);
}

/** Most recent sent revision first; cards without a revision last. */
export function byLatestSent(a: OpportunityCardData, b: OpportunityCardData): number {
  const av = a.latest_revision?.sent_at ?? "";
  const bv = b.latest_revision?.sent_at ?? "";
  if (av === bv) return a.title.localeCompare(b.title);
  return av < bv ? 1 : -1;
}
