/**
 * «Ventas › Seguimiento»: the views, filters, orders and groups, as pure functions over the rows
 * (`seguimientoRows`). Everything comes from the one pipeline read; nothing here asks the API.
 *
 * Filters combine AND across groups and OR inside a group. A chip's count is how many rows the
 * page would show with that chip added to the other groups' choices (a faceted count). Days are
 * America/Santiago calendar days (`dateSantiago`).
 */
import type { ProfileCard } from "../api/authClient";
import type { SetCaseFollowUpBody } from "./caseCommands";
import { contactLine, displayName, quoteProduct } from "./caseDisplay";
import type { OpportunityCardData } from "./crmTypes";
import { dateSantiago } from "./goals";
import { channelsFor, type FollowUpChannel, type SegRow } from "./seguimiento";
import { STAGE_LABEL } from "./stage";
import { nextContact } from "./nextContact";

/* ── state in the address ─────────────────────────────────────────────── */

export type SegView = "tabla" | "agenda" | "encargado";
export const SEG_VIEWS: { key: SegView; label: string }[] = [
  { key: "tabla", label: "Tabla" },
  { key: "agenda", label: "Agenda" },
  { key: "encargado", label: "Por encargado" },
];

export type GroupKey = "encargado" | "canal" | "estado" | "etapa" | "fono" | "monto";
export const GROUP_KEYS: GroupKey[] = ["encargado", "canal", "estado", "etapa", "fono", "monto"];
export type SegFilters = Record<GroupKey, string[]>;
export const EMPTY_FILTERS: SegFilters = { encargado: [], canal: [], estado: [], etapa: [], fono: [], monto: [] };

export type SegOrder = "prioridad" | "monto" | "dias" | "proximo";
export const SEG_ORDERS: { key: SegOrder; label: string }[] = [
  { key: "prioridad", label: "Prioridad" },
  { key: "monto", label: "Monto" },
  { key: "dias", label: "Días sin respuesta" },
  { key: "proximo", label: "Próximo contacto" },
];

export interface SegQuery {
  view: SegView;
  filters: SegFilters;
  order: SegOrder;
}

const isView = (v: string | null): v is SegView => SEG_VIEWS.some((x) => x.key === v);
const isOrder = (v: string | null): v is SegOrder => SEG_ORDERS.some((x) => x.key === v);

/** The view, filters and order a `?vista=&encargado=…` query names; `fallbackView` when it names none. */
export function readSegQuery(params: URLSearchParams, fallbackView: SegView = "tabla"): SegQuery {
  const filters = { ...EMPTY_FILTERS };
  for (const g of GROUP_KEYS) {
    const raw = params.get(g);
    filters[g] = raw ? [...new Set(raw.split(",").map((v) => v.trim()).filter(Boolean))] : [];
  }
  const view = params.get("vista");
  const order = params.get("orden");
  return { view: isView(view) ? view : fallbackView, filters, order: isOrder(order) ? order : "prioridad" };
}

/** `params` with the query written in (other keys, such as `caso`, kept); defaults are left out. */
export function writeSegQuery(params: URLSearchParams, q: SegQuery): URLSearchParams {
  const out = new URLSearchParams(params);
  for (const g of GROUP_KEYS) {
    if (q.filters[g].length) out.set(g, q.filters[g].join(","));
    else out.delete(g);
  }
  out.set("vista", q.view);
  if (q.order === "prioridad") out.delete("orden");
  else out.set("orden", q.order);
  return out;
}

export const activeFilterCount = (f: SegFilters): number => GROUP_KEYS.reduce((n, g) => n + f[g].length, 0);

/* ── the next contact ─────────────────────────────────────────────────── */

/** When the case is next due (`nextContact`: the one rule every screen shows), or null («Sin fecha»). */
export function nextContactAt(card: OpportunityCardData, now: Date = new Date()): string | null {
  return nextContact(card, now)?.at ?? null;
}

/** A `YYYY-MM-DD` day plus `days`. */
function plusDays(day: string, days: number): string {
  const d = new Date(`${day}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/** Sunday of the Santiago week `day` is in (weeks run Monday to Sunday). */
function weekEnd(day: string): string {
  const weekday = new Date(`${day}T12:00:00Z`).getUTCDay(); // 0 = Sunday
  return plusDays(day, weekday === 0 ? 0 : 7 - weekday);
}

/* ── filters ──────────────────────────────────────────────────────────── */

export interface FilterContext {
  /** The signed-in operator («Mis casos»). */
  me: string | null;
  profiles: ProfileCard[];
  now: Date;
}

export interface FilterOption {
  value: string;
  label: string;
}

export interface FilterGroup {
  key: GroupKey;
  label: string;
  options: FilterOption[];
}

export const profileKey = (p: ProfileCard): string => p.key || p.id;

/** The person a row shows: the encargado, else the suggestion the picker shows selected. */
export const shownOwner = (r: SegRow): string | null => r.ownerId ?? r.suggestedOwnerId;

const OPEN_STAGE_ORDER = ["lead", "qualifying", "qualified", "quoting", "negotiating"];

function amountBand(r: SegRow): string {
  if (r.amountClp === null) return "sin";
  if (r.amountClp < 1_000_000) return "lt1";
  return r.amountClp <= 10_000_000 ? "1a10" : "gt10";
}

/** «Toca hoy»: the row's next contact (`SegRow.next`) is due today or earlier (Santiago days). */
export function dueByToday(r: SegRow, now: Date): boolean {
  const at = r.next?.at ?? null;
  return !!at && dateSantiago(new Date(at)) <= dateSantiago(now);
}

/** Does `row` match one option of one group? */
export function matchesOption(group: GroupKey, value: string, r: SegRow, ctx: FilterContext): boolean {
  switch (group) {
    case "encargado": {
      // «Sin confirmar»: nobody confirmed yet (a suggestion is only a suggestion).
      if (value === "sin") return r.ownerId === null;
      if (value === "mios") return !!ctx.me && shownOwner(r) === ctx.me;
      const p = ctx.profiles.find((x) => profileKey(x) === value);
      return !!p && shownOwner(r) === p.id;
    }
    case "canal":
      return r.channel === value;
    case "estado":
      if (value === "atrasado") return r.late;
      if (value === "toca_hoy") return dueByToday(r, ctx.now);
      if (value === "nunca") return r.callsTotal === 0;
      if (value === "llamado_hoy") return r.callsToday > 0;
      if (value === "contesto") return !!r.card.last_call;
      return false;
    case "etapa":
      return r.card.stage === value;
    case "fono":
      return value === "con" ? !!r.phone : value === "sin" ? !r.phone : false;
    case "monto":
      return amountBand(r) === value;
  }
}

/** The chips of every group; «Etapa» lists only the stages present, «Encargado» the profiles. */
export function filterGroups(rows: SegRow[], ctx: FilterContext): FilterGroup[] {
  const stages = OPEN_STAGE_ORDER.filter((s) => rows.some((r) => r.card.stage === s));
  return [
    {
      key: "encargado",
      label: "Encargado",
      options: [
        ...(ctx.me ? [{ value: "mios", label: "Mis casos" }] : []),
        ...ctx.profiles.map((p) => ({ value: profileKey(p), label: p.displayName })),
        { value: "sin", label: "Sin confirmar" },
      ],
    },
    {
      key: "canal",
      label: "Canal",
      options: [
        { value: "llamada", label: "Llamada" },
        { value: "correo", label: "Correo" },
        { value: "whatsapp", label: "WhatsApp" },
      ],
    },
    {
      key: "estado",
      label: "Estado",
      options: [
        { value: "atrasado", label: "Atrasado" },
        { value: "toca_hoy", label: "Toca hoy" },
        { value: "nunca", label: "Nunca llamado" },
        { value: "llamado_hoy", label: "Llamado hoy" },
        { value: "contesto", label: "Contestó alguna vez" },
      ],
    },
    { key: "etapa", label: "Etapa", options: stages.map((s) => ({ value: s, label: STAGE_LABEL[s] ?? s })) },
    {
      key: "fono",
      label: "Teléfono",
      options: [
        { value: "con", label: "Con teléfono" },
        { value: "sin", label: "Sin teléfono" },
      ],
    },
    {
      key: "monto",
      label: "Monto",
      options: [
        { value: "lt1", label: "Menos de $1 M" },
        { value: "1a10", label: "$1 a 10 M" },
        { value: "gt10", label: "Más de $10 M" },
        { value: "sin", label: "Sin monto" },
      ],
    },
  ];
}

/** The filters with values no chip offers dropped (an old link, a profile that left). */
export function knownFilters(filters: SegFilters, groups: FilterGroup[]): SegFilters {
  const out = { ...EMPTY_FILTERS };
  for (const g of groups) out[g.key] = filters[g.key].filter((v) => g.options.some((o) => o.value === v));
  return out;
}

function matchesGroup(group: GroupKey, chosen: string[], r: SegRow, ctx: FilterContext): boolean {
  return chosen.length === 0 || chosen.some((v) => matchesOption(group, v, r, ctx));
}

/** Lower case without accents or tildes («Católica» → «catolica»): Chileans often type without them. */
export function foldText(s: string): string {
  return s.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("es");
}

/** Every word of `q` is somewhere in the client, institution, contact, quote N° or product. */
export function matchesSearch(r: SegRow, q: string): boolean {
  const words = foldText(q).split(/\s+/).filter(Boolean);
  if (words.length === 0) return true;
  const card = r.card;
  const hay = foldText(
    [
      displayName(card).name,
      card.organization?.name,
      card.contact?.name,
      contactLine(card),
      card.title,
      quoteProduct(card),
      ...card.quote_numbers,
      card.latest_revision?.quote_number,
      ...(card.money ?? []).map((m) => m.quote_number),
    ]
      .filter(Boolean)
      .join(" "),
  );
  return words.every((w) => hay.includes(w));
}

/** Rows that pass every group (but `except`, for faceted counts) and the search. */
export function applyFilters(rows: SegRow[], filters: SegFilters, ctx: FilterContext, q = "", except?: GroupKey): SegRow[] {
  return rows.filter((r) => GROUP_KEYS.every((g) => g === except || matchesGroup(g, filters[g], r, ctx)) && matchesSearch(r, q));
}

/** Each chip's count (`<group>:<value>`): the rows shown if that chip were added to its group. */
export function facetCounts(rows: SegRow[], groups: FilterGroup[], filters: SegFilters, ctx: FilterContext, q = ""): Map<string, number> {
  const counts = new Map<string, number>();
  for (const g of groups) {
    const base = applyFilters(rows, filters, ctx, q, g.key);
    for (const o of g.options) counts.set(`${g.key}:${o.value}`, base.filter((r) => matchesOption(g.key, o.value, r, ctx)).length);
  }
  return counts;
}

/* ── orders ───────────────────────────────────────────────────────────── */

const byTime = (iso: string | null) => (iso ? Date.parse(iso) : Number.POSITIVE_INFINITY);

/** The rows in `order`; ties keep the incoming order. */
export function sortRows(rows: SegRow[], order: SegOrder): SegRow[] {
  const out = [...rows];
  switch (order) {
    case "prioridad":
      return out.sort((a, b) => b.score - a.score || Number(b.late) - Number(a.late) || b.daysSinceTouch - a.daysSinceTouch);
    case "monto":
      return out.sort((a, b) => (b.amountClp ?? -1) - (a.amountClp ?? -1));
    case "dias":
      return out.sort((a, b) => b.daysSinceSent - a.daysSinceSent);
    case "proximo":
      return out.sort((a, b) => byTime(a.next?.at ?? null) - byTime(b.next?.at ?? null));
  }
}

/* ── Agenda ───────────────────────────────────────────────────────────── */

export type AgendaKey = "atrasados" | "hoy" | "manana" | "semana" | "despues" | "sin";
const AGENDA_LABEL: Record<AgendaKey, string> = {
  atrasados: "Atrasados",
  hoy: "Hoy",
  manana: "Mañana",
  semana: "Esta semana",
  despues: "Más adelante",
  sin: "Sin fecha",
};
const AGENDA_ORDER: AgendaKey[] = ["atrasados", "hoy", "manana", "semana", "despues", "sin"];

/** Which Agenda group a row falls in, by the Santiago day of its next contact (`SegRow.next`). */
export function agendaKey(r: SegRow, now: Date): AgendaKey {
  const at = r.next?.at ?? null;
  if (!at) return "sin";
  const day = dateSantiago(new Date(at));
  const today = dateSantiago(now);
  if (day < today) return "atrasados";
  if (day === today) return "hoy";
  if (day === plusDays(today, 1)) return "manana";
  return day <= weekEnd(today) ? "semana" : "despues";
}

/** The Agenda's groups in order, empty ones left out; rows keep their incoming order. */
export function agendaGroups(rows: SegRow[], now: Date): { key: AgendaKey; label: string; rows: SegRow[] }[] {
  const by = new Map<AgendaKey, SegRow[]>(AGENDA_ORDER.map((k) => [k, []]));
  for (const r of rows) by.get(agendaKey(r, now))!.push(r);
  return AGENDA_ORDER.filter((k) => by.get(k)!.length > 0).map((k) => ({ key: k, label: AGENDA_LABEL[k], rows: by.get(k)! }));
}

/* ── Por encargado ────────────────────────────────────────────────────── */

const BOARD_KEY_ORDER = ["karla", "rafael", "rafa", "tatiana", "tati"];

export interface BoardColumn {
  /** The operator, or null for «Sin confirmar». */
  ownerId: string | null;
  label: string;
  rows: SegRow[];
}

/**
 * «Sin confirmar» first (where the work is: confirm or reassign), then one column per profile
 * (Karla · Rafa · Tati first); a row sits under its confirmed encargado.
 */
export function boardColumns(rows: SegRow[], profiles: ProfileCard[]): BoardColumn[] {
  const rank = (p: ProfileCard) => {
    const i = BOARD_KEY_ORDER.indexOf(p.key.toLowerCase());
    return i < 0 ? BOARD_KEY_ORDER.length : i;
  };
  const people = [...profiles].sort((a, b) => rank(a) - rank(b));
  const known = new Set(people.map((p) => p.id));
  return [
    { ownerId: null, label: "Sin confirmar", rows: rows.filter((r) => r.ownerId === null || !known.has(r.ownerId)) },
    ...people.map((p) => ({ ownerId: p.id, label: p.displayName, rows: rows.filter((r) => r.ownerId === p.id) })),
  ];
}

/**
 * The `set-case-follow-up` body for a card moved to `ownerId`: the channel stays, unless it is
 * WhatsApp and the new encargado may not use it, then the first channel they may.
 */
export function boardMoveBody(card: OpportunityCardData, ownerId: string | null, profiles: ProfileCard[]): SetCaseFollowUpBody {
  const current = (card.follow_up?.channel ?? null) as FollowUpChannel | null;
  const allowed = channelsFor(ownerId, profiles);
  const channel = current && !allowed.includes(current) ? allowed[0] : current;
  return { opportunity_id: card.opportunity_id, opportunity_version: card.version ?? 0, follow_up_operator_id: ownerId, channel };
}

/* ── a stable place on screen ─────────────────────────────────────────── */

/**
 * `rows` in the order `prev` (the ids as last shown) had them; rows new to the list follow in
 * their own order. A save, a receipt or a refetch never moves a row away from under the pointer
 * (a re-sort mid-click opened another case's panel, QA 2026-11-19): the order changes only when
 * the person changes the order, a filter or the search.
 */
export function keepOrder(rows: SegRow[], prev: readonly string[]): SegRow[] {
  if (prev.length === 0) return rows;
  const at = new Map(prev.map((id, i) => [id, i]));
  const known = rows.filter((r) => at.has(r.card.opportunity_id));
  const fresh = rows.filter((r) => !at.has(r.card.opportunity_id));
  known.sort((a, b) => at.get(a.card.opportunity_id)! - at.get(b.card.opportunity_id)!);
  return [...known, ...fresh];
}

/* ── «Modo llamadas» ──────────────────────────────────────────────────── */

/**
 * A case to call now: not called today, and its próximo contacto is today or past — or, with no
 * date at all, it is late by the 3-day rhythm (nobody set a date, so it is due).
 */
export function dueForCall(r: SegRow, now: Date): boolean {
  if (r.callsToday > 0) return false;
  return dueByToday(r, now) || (!r.next && r.late);
}

/* ── bulk assign ──────────────────────────────────────────────────────── */

export type BulkAction = { kind: "confirm" } | { kind: "owner"; ownerId: string | null } | { kind: "channel"; channel: FollowUpChannel };

/**
 * The `set-case-follow-up` body for each selected row, and how many rows the action does not apply
 * to («Confirmar sugerido» on a confirmed row, WhatsApp for an encargado who may not use it).
 */
export function bulkBodies(rows: SegRow[], action: BulkAction, profiles: ProfileCard[]): { bodies: SetCaseFollowUpBody[]; skipped: number } {
  const bodies: SetCaseFollowUpBody[] = [];
  let skipped = 0;
  for (const r of rows) {
    const base = { opportunity_id: r.card.opportunity_id, opportunity_version: r.card.version ?? 0 };
    if (action.kind === "confirm") {
      if (!r.isSuggested) skipped += 1;
      else bodies.push({ ...base, follow_up_operator_id: r.suggestedOwnerId, channel: r.channel });
    } else if (action.kind === "owner") {
      bodies.push(boardMoveBody(r.card, action.ownerId, profiles));
    } else if (!channelsFor(r.ownerId ?? r.suggestedOwnerId, profiles).includes(action.channel)) {
      skipped += 1;
    } else {
      // A channel set on a suggested case confirms the suggestion too: one command, the full pair.
      bodies.push({ ...base, follow_up_operator_id: r.ownerId ?? r.suggestedOwnerId, channel: action.channel });
    }
  }
  return { bodies, skipped };
}
