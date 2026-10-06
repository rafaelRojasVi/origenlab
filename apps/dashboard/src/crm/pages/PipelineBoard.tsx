/**
 * The Oportunidades «Tablero»: one column per state (Solicitada, En estudio, Enviada,
 * Conversación, En pausa, Ganada, Perdida).
 *
 * - Every card is shown; every column scrolls on its own, so a long column never stretches the
 *   page. `sort` orders them (newest quote, oldest, «respondieron primero», institution); by date,
 *   a thin divider marks each age band («Últimos 30 días», «31–90 días»…) without hiding a card.
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
import { ageTone, contactLine, conversation, daysSince, displayName, quoteProduct } from "../caseDisplay";
import { AGE_BUCKETS, BOARD_COLUMNS, ageBucket, boardColumnOf, pausedUntil, type BoardColumnKey } from "../stage";
import { fmtDate, type Tone } from "../ui";

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
  brand: "text-brand-700",
  neutral: "text-ink-muted",
};

const BADGE_TONE: Record<Tone, string> = {
  brand: "bg-brand-50 text-brand-700",
  info: "bg-info-bg text-info",
  warn: "bg-warn-bg text-warn",
  bad: "bg-bad-bg text-bad",
  good: "bg-good-bg text-good",
  neutral: "bg-canvas-sunken text-ink-muted",
};

const COLUMN_DOT: Record<BoardColumnKey, string> = {
  solicitada: "bg-ink-faint",
  estudio: "bg-info",
  enviada: "bg-brand-600",
  conversacion: "bg-warn",
  pausa: "bg-violet-600",
  ganada: "bg-good",
  perdida: "bg-bad",
};

const DRAG_TYPE = "text/plain";

const MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sept", "oct", "nov", "dic"];

/** «24 sept», with the year only when it is not this one («12 may 2025»). */
export function shortDate(iso: string | null | undefined, now: Date): string {
  const d = iso ? new Date(iso) : null;
  if (!d || Number.isNaN(d.getTime())) return "—";
  const base = `${d.getDate()} ${MONTHS[d.getMonth()]}`;
  return d.getFullYear() === now.getFullYear() ? base : `${base} ${d.getFullYear()}`;
}

const sentAt = (c: OpportunityCardData) => c.latest_revision?.sent_at ?? "";

export function sortCards(cards: OpportunityCardData[], sort: BoardSort, now: Date): OpportunityCardData[] {
  const out = [...cards];
  if (sort === "name") return out.sort((a, b) => displayName(a).name.localeCompare(displayName(b).name, "es"));
  if (sort === "replied") {
    const rank = (c: OpportunityCardData) => ({ replied: 0, followed_up: 1, silent: 2, none: 3 })[conversation(c, now).kind];
    return out.sort((a, b) => rank(a) - rank(b) || sentAt(b).localeCompare(sentAt(a)));
  }
  out.sort((a, b) => sentAt(b).localeCompare(sentAt(a)) || a.title.localeCompare(b.title));
  return sort === "oldest" ? out.reverse() : out;
}

export function Board({
  cards,
  onOpen,
  onMove,
  sort = "recent",
  now,
}: {
  cards: OpportunityCardData[];
  onOpen: (id: string) => void;
  /** A card was dropped on another column. Absent: the board is read-only. */
  onMove?: (card: OpportunityCardData, to: BoardColumnKey) => void;
  sort?: BoardSort;
  /** The moment ages and pauses are measured from; tests pass a fixed one. */
  now?: Date;
}) {
  const at = useMemo(() => now ?? new Date(), [now]);
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
      <div className="grid min-w-[72rem] grid-cols-[repeat(5,minmax(10rem,1fr))_repeat(2,minmax(8.5rem,0.75fr))] gap-3">
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
              <header className="mb-1.5 flex items-center gap-1.5 px-0.5">
                <span aria-hidden="true" className={`h-2 w-2 rounded-full ${COLUMN_DOT[col.key]}`} />
                <h2 className="text-[13px] font-semibold text-ink">{col.label}</h2>
                <span
                  className="rounded-full bg-canvas-sunken px-1.5 py-px text-[11px] tabular-nums text-ink-muted"
                  data-testid={`board-count-${col.key}`}
                >
                  {inCol.length}
                </span>
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
                    {droppable ? "Suelta aquí" : "Sin oportunidades"}
                  </p>
                ) : (
                  inCol.map((c, i) => {
                    const band = byDate ? ageBucket(c.latest_revision?.sent_at, at) : null;
                    const prev = byDate && i > 0 ? ageBucket(inCol[i - 1].latest_revision?.sent_at, at) : null;
                    const label = band && (i === 0 || band !== prev) ? AGE_BUCKETS.find((b) => b.key === band)?.label : null;
                    return (
                      <Fragment key={c.opportunity_id}>
                        {label && inCol.length > 4 ? (
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
                          compact={col.key === "ganada" || col.key === "perdida"}
                        />
                      </Fragment>
                    );
                  })
                )}
              </div>
            </section>
          );
        })}
      </div>
    </div>
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
  if (conv.kind === "followed_up") return { text: conv.text, tone: "info" };
  if (conv.kind === "none") return { text: conv.text, tone: "neutral" };
  return { text: conv.text, tone: ageTone(daysSince(card.latest_revision?.sent_at, now)) };
}

export function BoardCard({
  card,
  onOpen,
  now,
  drag = null,
  index = 0,
  compact = false,
}: {
  card: OpportunityCardData;
  onOpen: (id: string) => void;
  now?: Date;
  drag?: DragHook | null;
  index?: number;
  /** A closed case: the name, the quote and how it ended — nothing to act on. */
  compact?: boolean;
}) {
  const at = now ?? new Date();
  const latest = card.latest_revision;
  const { name, sub } = displayName(card);
  const contact = contactLine(card);
  const days = card.closed_at ? null : daysSince(latest?.sent_at, at);
  const product = quoteProduct(card);
  const line = cardLine(card, at);
  const driveUrl = latest?.drive?.file_url ?? card.drive_folder?.url ?? null;
  const gmailUrl = inSharedMailbox(latest?.gmail?.url);
  const pending = card.attention.filter((a) => !a.blocking).length;
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
          <h3 className="line-clamp-2 text-[13px] [overflow-wrap:anywhere] font-semibold leading-[1.15rem] text-ink">
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
        {days != null ? (
          <span
            className={`shrink-0 rounded px-1 py-px text-[10.5px] font-semibold tabular-nums ${BADGE_TONE[ageTone(days)]}`}
            title={`Cotización enviada hace ${days} días`}
            data-testid="board-age"
          >
            {days === 0 ? "hoy" : `${days} d`}
          </span>
        ) : null}
      </div>
      <p className="mt-1.5 truncate text-[11px] leading-4 tabular-nums text-ink-muted">
        {latest ? (
          <>
            <span className="font-semibold text-ink">{latest.quote_number}</span>
            {card.quote_numbers.length > 1 ? <span className="text-ink-faint"> +{card.quote_numbers.length - 1}</span> : null} ·{" "}
            {shortDate(latest.sent_at, at)}
          </>
        ) : (
          <span className="text-ink-faint">Sin cotización</span>
        )}
      </p>
      {product ? (
        <span
          className="mt-1.5 inline-block max-w-full truncate rounded-full border border-brand-600/25 bg-brand-50 px-1.5 py-px align-top text-[10.5px] leading-4 text-brand-700"
          title={`Cotización por: ${product}`}
          data-testid="board-product"
        >
          {product}
        </span>
      ) : null}
      <p className={`mt-1.5 line-clamp-2 text-[11px] font-medium leading-4 ${LINE_TONE[line.tone] ?? LINE_TONE.neutral}`} data-testid="board-status-line">
        {line.text}
      </p>
      {compact ? null : (
        <div className="mt-2 flex items-center gap-1 border-t border-line pt-1.5 text-[11px]">
          {driveUrl ? (
            <a
              href={driveUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex h-6 items-center gap-1 rounded px-1.5 text-ink-muted hover:bg-canvas-sunken hover:text-brand-700"
              aria-label={`PDF en Drive de ${name}`}
            >
              <DriveIcon />
              Drive
            </a>
          ) : (
            <span className="inline-flex h-6 items-center gap-1 whitespace-nowrap px-1.5 text-ink-faint" title="El PDF todavía no está archivado en Drive">
              <DriveIcon />
              sin Drive
            </span>
          )}
          {gmailUrl ? (
            <a
              href={gmailUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex h-6 items-center gap-1 rounded px-1.5 text-ink-muted hover:bg-canvas-sunken hover:text-brand-700"
              aria-label={`Correo de la cotización de ${name}`}
            >
              <MailIcon />
              Gmail
            </a>
          ) : null}
          {pending > 0 ? (
            <span className="ml-auto whitespace-nowrap rounded-full bg-warn-bg px-1.5 text-[10px] font-semibold text-warn" title={card.attention.map((a) => a.label).join(" · ")}>
              {pending} pendiente{pending > 1 ? "s" : ""}
            </span>
          ) : null}
        </div>
      )}
    </article>
  );
}
