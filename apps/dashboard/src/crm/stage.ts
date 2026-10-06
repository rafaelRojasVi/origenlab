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

/**
 * Whether a card's stage is a verified commercial status or only the trace of the historical
 * import. The importer set `quoting` on every case whose quotation it found sent; that says
 * "a quotation was sent on this date", not "this deal is being quoted today". A stage counts as
 * historical while the case is open, sits in `quoting`, and every revision it holds came from
 * the historical import — no operator has recorded anything that would confirm it since.
 */
export type StageBasis = "historical_import" | "crm_record";

export function stageBasis(card: OpportunityCardData): StageBasis {
  const revisions = card.quotes.flatMap((q) => q.revisions);
  if (
    !card.closed_at &&
    card.stage === "quoting" &&
    revisions.length > 0 &&
    revisions.every((r) => r.origin === "historical_import")
  ) {
    return "historical_import";
  }
  return "crm_record";
}

export const HISTORICAL_STAGE_LABEL = "Cotización enviada · histórico";
export const HISTORICAL_STAGE_TITLE =
  "Etapa fijada por la importación histórica a partir de una cotización enviada. No confirma el estado comercial actual: nadie lo ha verificado todavía en el CRM.";

export function stageDisplay(card: OpportunityCardData): { label: string; tone: Tone; title?: string } {
  if (stageBasis(card) === "historical_import") {
    return { label: HISTORICAL_STAGE_LABEL, tone: "neutral", title: HISTORICAL_STAGE_TITLE };
  }
  return { label: STAGE_LABEL[card.stage] ?? card.stage, tone: STAGE_TONE[card.stage] ?? "neutral" };
}


/* ── the Tablero (board) ─────────────────────────────────────────────────── */

/** Age of a card's latest sent revision, for grouping a long board column. Newest first. */
export const AGE_BUCKETS = [
  { key: "d30", label: "Últimos 30 días", maxDays: 30 },
  { key: "d90", label: "31–90 días", maxDays: 90 },
  { key: "d180", label: "91–180 días", maxDays: 180 },
  { key: "d365", label: "181–365 días", maxDays: 365 },
  { key: "older", label: "Más de un año", maxDays: Number.POSITIVE_INFINITY },
  { key: "none", label: "Sin revisión enviada", maxDays: Number.NaN },
] as const;

export type AgeBucketKey = (typeof AGE_BUCKETS)[number]["key"];

/** Which `AGE_BUCKETS` entry a revision sent at `sentAt` falls in, seen from `now`. */
export function ageBucket(sentAt: string | null | undefined, now: Date = new Date()): AgeBucketKey {
  const t = sentAt ? Date.parse(sentAt) : Number.NaN;
  if (Number.isNaN(t)) return "none";
  const days = Math.max(0, (now.getTime() - t) / 86_400_000);
  for (const b of AGE_BUCKETS) if (days <= b.maxDays) return b.key;
  return "older";
}

/**
 * One compact status line for a board card, replacing the row of chips the card view shows.
 * The column already says the stage, so the line says only what an operator must still do:
 * the blocking reason when there is one, otherwise the gaps («sin Drive», «sin Gmail»,
 * «sin contacto») — and whether the stage is only the historical import's trace.
 */
export function boardStatusLine(card: OpportunityCardData): { text: string; tone: Tone } {
  const blocking = card.attention.find((a) => a.blocking);
  if (blocking) return { text: blocking.label, tone: "bad" };
  const parts: string[] = [];
  if (stageBasis(card) === "historical_import") parts.push("histórico");
  const latest = card.latest_revision;
  if (!latest) parts.push("sin cotización");
  else {
    if (!card.drive_folder) parts.push("sin Drive");
    if (!latest.gmail) parts.push("sin Gmail");
  }
  if (!card.contact) parts.push("sin contacto");
  if (parts.length === 0 || (parts.length === 1 && parts[0] === "histórico")) {
    return { text: parts.length ? "histórico · al día" : "Al día", tone: parts.length ? "neutral" : "good" };
  }
  return { text: parts.join(" · "), tone: card.status === "ok" ? "neutral" : "warn" };
}
