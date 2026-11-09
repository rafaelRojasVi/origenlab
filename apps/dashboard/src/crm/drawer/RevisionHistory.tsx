/**
 * «Historial de esta cotización»: every revision of a quotation, newest first, with its sent date,
 * its neto and what changed from the one before (`revisionChanges`). A quote with revisions shows
 * one amount and a «rev N» chip everywhere else; its history lives here (owner ruling 2026-11-08).
 */
import { useId } from "react";
import type { OpportunityCardData, QuoteCard } from "../crmTypes";
import { formatMoney } from "../money";
import { currentRevision, quotesNewestFirst } from "../pages/CaseQuotes";
import { revisionChanges } from "../revisionDiff";
import { fmtDate } from "../ui";

function QuoteHistory({ q, named }: { q: QuoteCard; named: boolean }) {
  const oldestFirst = [...q.revisions].sort((a, b) => a.revision_no - b.revision_no);
  const vigente = currentRevision(q);
  const rows = oldestFirst
    .map((r, i) => ({ r, changes: i === 0 ? ["Primera versión"] : revisionChanges(oldestFirst[i - 1], r) }))
    .reverse();
  return (
    <div>
      {named ? <p className="mb-1 font-mono text-[12px] font-semibold text-ink">{q.quote_number}</p> : null}
      <ol className="space-y-2.5 border-l border-line pl-3">
        {rows.map(({ r, changes }) => {
          const net = r.contents ? formatMoney(r.contents.net_total, r.contents.currency) : null;
          return (
            <li key={r.revision_id} className="text-[12.5px] leading-5">
              <p className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                <span className="inline-flex items-center rounded-full bg-canvas-sunken px-1.5 text-[11px] font-semibold text-ink tabular-nums">
                  rev {r.revision_no}
                </span>
                <span className="text-ink-muted">{r.sent_at ? `enviada ${fmtDate(r.sent_at)}` : "sin enviar"}</span>
                {net ? <span className="font-medium text-ink tabular-nums">{net}</span> : null}
                {r.status === "void" ? (
                  <span className="text-[11px] font-medium text-bad">anulada</span>
                ) : vigente?.revision_id === r.revision_id ? (
                  <span className="text-[11px] font-medium text-good">vigente</span>
                ) : null}
              </p>
              <div className="mt-0.5 text-[12px] text-ink-muted">
                {changes.map((c) => (
                  <span key={c} className="block">
                    {c}
                  </span>
                ))}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

export function RevisionHistory({ card }: { card: OpportunityCardData }) {
  const headingId = useId();
  const quotes = quotesNewestFirst(card).filter((q) => q.revisions.length > 1);
  if (quotes.length === 0) return null;
  return (
    <section aria-labelledby={headingId} data-testid="case-revision-history">
      <h3 id={headingId} className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-ink-faint">
        Historial de esta cotización
      </h3>
      <div className="space-y-3">
        {quotes.map((q) => (
          <QuoteHistory key={q.quote_id} q={q} named={card.quotes.length > 1} />
        ))}
      </div>
    </section>
  );
}
