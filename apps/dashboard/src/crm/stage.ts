import type { OpportunityCardData } from "./crmTypes";
import type { Tone } from "./ui";

export const STAGE_ORDER = ["lead", "qualifying", "qualified", "quoting", "negotiating", "won", "lost", "abandoned"] as const;

/**
 * The operator's six states, over the eight stages the API keeps (`STAGE_TRANSITIONS`):
 *
 * | State | Stage(s) |
 * |---|---|
 * | Solicitada | `lead`, `qualifying` |
 * | En estudio | `qualified` |
 * | Enviada | `quoting` |
 * | Conversación | `negotiating` |
 * | Ganada | `won` |
 * | Perdida | `lost`, `abandoned` |
 *
 * «En pausa» is not a stage: an open case whose earliest open task is due later (`pausedUntil`).
 */
export const STAGE_LABEL: Record<string, string> = {
  lead: "Solicitada",
  qualifying: "Solicitada · calificando",
  qualified: "En estudio",
  quoting: "Enviada",
  negotiating: "Conversación",
  won: "Ganada",
  lost: "Perdida",
  abandoned: "Cerrada · abandonada",
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

export type BoardColumnKey = "solicitada" | "estudio" | "enviada" | "conversacion" | "pausa" | "ganada" | "perdida";

/**
 * The Tablero's columns, one per state. `target` is the stage a card dropped there moves to;
 * «En pausa» has none (a drop asks for a date and writes a task), «Ganada» asks for the revision.
 */
export const BOARD_COLUMNS: { key: BoardColumnKey; label: string; stages: string[]; target: string | null }[] = [
  { key: "solicitada", label: "Solicitada", stages: ["lead", "qualifying"], target: "lead" },
  { key: "estudio", label: "En estudio", stages: ["qualified"], target: "qualified" },
  { key: "enviada", label: "Enviada", stages: ["quoting"], target: "quoting" },
  { key: "conversacion", label: "Conversación", stages: ["negotiating"], target: "negotiating" },
  { key: "pausa", label: "En pausa", stages: [], target: null },
  { key: "ganada", label: "Ganada", stages: ["won"], target: "won" },
  { key: "perdida", label: "Perdida", stages: ["lost", "abandoned"], target: "lost" },
];

/** A case's open tasks that keep it paused: open case, task due after `now`. */
export function pauseTasks(card: OpportunityCardData, now: Date = new Date()): NonNullable<OpportunityCardData["open_tasks"]> {
  if (card.closed_at || TERMINAL.has(card.stage)) return [];
  return (card.open_tasks ?? []).filter((t) => Date.parse(t.due_at) > now.getTime());
}

/**
 * «En pausa hasta…»: the case is open and its earliest open task is due after `now` — nothing to
 * do on it before that day. A task already due (today or overdue) is work, not a pause.
 */
export function pausedUntil(card: OpportunityCardData, now: Date = new Date()): string | null {
  if (card.closed_at || TERMINAL.has(card.stage)) return null;
  const first = (card.open_tasks ?? [])[0];
  return first && Date.parse(first.due_at) > now.getTime() ? first.due_at : null;
}

const TERMINAL: ReadonlySet<string> = new Set(["won", "lost", "abandoned"]);

/** The Tablero column a card sits in. */
export function boardColumnOf(card: OpportunityCardData, now: Date = new Date()): BoardColumnKey {
  if (pausedUntil(card, now)) return "pausa";
  return BOARD_COLUMNS.find((c) => c.stages.includes(card.stage))?.key ?? "solicitada";
}

/** «Perdida»: one click on a reason. «Sin respuesta» closes as `abandoned`, the rest as `lost`. */
export const LOST_REASONS: { label: string; stage: "lost" | "abandoned" }[] = [
  { label: "No es una solicitud", stage: "abandoned" },
  { label: "Sin respuesta", stage: "abandoned" },
  { label: "Sin presupuesto", stage: "lost" },
  { label: "Compró a otro proveedor", stage: "lost" },
  { label: "Precio", stage: "lost" },
  { label: "Plazo de entrega", stage: "lost" },
  { label: "Cambió la necesidad", stage: "lost" },
];

/** «En pausa hasta…»: why the case waits. Becomes the task title «Retomar: <motivo>». */
export const PAUSE_REASONS: string[] = [
  "Esperando fondos o proyecto",
  "Esperando la orden de compra",
  "Pidió volver a contactar",
  "Evaluando internamente",
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
    card.last_contact?.inbound?.sender_name ?? "",
    card.last_contact?.inbound?.subject ?? "",
    ...card.quote_numbers,
    ...card.other_organizations.map((o) => o.name),
  ]
    .join(" ")
    .toLowerCase();
  return hay.includes(needle);
}

/** Most recent commercial activity, not merely the original quotation's sent date.

    Manual stage transitions update opportunity.updated_at; Gmail activity may update
    last_contact without bumping the opportunity row. Both must bring a card to the top.
    Revision sent and creation dates are fallbacks for legacy/imported cards.
    Never use the current wall-clock date: resorting must be deterministic.
 */
export function activityTimestamp(card: OpportunityCardData): number | null {
  const dates = [
    card.updated_at,
    card.last_contact?.inbound?.at,
    card.last_contact?.outbound?.at,
    card.latest_revision?.sent_at,
    card.created_at,
  ];
  const parsed = dates.map((d) => (d ? Date.parse(d) : Number.NaN)).filter(Number.isFinite);
  return parsed.length ? Math.max(...parsed) : null;
}

/** Last case activity first; a case with no known date stays at the end. */
export function byLatestActivity(a: OpportunityCardData, b: OpportunityCardData): number {
  const av = activityTimestamp(a);
  const bv = activityTimestamp(b);
  if (av !== bv) return (bv ?? -Infinity) - (av ?? -Infinity);
  return a.title.localeCompare(b.title, "es") || a.opportunity_id.localeCompare(b.opportunity_id);
}

/**
 * Whether a card's stage is a verified commercial status or only the trace of the historical
 * import. The importer set `quoting` on every case whose quotation it found sent; that says
 * "a quotation was sent on this date", not "this deal is being quoted today". A stage counts as
 * historical while the case is open, sits in `quoting`, has no open task, and every revision it
 * holds came from the historical import — no operator has recorded anything that would confirm it
 * since.
 */
export type StageBasis = "historical_import" | "crm_record";

export function stageBasis(card: OpportunityCardData): StageBasis {
  const revisions = card.quotes.flatMap((q) => q.revisions);
  if (
    !card.closed_at &&
    card.stage === "quoting" &&
    // A task an operator wrote (a follow-up, a pause) is a decision about the case's present.
    (card.open_tasks ?? []).length === 0 &&
    revisions.length > 0 &&
    revisions.every((r) => r.origin === "historical_import")
  ) {
    return "historical_import";
  }
  return "crm_record";
}

export const HISTORICAL_STAGE_LABEL = "Enviada · sin decidir";
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
  { key: "none", label: "Sin actividad registrada", maxDays: Number.NaN },
] as const;

export type AgeBucketKey = (typeof AGE_BUCKETS)[number]["key"];

/** Which `AGE_BUCKETS` entry a timestamp falls in, seen from `now`. */
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
  if (stageBasis(card) === "historical_import") parts.push("sin decidir");
  const latest = card.latest_revision;
  if (!latest) parts.push("sin cotización");
  else {
    if (!card.drive_folder) parts.push("sin Drive");
    if (!latest.gmail) parts.push("sin Gmail");
  }
  if (!card.contact) parts.push("sin contacto");
  if (parts.length === 0 || (parts.length === 1 && parts[0] === "sin decidir")) {
    return { text: parts.length ? "sin decidir · al día" : "Al día", tone: parts.length ? "neutral" : "good" };
  }
  return { text: parts.join(" · "), tone: card.status === "ok" ? "neutral" : "warn" };
}
