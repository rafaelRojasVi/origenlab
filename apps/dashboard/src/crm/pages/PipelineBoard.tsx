/**
 * The Pipeline «Tablero»: one column per state (Solicitada, Enviada, Silenciosas,
 * Conversación, En pausa, Ganada, Perdida).
 *
 * - Every card is shown except a loss closed more than `LOST_ON_BOARD_DAYS` ago (`isOnBoard`; it
 *   lives under «Cerradas»); a won case always stays. Every column scrolls on its own, so a long
 *   column never stretches the page. `sort` orders them (newest quote, oldest, «respondieron
 *   primero», institution); by date, a thin divider marks each age band without hiding a card —
 *   «Enviada» by the follow-up rhythm («0–7 días», «8–21 días»: `enviadaBand`), the others by the
 *   last touch («Hoy», «Esta semana», «Semana pasada»…).
 * - «Silenciosas» fills by itself (`isSilent`: 21 days without an answer). Its cards say which
 *   follow-up is due («Seguimiento N de 3») and offer «¿Marcar perdida?».
 * - A card shows one amount: the neto of the case's current quotation (`cardMoney`), its number and
 *   a «rev N» chip. The history of revisions is in the drawer.
 * - A card leads with a readable institution name, the contact and the days since the quote went
 *   out (coloured by the follow-up rhythm: 3 · 14 · 30 days), then the quote, what it is for
 *   (`quoteProduct`: the email subject's product, the PDF's model), where the conversation
 *   stands, and direct links to the PDF in Drive and the email.
 * - With `onMove` (the operator may decide cases), a card can be dragged to another column. The
 *   board writes nothing itself: it says which card went where, and the page asks for what the
 *   move needs (a reason, a date, the revision) before anything is recorded. A click still opens
 *   the drawer, whose «Cambiar estado» does the same without a mouse.
 */
import { Fragment, useMemo, useState, type DragEvent, type MouseEvent } from "react";
import { inSharedMailbox } from "../gmailLinks";
import type { OpportunityCardData } from "../crmTypes";
import { originLabel } from "../caseOrigin";
import { ageTone, contactLine, conversation, displayName, isDelivered, lastTouch, quoteProduct } from "../caseDisplay";
import {
  AGE_BUCKETS,
  BOARD_COLUMNS,
  ENVIADA_BANDS,
  activityTimestamp,
  ageBucket,
  boardColumnOf,
  byLatestActivity,
  enviadaBand,
  isOnBoard,
  pausedUntil,
  type BoardColumnKey,
  type EnviadaBandKey,
} from "../stage";
import { followUpStep, followUpsDue } from "../today";
import { fmtDate } from "../ui";
import { GrowingListEnd } from "../GrowingListEnd";
import { useGrowingList } from "../useGrowingList";
import { formatMoney } from "../money";

export type BoardSort = "recent" | "oldest" | "replied" | "name";

export const BOARD_SORT_LABEL: Record<BoardSort, string> = {
  recent: "Más recientes",
  oldest: "Más antiguas",
  replied: "Respondieron primero",
  name: "Institución",
};

const LINE_TONE: Record<string, string> = {
  bad: "text-bad",
  warn: "text-warn",
  good: "text-good",
  info: "text-info",
  brand: "text-accent",
  neutral: "text-ink-muted",
};

const COLUMN_DOT: Record<BoardColumnKey, string> = {
  solicitada: "bg-ink-faint",
  enviada: "bg-brand-600",
  silenciosas: "bg-warn",
  conversacion: "bg-warn",
  pausa: "bg-violet-600",
  ganada: "bg-good",
  perdida: "bg-bad",
};

const DRAG_TYPE = "text/plain";

/**
 * The neto quoted in a column: each case's last sent quotation (`money`), CLP only (a
 * foreign-currency quote is rare and would not add up), and how many cases have a quote whose
 * PDF nobody read yet, so a small number never pretends to be the whole column.
 */
export function columnMoney(cards: readonly OpportunityCardData[]): { clp: number; unread: number } {
  let clp = 0;
  let unread = 0;
  for (const c of cards) {
    const money = c.money ?? [];
    const read = money.find((m) => m.currency === "CLP");
    if (read) clp += Number(read.net_total) || 0;
    if (money.some((m) => m.currency === "unread") || (c.quotes.length > 0 && money.length === 0)) unread += 1;
  }
  return { clp, unread };
}

function ColumnMoney({ cards, columnKey }: { cards: readonly OpportunityCardData[]; columnKey: string }) {
  const { clp, unread } = columnMoney(cards);
  return (
    <p className="mt-0.5 pl-3.5 text-[11.5px] tabular-nums text-ink-muted" data-testid={`board-money-${columnKey}`}>
      <span className="font-semibold text-ink">{formatMoney(String(clp), "CLP")}</span>
      <span className="text-ink-faint"> neto</span>
      {unread > 0 ? (
        <span className="text-ink-faint" title="Cotizaciones cuyo PDF aún no se ha leído: no están sumadas">
          {" "}+ {unread} sin leer
        </span>
      ) : null}
    </p>
  );
}

/**
 * The one amount a card shows: the neto of the case's current quotation (`money[0]`, the last sent
 * vigente revision), its number and revision. «sin leer» when nobody has read that PDF yet; an
 * empty text when the case has no quotation with an amount.
 */
export function cardMoney(card: OpportunityCardData): { text: string; cn: string | null; rev: number | null } {
  const m = (card.money ?? [])[0];
  const fallbackCn = card.latest_revision?.quote_number ?? null;
  if (!m) return { text: "", cn: fallbackCn, rev: null };
  return {
    text: m.currency === "unread" ? "sin leer" : (formatMoney(m.net_total, m.currency) ?? ""),
    cn: m.quote_number ?? fallbackCn,
    rev: m.revision_no ?? null,
  };
}

/**
 * «Enviada»'s band for a card. A draft nobody sent yet is fresh work: «0–7 días». Past day 21 and
 * not silent (the client answered) it sits under «8–21 días».
 */
function enviadaBandOf(card: OpportunityCardData, at: Date): EnviadaBandKey {
  const band = enviadaBand(card, at);
  if (band) return band;
  return isDelivered(card.latest_revision) ? "d8_21" : "d0_7";
}

/**
 * «Seguimiento N de 3» for a silent card: the step `followUpsDue` gives it, or — for a case that
 * list leaves out (an undecided historical import, a follow-up written in the last days) — the same
 * 3 · 14 · 30 mapping over the days since OrigenLab's last touch.
 */
export function silentFollowUpStep(card: OpportunityCardData, at: Date): 1 | 2 | 3 {
  const due = followUpsDue([card], at)[0];
  if (due) return followUpStep(due);
  const since = Date.parse(lastTouch(card).at ?? card.latest_revision?.sent_at ?? "");
  const days = Number.isNaN(since) ? 0 : Math.floor((at.getTime() - since) / 86_400_000);
  return days >= 30 ? 3 : days >= 14 ? 2 : 1;
}

const MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sept", "oct", "nov", "dic"];

/** «24 sept», with the year only when it is not this one («12 may 2025»). */
export function shortDate(iso: string | null | undefined, now: Date): string {
  const d = iso ? new Date(iso) : null;
  if (!d || Number.isNaN(d.getTime())) return "—";
  const base = `${d.getDate()} ${MONTHS[d.getMonth()]}`;
  return d.getFullYear() === now.getFullYear() ? base : `${base} ${d.getFullYear()}`;
}

export function sortCards(cards: OpportunityCardData[], sort: BoardSort, now: Date): OpportunityCardData[] {
  const out = [...cards];
  if (sort === "name") return out.sort((a, b) => displayName(a).name.localeCompare(displayName(b).name, "es"));
  if (sort === "replied") {
    const rank = (c: OpportunityCardData) => ({ replied: 0, followed_up: 1, silent: 2, none: 3 })[conversation(c, now).kind];
    return out.sort((a, b) => rank(a) - rank(b) || byLatestActivity(a, b));
  }
  // Unknown timestamps stay last even when reversing. Do not confuse an older PDF
  // with a case the operator moved or the customer answered today.
  return out.sort((a, b) => {
    if (sort !== "oldest") return byLatestActivity(a, b);
    const av = activityTimestamp(a);
    const bv = activityTimestamp(b);
    if (av === null || bv === null) {
      return av === bv ? a.opportunity_id.localeCompare(b.opportunity_id) : av === null ? 1 : -1;
    }
    return av - bv || a.opportunity_id.localeCompare(b.opportunity_id);
  });
}

export function Board({
  cards: allCards,
  onOpen,
  onMove,
  onLose,
  sort = "recent",
  now,
}: {
  cards: OpportunityCardData[];
  onOpen: (id: string) => void;
  /** A card was dropped on another column. Absent: the board is read-only. */
  onMove?: (card: OpportunityCardData, to: BoardColumnKey) => void;
  /** «¿Marcar perdida?» on a silent card. Offered only when the board takes moves (`onMove`). */
  onLose?: (card: OpportunityCardData) => void;
  sort?: BoardSort;
  /** The moment ages and pauses are measured from; tests pass a fixed one. */
  now?: Date;
}) {
  const at = useMemo(() => now ?? new Date(), [now]);
  const cards = useMemo(() => allCards.filter((c) => isOnBoard(c, at)), [allCards, at]);
  const lose = onMove ? onLose : undefined;
  const [dragging, setDragging] = useState<{ id: string; from: BoardColumnKey } | null>(null);
  const [over, setOver] = useState<BoardColumnKey | null>(null);

  function drop(e: DragEvent, to: BoardColumnKey) {
    e.preventDefault();
    const id = e.dataTransfer.getData(DRAG_TYPE) || dragging?.id;
    setOver(null);
    setDragging(null);
    const card = cards.find((c) => c.opportunity_id === id);
    if (card && onMove && boardColumnOf(card, at) !== to) onMove(card, to);
  }

  const byDate = sort === "recent" || sort === "oldest";
  return (
    <div className="-mx-4 overflow-x-auto px-4 pb-2 sm:mx-0 sm:px-0">
      {/* Five working columns share the width; Ganada and Perdida are narrower, with compact cards. */}
      <div className="grid min-w-[70rem] grid-cols-[repeat(5,minmax(10rem,1fr))_repeat(2,minmax(8.5rem,0.8fr))] gap-3">
        {BOARD_COLUMNS.map((col) => {
          const inCol = sortCards(
            cards.filter((c) => boardColumnOf(c, at) === col.key),
            sort,
            at,
          );
          const droppable = onMove != null && dragging != null && dragging.from !== col.key;
          const drag = onMove ? { column: col.key, set: setDragging, current: dragging?.id ?? null } : null;
          return (
            <section
              key={col.key}
              aria-label={col.label}
              className="flex min-w-0 flex-col"
              onDragOver={(e) => {
                if (!droppable) return;
                e.preventDefault();
                e.dataTransfer.dropEffect = "move";
                if (over !== col.key) setOver(col.key);
              }}
              onDragLeave={(e) => {
                if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOver((o) => (o === col.key ? null : o));
              }}
              onDrop={(e) => drop(e, col.key)}
              data-drop-target={over === col.key ? "true" : undefined}
            >
              <header className="mb-1.5 px-0.5">
                <div className="flex items-center gap-1.5">
                  <span aria-hidden="true" className={`h-2 w-2 shrink-0 rounded-full ${COLUMN_DOT[col.key]}`} />
                  <h2 className="truncate text-[13px] font-semibold text-ink">{col.label}</h2>
                  <span
                    className="rounded-full bg-canvas-sunken px-1.5 py-px text-[11px] tabular-nums text-ink-muted"
                    data-testid={`board-count-${col.key}`}
                  >
                    {inCol.length}
                  </span>
                </div>
                <ColumnMoney cards={inCol} columnKey={col.key} />
              </header>
              <div
                className={`flex max-h-[72vh] min-h-48 flex-col gap-2 overflow-y-auto rounded-xl p-1.5 transition-[background-color,box-shadow] duration-150 ${
                  over === col.key
                    ? "bg-brand-50 ring-2 ring-brand-600"
                    : droppable
                      ? "bg-canvas-sunken/70 ring-1 ring-brand-600/40"
                      : "bg-canvas-sunken/70"
                }`}
                data-testid={`board-column-${col.key}`}
              >
                {inCol.length === 0 ? (
                  <p className="rounded-lg border border-dashed border-line-strong px-2 py-4 text-center text-[11px] text-ink-faint">
                    {col.key === "silenciosas"
                      ? "Ninguna cotización lleva más de 21 días sin respuesta. Se llena sola."
                      : droppable
                        ? "Suelta aquí"
                        : "Sin oportunidades"}
                  </p>
                ) : (
                  <ColumnCards
                    cards={col.key === "enviada" && byDate ? byEnviadaBand(inCol, sort, at) : inCol}
                    columnKey={col.key}
                    byDate={byDate}
                    at={at}
                    onOpen={onOpen}
                    onLose={col.key === "silenciosas" ? lose : undefined}
                    drag={drag}
                  />
                )}
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}

/**
 * «Enviada» sorted by date: its two bands in order (newest band first, or last under «Más
 * antiguas»), each band keeping the sort inside it — a follow-up email moves a card up within its
 * band, never across the divider.
 */
function byEnviadaBand(cards: OpportunityCardData[], sort: BoardSort, at: Date): OpportunityCardData[] {
  const keys = ENVIADA_BANDS.map((b) => b.key);
  const order = sort === "oldest" ? [...keys].reverse() : keys;
  return order.flatMap((k) => cards.filter((c) => enviadaBandOf(c, at) === k));
}

function ageBucketOf(card: OpportunityCardData, at: Date): string {
  const activity = activityTimestamp(card);
  return ageBucket(activity == null ? null : new Date(activity).toISOString(), at);
}

/** One column's cards, drawn in slices: Ganada and Perdida hold every closed case. */
function ColumnCards({
  cards,
  columnKey,
  byDate,
  at,
  onOpen,
  onLose,
  drag,
}: {
  cards: OpportunityCardData[];
  columnKey: BoardColumnKey;
  byDate: boolean;
  at: Date;
  onOpen: (id: string) => void;
  onLose?: (card: OpportunityCardData) => void;
  drag: DragHook | null;
}) {
  const { shown, hidden, showMore, endRef } = useGrowingList(cards);
  const enviada = columnKey === "enviada";
  // «Enviada» always shows its two dividers by date: they are the follow-up rhythm, not just a
  // way through a long column.
  const bandOf = (c: OpportunityCardData) => (enviada ? enviadaBandOf(c, at) : ageBucketOf(c, at));
  const labelOf = (key: string) =>
    enviada ? ENVIADA_BANDS.find((b) => b.key === key)?.label : AGE_BUCKETS.find((b) => b.key === key)?.label;
  return (
    <>
      {shown.map((c, i) => {
        const band = byDate ? bandOf(c) : null;
        const prev = byDate && i > 0 ? bandOf(cards[i - 1]) : null;
        const label = band && (i === 0 || band !== prev) ? labelOf(band) : null;
        return (
          <Fragment key={c.opportunity_id}>
            {label && (enviada || cards.length > 4) ? (
              <p className="flex items-center gap-2 px-1 pt-1 text-[10px] font-semibold uppercase tracking-wider text-ink-faint" data-testid={`board-band-${band}`}>
                {label}
                <span aria-hidden="true" className="h-px flex-1 bg-line-strong" />
              </p>
            ) : null}
            <BoardCard
              card={c}
              onOpen={onOpen}
              now={at}
              drag={drag}
              index={i}
              compact={columnKey === "ganada" || columnKey === "perdida"}
              followUpStep={columnKey === "silenciosas" ? silentFollowUpStep(c, at) : undefined}
              onLose={onLose}
            />
          </Fragment>
        );
      })}
      <GrowingListEnd hidden={hidden} onMore={showMore} endRef={endRef} />
    </>
  );
}

interface DragHook {
  column: BoardColumnKey;
  set: (d: { id: string; from: BoardColumnKey } | null) => void;
  current: string | null;
}

function DriveIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="M2 4.5A1.5 1.5 0 0 1 3.5 3h3l1.5 1.5h4.5A1.5 1.5 0 0 1 14 6v5.5a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 2 11.5z" />
    </svg>
  );
}

function MailIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="1.5">
      <rect x="2" y="3.5" width="12" height="9" rx="1.5" />
      <path d="m2.5 4.5 5.5 4 5.5-4" />
    </svg>
  );
}

/** The one line under the quote: a pause, a blocker, how the case closed, or the conversation. */
export function cardLine(card: OpportunityCardData, now: Date): { text: string; tone: string } {
  const until = pausedUntil(card, now);
  if (until) return { text: `Hasta ${fmtDate(until)} · ${card.open_tasks?.[0]?.title ?? "en pausa"}`, tone: "info" };
  const blocking = card.attention.find((a) => a.blocking);
  if (blocking) return { text: blocking.label, tone: "bad" };
  if (card.stage === "won") return { text: "Ganada", tone: "good" };
  if (card.closed_at) return { text: card.close_reason ?? "Cerrada", tone: "neutral" };
  const conv = conversation(card, now);
  if (conv.kind === "replied") return { text: conv.text, tone: "warn" };
  if (conv.kind === "none") return { text: conv.text, tone: "neutral" };
  // A follow-up and a quote age the same way: the rhythm counts from OrigenLab's last touch.
  return { text: conv.text, tone: ageTone(conv.days) };
}

export function BoardCard({
  card,
  onOpen,
  now,
  drag = null,
  index = 0,
  compact = false,
  followUpStep: step,
  onLose,
}: {
  card: OpportunityCardData;
  onOpen: (id: string) => void;
  now?: Date;
  drag?: DragHook | null;
  index?: number;
  /** A closed case: the name, the quote and how it ended — nothing to act on. */
  compact?: boolean;
  /** A silent card: «Seguimiento N de 3». */
  followUpStep?: 1 | 2 | 3;
  /** «¿Marcar perdida?»: asks for the reason. Absent: no button. */
  onLose?: (card: OpportunityCardData) => void;
}) {
  const at = now ?? new Date();
  const latest = card.latest_revision;
  // One amount: the current quotation's neto, its number and revision (history is in the drawer).
  const cm = cardMoney(card);
  const money = cm.text || null;
  const { name, sub } = displayName(card);
  const contact = contactLine(card);
  // One clock per card: the status line says who touched the case last and for how long
  // («Sin respuesta · 154 d», «Seguimiento 07 may · 156 d sin respuesta»), and the column's bands
  // group by that same date. There is no second «last movement» badge: a record edit is not contact.
  const product = quoteProduct(card);
  const line = cardLine(card, at);
  const driveUrl = latest?.drive?.file_url ?? card.drive_folder?.url ?? null;
  const gmailUrl = inSharedMailbox(latest?.gmail?.url ?? card.last_contact?.inbound?.url ?? card.last_contact?.outbound?.url);
  const isDragging = drag?.current === card.opportunity_id;
  const open = (e: MouseEvent) => {
    if ((e.target as HTMLElement).closest("a, button")) return;
    onOpen(card.opportunity_id);
  };
  return (
    <article
      draggable={drag != null}
      onDragStart={
        drag
          ? (e) => {
              e.dataTransfer.setData(DRAG_TYPE, card.opportunity_id);
              e.dataTransfer.effectAllowed = "move";
              drag.set({ id: card.opportunity_id, from: drag.column });
            }
          : undefined
      }
      onDragEnd={drag ? () => drag.set(null) : undefined}
      onClick={open}
      title={card.next_action.source === "task" ? `Próxima tarea: ${card.next_action.text}` : `Sugerencia: ${card.next_action.text}`}
      data-testid={`board-card-${card.opportunity_id}`}
      style={{ ["--i" as string]: Math.min(index, 8) }}
      className={`crm-rise group min-w-0 cursor-pointer rounded-lg border bg-canvas-raised p-2.5 shadow-[0_1px_2px_rgb(28_25_23/0.04)] transition-[transform,box-shadow,border-color,opacity] duration-150 hover:-translate-y-0.5 hover:border-line-strong hover:shadow-md motion-reduce:transform-none ${
        drag ? "active:cursor-grabbing" : ""
      } ${isDragging ? "rotate-[-1.5deg] scale-[0.98] opacity-40" : "border-line"}`}
    >
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <h3 className="line-clamp-2 break-words text-[13px] font-semibold leading-[1.15rem] text-ink">
            <button
              type="button"
              onClick={() => onOpen(card.opportunity_id)}
              aria-haspopup="dialog"
              title={sub ? `En el CRM: ${sub}` : undefined}
              className="rounded text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
            >
              {name}
            </button>
          </h3>
          {contact && !compact ? <p className="truncate text-[11px] leading-4 text-ink-muted">{contact}</p> : null}
        </div>
      </div>
      <p className="mt-1.5 flex min-w-0 items-baseline gap-1 text-[11px] leading-4 tabular-nums text-ink-muted">
        {latest ? (
          <>
            <span className="shrink-0 font-semibold text-ink">{cm.cn ?? latest.quote_number}</span>
            {cm.rev != null ? (
              <span
                className="shrink-0 rounded bg-canvas-sunken px-1 py-px text-[10px] font-semibold text-ink-muted"
                title="Revisión vigente; el historial está en el caso"
                data-testid="board-rev"
              >
                rev {cm.rev}
              </span>
            ) : null}
            <span className="min-w-0 truncate">
              {card.quote_numbers.length > 1 ? <span className="text-ink-faint"> +{card.quote_numbers.length - 1}</span> : null}
              {` · ${shortDate(latest.sent_at, at)}`}
            </span>
          </>
        ) : (
          <span className="text-ink-faint">Sin cotización</span>
        )}
      </p>
      {/* One amount: the current quotation's neto — the number the month's goal counts. */}
      {money || (card.documents?.length ?? 0) > 0 ? (
        <p className="mt-0.5 flex items-baseline gap-1.5 tabular-nums">
          {money ? (
            <span
              className={cm.text === "sin leer" ? "text-[11px] text-ink-faint" : "text-[13px] font-semibold text-ink"}
              title={cm.text === "sin leer" ? "El PDF de la cotización vigente aún no se ha leído" : "Neto de la cotización vigente"}
              data-testid="board-money"
            >
              {money}
            </span>
          ) : null}
          {(card.documents?.length ?? 0) > 0 ? (
            <span
              className="shrink-0 rounded bg-good-bg px-1 text-[10.5px] font-semibold text-good"
              title="Orden de compra adjunta"
              data-testid="board-oc"
            >
              OC
            </span>
          ) : null}
        </p>
      ) : null}
      {product || card.origin ? (
        <div className="mt-1.5 flex min-w-0 flex-wrap items-center gap-1">
          {product ? (
            <span
              className="inline-block max-w-full truncate rounded-full border border-brand-600/25 bg-brand-50 px-1.5 py-px align-top text-[10.5px] leading-4 text-accent"
              title={`Cotización por: ${product}`}
              data-testid="board-product"
            >
              {product}
            </span>
          ) : null}
          {card.origin ? (
            <span
              className="inline-block max-w-full truncate rounded-full border border-line bg-canvas-sunken px-1.5 py-px align-top text-[10.5px] leading-4 text-ink-muted"
              title="Por dónde llegó el caso"
              data-testid="board-origin"
            >
              {originLabel(card.origin)}
            </span>
          ) : null}
        </div>
      ) : null}
      {step ? (
        <p className="mt-1.5 text-[11px] font-semibold leading-4 text-warn" data-testid="board-followup-step">
          Seguimiento {step} de 3
        </p>
      ) : null}
      <p className={`mt-1.5 line-clamp-2 text-[11px] font-medium leading-4 ${LINE_TONE[line.tone] ?? LINE_TONE.neutral}`} data-testid="board-status-line">
        {line.text}
      </p>
      {onLose ? (
        <button
          type="button"
          onClick={() => onLose(card)}
          aria-label={`¿Marcar perdida? ${name}`}
          className="mt-1.5 inline-flex min-h-11 w-full items-center justify-center rounded-md border border-line bg-canvas-raised px-2 text-[11.5px] font-medium text-ink hover:border-bad/50 hover:text-bad focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
        >
          ¿Marcar perdida?
        </button>
      ) : null}
      {compact ? null : (
        <div className="mt-2 flex min-w-0 flex-wrap items-center gap-x-1 gap-y-1 border-t border-line pt-1.5 text-[11px]">
          {driveUrl ? (
            <a
              href={driveUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex h-6 items-center gap-1 rounded px-1.5 text-ink-muted hover:bg-canvas-sunken hover:text-accent"
              aria-label={`PDF en Drive de ${name}`}
            >
              <DriveIcon />
              Drive
            </a>
          ) : isDelivered(latest) ? (
            <span className="inline-flex h-6 items-center gap-1 whitespace-nowrap px-1.5 text-ink-faint" title="El PDF todavía no está archivado en Drive">
              <DriveIcon />
              sin Drive
            </span>
          ) : null}
          {gmailUrl ? (
            <a
              href={gmailUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex h-6 items-center gap-1 rounded px-1.5 text-ink-muted hover:bg-canvas-sunken hover:text-accent"
              aria-label={`Correo de la cotización de ${name}`}
            >
              <MailIcon />
              Gmail
            </a>
          ) : null}
        </div>
      )}
    </article>
  );
}
