/**
 * The Oportunidades «Tablero»: one column per state (Solicitada, En estudio, Enviada,
 * Conversación, En pausa, Ganada, Perdida), readable even when one column holds almost every case
 * (the historical import put 50 of 51 cases in «Enviada»).
 *
 * - Every column scrolls on its own, so a long column never stretches the page.
 * - A column with more than `GROUP_AFTER` cards is grouped by the age of its latest sent
 *   revision; the newest group is open, the older ones are collapsed behind their count.
 * - A board card leads with the client and the quote number, carries one status line instead of
 *   the card view's chips, and shows the full suggestion as its tooltip.
 *
 * - With `onMove` (the operator may decide cases), a card can be dragged to another column. The
 *   board writes nothing itself: it says which card went where, and the page asks for what the
 *   move needs (a reason, a date, the revision) before anything is recorded. A click still opens
 *   the drawer, whose «Cambiar estado» does the same without a mouse.
 */
import { useMemo, useState, type DragEvent } from "react";
import type { OpportunityCardData } from "../crmTypes";
import {
  AGE_BUCKETS,
  BOARD_COLUMNS,
  ageBucket,
  boardColumnOf,
  boardStatusLine,
  pausedUntil,
  type AgeBucketKey,
  type BoardColumnKey,
} from "../stage";
import { fmtDate } from "../ui";

/** Columns longer than this are grouped by revision age. */
export const GROUP_AFTER = 8;

const LINE_TONE: Record<string, string> = {
  bad: "text-bad",
  warn: "text-warn",
  good: "text-good",
  info: "text-info",
  neutral: "text-ink-muted",
};

const DRAG_TYPE = "text/plain";

export function Board({
  cards,
  onOpen,
  onMove,
  now,
}: {
  cards: OpportunityCardData[];
  onOpen: (id: string) => void;
  /** A card was dropped on another column. Absent: the board is read-only. */
  onMove?: (card: OpportunityCardData, to: BoardColumnKey) => void;
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

  return (
    <div className="-mx-4 overflow-x-auto px-4 pb-2 sm:mx-0 sm:px-0">
      {onMove ? (
        <p className="mb-2 text-[11px] text-ink-faint">Arrastra una tarjeta a otra columna para cambiar su estado.</p>
      ) : null}
      <div className="grid min-w-[84rem] grid-cols-7 gap-3">
        {BOARD_COLUMNS.map((col) => {
          const inCol = cards.filter((c) => boardColumnOf(c, at) === col.key);
          const droppable = onMove != null && dragging != null && dragging.from !== col.key;
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
                <h2 className="text-[13px] font-semibold text-ink">{col.label}</h2>
                <span
                  className="rounded-full bg-canvas-sunken px-1.5 py-px text-[11px] tabular-nums text-ink-muted"
                  data-testid={`board-count-${col.key}`}
                >
                  {inCol.length}
                </span>
              </header>
              <div
                className={`flex max-h-[70vh] min-h-44 flex-col gap-1.5 overflow-y-auto rounded-lg p-1.5 transition-colors ${
                  over === col.key
                    ? "bg-brand-50 ring-2 ring-brand-600"
                    : droppable
                      ? "bg-canvas-sunken/70 ring-1 ring-dashed ring-brand-600/40"
                      : "bg-canvas-sunken/70"
                }`}
                data-testid={`board-column-${col.key}`}
              >
                {inCol.length === 0 ? (
                  <p className="rounded-md border border-dashed border-line-strong px-2 py-3 text-center text-[11px] text-ink-faint">
                    Sin oportunidades
                  </p>
                ) : inCol.length > GROUP_AFTER ? (
                  <AgeGroups cards={inCol} onOpen={onOpen} now={at} drag={onMove ? { column: col.key, set: setDragging } : null} />
                ) : (
                  inCol.map((c) => (
                    <BoardCard
                      key={c.opportunity_id}
                      card={c}
                      onOpen={onOpen}
                      now={at}
                      drag={onMove ? { column: col.key, set: setDragging } : null}
                    />
                  ))
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
}

function AgeGroups({
  cards,
  onOpen,
  now,
  drag,
}: {
  cards: OpportunityCardData[];
  onOpen: (id: string) => void;
  now: Date;
  drag: DragHook | null;
}) {
  const groups = new Map<AgeBucketKey, OpportunityCardData[]>();
  for (const c of cards) {
    const key = ageBucket(c.latest_revision?.sent_at, now);
    groups.set(key, [...(groups.get(key) ?? []), c]);
  }
  const present = AGE_BUCKETS.filter((b) => groups.has(b.key));
  return (
    <>
      {present.map((b, i) => (
        <details key={b.key} open={i === 0} className="group/age" data-testid={`board-age-${b.key}`}>
          <summary className="flex cursor-pointer list-none items-center gap-1.5 rounded px-1 py-0.5 text-[11px] font-medium text-ink-muted hover:text-ink">
            <span aria-hidden="true" className="inline-block transition-transform group-open/age:rotate-90">
              ▸
            </span>
            {b.label}
            <span className="tabular-nums text-ink-faint">{groups.get(b.key)!.length}</span>
          </summary>
          <div className="mt-1 flex flex-col gap-1.5">
            {groups.get(b.key)!.map((c) => (
              <BoardCard key={c.opportunity_id} card={c} onOpen={onOpen} now={now} drag={drag} />
            ))}
          </div>
        </details>
      ))}
    </>
  );
}

export function BoardCard({
  card,
  onOpen,
  now,
  drag = null,
}: {
  card: OpportunityCardData;
  onOpen: (id: string) => void;
  now?: Date;
  drag?: DragHook | null;
}) {
  const latest = card.latest_revision;
  const numbers = latest
    ? [latest.quote_number, ...card.quote_numbers.filter((n) => n !== latest.quote_number)]
    : card.quote_numbers;
  const until = pausedUntil(card, now);
  const line = until
    ? { text: `Hasta ${fmtDate(until)} · ${card.open_tasks?.[0]?.title ?? "en pausa"}`, tone: "info" }
    : boardStatusLine(card);
  const client = card.organization?.name ?? null;
  return (
    <button
      type="button"
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
      onClick={() => onOpen(card.opportunity_id)}
      aria-haspopup="dialog"
      title={card.next_action.source === "task" ? `Próxima tarea: ${card.next_action.text}` : `Sugerencia: ${card.next_action.text}`}
      data-testid={`board-card-${card.opportunity_id}`}
      className="block w-full min-w-0 cursor-pointer rounded-md border border-line bg-canvas-raised px-2.5 py-2 text-left active:cursor-grabbing shadow-[0_1px_1px_rgb(24_24_27/0.03)] transition-[box-shadow,border-color] duration-150 hover:border-line-strong hover:shadow-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
    >
      <span className="block truncate text-[13px] font-semibold leading-5 text-ink">
        {client ?? <span className="text-bad">Sin institución</span>}
      </span>
      <span className="block truncate text-[11px] leading-4 text-ink tabular-nums">
        {numbers.length ? (
          <>
            <span className="font-semibold">{numbers[0]}</span>
            {numbers.length > 1 ? <span className="text-ink-faint"> +{numbers.length - 1}</span> : null}
            {latest ? (
              <span className="text-ink-muted">
                {" "}
                · r{latest.revision_no} · {fmtDate(latest.sent_at)}
              </span>
            ) : null}
          </>
        ) : (
          <span className="text-ink-faint">Sin cotización</span>
        )}
      </span>
      <span className={`mt-0.5 block truncate text-[11px] leading-4 ${LINE_TONE[line.tone] ?? LINE_TONE.neutral}`} data-testid="board-status-line">
        {line.text}
      </span>
    </button>
  );
}
