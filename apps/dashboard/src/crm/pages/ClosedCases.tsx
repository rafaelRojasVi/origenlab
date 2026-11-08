/**
 * The Pipeline «Cerradas» view: the lost and abandoned cases that left the Tablero (closed more
 * than `LOST_ON_BOARD_DAYS` ago — `isOnBoard`), one table per month on Santiago's clock, newest
 * month first. Each month's header leads with what was lost — how many cases and the neto they
 * quoted (`columnMoney`: CLP only, «+ N sin leer» for a PDF nobody read yet) — and the reasons,
 * most frequent first. Won cases never come here: they stay on the board (owner ruling 2026-11-08).
 * A row opens the case drawer.
 */
import { useMemo } from "react";
import { displayName } from "../caseDisplay";
import type { OpportunityCardData } from "../crmTypes";
import { GrowingListEnd } from "../GrowingListEnd";
import { formatMoney } from "../money";
import { closedAt, isOnBoard, monthKeySantiago } from "../stage";
import { EmptyState, fmtDate } from "../ui";
import { useGrowingList } from "../useGrowingList";
import { cardMoney, columnMoney } from "./PipelineBoard";

export const NO_REASON = "Sin motivo";

const MONTHS = [
  "enero", "febrero", "marzo", "abril", "mayo", "junio",
  "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
];

export interface ClosedMonth {
  /** `YYYY-MM` on Santiago's clock. */
  month: string;
  /** «octubre 2026». */
  label: string;
  count: number;
  /** Neto quoted in CLP across the month's cases (`columnMoney`). */
  clp: number;
  /** Cases whose quotation PDF nobody read yet: not in `clp`. */
  unread: number;
  /** Most frequent first, then by name. */
  reasons: { reason: string; count: number }[];
  /** Newest closing first. */
  cards: OpportunityCardData[];
}

function monthLabel(key: string): string {
  const [year, month] = key.split("-");
  return `${MONTHS[Number(month) - 1] ?? month} ${year}`;
}

/** The `lost`/`abandoned` cases off the board, grouped by the Santiago month they closed in. */
export function closedByMonth(cards: readonly OpportunityCardData[], now: Date = new Date()): ClosedMonth[] {
  const byMonth = new Map<string, { at: number; card: OpportunityCardData }[]>();
  for (const card of cards) {
    if (card.stage !== "lost" && card.stage !== "abandoned") continue;
    if (isOnBoard(card, now)) continue;
    const iso = closedAt(card);
    if (!iso) continue;
    const key = monthKeySantiago(iso);
    const list = byMonth.get(key) ?? [];
    list.push({ at: Date.parse(iso), card });
    byMonth.set(key, list);
  }
  return [...byMonth.entries()]
    .sort(([a], [b]) => b.localeCompare(a))
    .map(([month, list]) => {
      const sorted = list.sort((a, b) => b.at - a.at).map((e) => e.card);
      const counts = new Map<string, number>();
      for (const c of sorted) {
        const reason = c.close_reason?.trim() || NO_REASON;
        counts.set(reason, (counts.get(reason) ?? 0) + 1);
      }
      const reasons = [...counts.entries()]
        .map(([reason, count]) => ({ reason, count }))
        .sort((a, b) => b.count - a.count || a.reason.localeCompare(b.reason, "es"));
      const { clp, unread } = columnMoney(sorted);
      return { month, label: monthLabel(month), count: sorted.length, clp, unread, reasons, cards: sorted };
    });
}

export function ClosedCases({
  cards,
  onOpen,
  now,
}: {
  cards: readonly OpportunityCardData[];
  onOpen: (id: string) => void;
  /** The clock the 7-day rule reads; tests pass a fixed one. */
  now?: Date;
}) {
  const at = now?.getTime();
  const months = useMemo(() => closedByMonth(cards, at === undefined ? new Date() : new Date(at)), [cards, at]);
  return (
    <div className="space-y-4">
      <p className="text-xs text-ink-muted" data-testid="closed-note">
        Pérdidas de más de 7 días, por mes en que se cerraron. Las ganadas siguen en el tablero.
      </p>
      {months.length === 0 ? (
        <EmptyState title="Sin casos cerrados todavía">
          Una pérdida pasa aquí 7 días después de cerrarse; hasta entonces sigue en «Perdida» del tablero.
        </EmptyState>
      ) : (
        months.map((m) => <MonthTable key={m.month} month={m} onOpen={onOpen} />)
      )}
    </div>
  );
}

function MonthTable({ month, onOpen }: { month: ClosedMonth; onOpen: (id: string) => void }) {
  const { shown, hidden, showMore, endRef } = useGrowingList(month.cards);
  const headingId = `closed-month-heading-${month.month}`;
  return (
    <section
      aria-labelledby={headingId}
      className="overflow-hidden rounded-lg border border-line bg-canvas-raised"
      data-testid={`closed-month-${month.month}`}
    >
      <header className="space-y-2 border-b border-line bg-canvas-sunken/60 px-3 py-2.5 sm:px-4">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h2 id={headingId} className="text-[14px] font-semibold capitalize text-ink">
            {month.label}
          </h2>
          <p className="text-[12.5px] tabular-nums text-ink-muted" data-testid="closed-month-summary">
            {month.count} {month.count === 1 ? "caso" : "casos"} ·{" "}
            <span className="text-[15px] font-semibold text-bad">{formatMoney(String(month.clp), "CLP")}</span>
            <span className="text-ink-faint"> neto perdido</span>
            {month.unread > 0 ? (
              <span className="text-ink-faint" title="Cotizaciones cuyo PDF aún no se ha leído: no están sumadas">
                {" "}+ {month.unread} sin leer
              </span>
            ) : null}
          </p>
        </div>
        <ul className="flex flex-wrap gap-1.5" aria-label="Motivos">
          {month.reasons.map((r, i) => (
            <li
              key={r.reason}
              data-testid="closed-reason"
              className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11.5px] tabular-nums ${
                i === 0 && r.reason !== NO_REASON ? "bg-bad-bg font-semibold text-bad" : "bg-canvas-sunken text-ink-muted"
              }`}
            >
              {r.reason} · {r.count}
            </li>
          ))}
        </ul>
      </header>
      <table className="w-full table-fixed text-left text-[12.5px]">
        <thead className="text-[11px] uppercase tracking-wide text-ink-faint">
          <tr>
            <th scope="col" className="w-auto px-3 py-1.5 font-medium sm:px-4">Institución</th>
            <th scope="col" className="hidden w-24 px-2 py-1.5 font-medium sm:table-cell">CN</th>
            <th scope="col" className="w-28 px-2 py-1.5 text-right font-medium sm:w-32">Neto</th>
            <th scope="col" className="w-28 px-2 py-1.5 font-medium sm:w-40">Motivo</th>
            <th scope="col" className="hidden w-28 px-3 py-1.5 font-medium sm:table-cell sm:px-4">Cerrada el</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((card) => (
            <ClosedRow key={card.opportunity_id} card={card} onOpen={onOpen} />
          ))}
        </tbody>
      </table>
      {hidden > 0 ? (
        <div className="border-t border-line p-1">
          <GrowingListEnd hidden={hidden} onMore={showMore} endRef={endRef} />
        </div>
      ) : null}
    </section>
  );
}

function ClosedRow({ card, onOpen }: { card: OpportunityCardData; onOpen: (id: string) => void }) {
  const { name } = displayName(card);
  const money = cardMoney(card);
  const reason = card.close_reason?.trim() || NO_REASON;
  const closed = fmtDate(closedAt(card));
  return (
    <tr
      data-testid={`closed-row-${card.opportunity_id}`}
      onClick={() => onOpen(card.opportunity_id)}
      className="cursor-pointer border-t border-line align-top hover:bg-canvas-sunken/60"
    >
      <td className="px-3 py-2 sm:px-4">
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onOpen(card.opportunity_id);
          }}
          className="block w-full truncate text-left font-medium text-ink hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
          title={name}
        >
          {name}
        </button>
        {/* On a phone the CN and the date ride under the name. */}
        <span className="block truncate text-[11.5px] tabular-nums text-ink-faint sm:hidden">
          {money.cn ? `${money.cn} · ` : ""}
          {closed}
        </span>
      </td>
      <td className="hidden px-2 py-2 tabular-nums text-ink-muted sm:table-cell">{money.cn ?? "—"}</td>
      <td className={`px-2 py-2 text-right tabular-nums ${money.text === "sin leer" ? "text-ink-faint" : "text-ink"}`}>
        {money.text || "—"}
      </td>
      <td className={`truncate px-2 py-2 ${reason === NO_REASON ? "text-ink-faint" : "text-ink-muted"}`} title={reason}>
        {reason}
      </td>
      <td className="hidden px-3 py-2 tabular-nums text-ink-muted sm:table-cell sm:px-4">{closed}</td>
    </tr>
  );
}
