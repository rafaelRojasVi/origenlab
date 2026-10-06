/**
 * The Oportunidades «Tablero»: one column per stage group, readable even when one column holds
 * almost every case (the historical import put 50 of 51 cases in «Cotizando»).
 *
 * - Every column scrolls on its own, so a long column never stretches the page.
 * - A column with more than `GROUP_AFTER` cards is grouped by the age of its latest sent
 *   revision; the newest group is open, the older ones are collapsed behind their count.
 * - A board card leads with the client and the quote number, carries one status line instead of
 *   the card view's chips, and shows the full suggestion as its tooltip.
 *
 * Read-only: the board opens the case drawer and nothing else.
 */
import { useMemo } from "react";
import type { OpportunityCardData } from "../crmTypes";
import { AGE_BUCKETS, BOARD_COLUMNS, ageBucket, boardStatusLine, type AgeBucketKey } from "../stage";
import { fmtDate } from "../ui";

/** Columns longer than this are grouped by revision age. */
export const GROUP_AFTER = 8;

const LINE_TONE: Record<string, string> = {
  bad: "text-bad",
  warn: "text-warn",
  good: "text-good",
  neutral: "text-ink-muted",
};

export function Board({
  cards,
  onOpen,
  now,
}: {
  cards: OpportunityCardData[];
  onOpen: (id: string) => void;
  /** The moment ages are measured from; tests pass a fixed one. */
  now?: Date;
}) {
  const at = useMemo(() => now ?? new Date(), [now]);
  return (
    <div className="-mx-4 overflow-x-auto px-4 pb-2 sm:mx-0 sm:px-0">
      <div className="grid min-w-[64rem] grid-cols-5 gap-3">
        {BOARD_COLUMNS.map((col) => {
          const inCol = cards.filter((c) => col.stages.includes(c.stage));
          return (
            <section key={col.key} aria-label={col.label} className="flex min-w-0 flex-col">
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
                className="flex max-h-[70vh] min-h-44 flex-col gap-1.5 overflow-y-auto rounded-lg bg-canvas-sunken/70 p-1.5"
                data-testid={`board-column-${col.key}`}
              >
                {inCol.length === 0 ? (
                  <p className="rounded-md border border-dashed border-line-strong px-2 py-3 text-center text-[11px] text-ink-faint">
                    Sin oportunidades
                  </p>
                ) : inCol.length > GROUP_AFTER ? (
                  <AgeGroups cards={inCol} onOpen={onOpen} now={at} />
                ) : (
                  inCol.map((c) => <BoardCard key={c.opportunity_id} card={c} onOpen={onOpen} />)
                )}
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}

function AgeGroups({ cards, onOpen, now }: { cards: OpportunityCardData[]; onOpen: (id: string) => void; now: Date }) {
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
              <BoardCard key={c.opportunity_id} card={c} onOpen={onOpen} />
            ))}
          </div>
        </details>
      ))}
    </>
  );
}

export function BoardCard({ card, onOpen }: { card: OpportunityCardData; onOpen: (id: string) => void }) {
  const latest = card.latest_revision;
  const numbers = latest
    ? [latest.quote_number, ...card.quote_numbers.filter((n) => n !== latest.quote_number)]
    : card.quote_numbers;
  const line = boardStatusLine(card);
  const client = card.organization?.name ?? null;
  return (
    <button
      type="button"
      onClick={() => onOpen(card.opportunity_id)}
      aria-haspopup="dialog"
      title={`Sugerencia: ${card.next_action.text}`}
      data-testid={`board-card-${card.opportunity_id}`}
      className="block w-full min-w-0 rounded-md border border-line bg-canvas-raised px-2.5 py-2 text-left shadow-[0_1px_1px_rgb(24_24_27/0.03)] transition-[box-shadow,border-color] duration-150 hover:border-line-strong hover:shadow-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
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
