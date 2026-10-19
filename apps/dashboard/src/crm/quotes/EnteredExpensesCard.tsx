/**
 * «Gastos ingresados»: under «Economía de la cotización» on an approved or sent revision, each
 * import expense as the operator typed it, how its amount was set and its pesos, with the total —
 * which is «Gastos de importación» of the card above (the card says so if they ever differ).
 * Internal, like Economía: the rail hides it in «Vista cliente» and the printable quote never
 * carries it (`printableQuote` copies items only). See `enteredExpenses` for the numbers.
 */
import { useId } from "react";
import { formatMoney } from "../money";
import { enteredExpenses } from "./enteredExpenses";
import type { QuoteStoredLine } from "./quoteApi";

const clp = (v: string | null | undefined) => formatMoney(v, "CLP") ?? "—";

export function EnteredExpensesCard({ lines, importCostsClp }: { lines: QuoteStoredLine[] | null | undefined; importCostsClp: string | null | undefined }) {
  const headingId = useId();
  const { rows, totalClp } = enteredExpenses(lines);
  const mismatch = importCostsClp != null && totalClp != null && totalClp !== String(importCostsClp);
  return (
    <section aria-labelledby={headingId} data-testid="gastos-ingresados" className="flex flex-col gap-2 rounded-[14px] border border-line bg-canvas-raised px-4 py-3.5">
      <h2 id={headingId} className="text-[15px] font-semibold text-ink">
        Gastos ingresados
      </h2>
      {rows.length === 0 ? (
        <p className="text-[13px] text-ink-muted">Sin gastos de importación.</p>
      ) : (
        <>
          <ul className="flex flex-col gap-2 text-[13px]">
            {rows.map((r) => (
              <li key={r.lineNo} className="flex items-start justify-between gap-3">
                <span className="min-w-0">
                  <span className="block break-words text-ink">
                    {r.text}
                    {r.tag ? (
                      <span className="ml-1.5 inline-block rounded border border-line bg-canvas-sunken px-1.5 align-middle text-[11px] text-ink-muted">{r.tag}</span>
                    ) : null}
                  </span>
                  <span className="block text-[11.5px] text-ink-muted">{r.method}</span>
                </span>
                <span className="shrink-0 text-right font-mono tabular-nums">
                  <span className="block">{clp(r.amountClp)}</span>
                  {r.currency !== "CLP" ? <span className="block text-[11.5px] text-ink-muted">{formatMoney(r.amount, r.currency)}</span> : null}
                </span>
              </li>
            ))}
          </ul>
          <div className="h-px bg-line" />
          <dl className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 text-[13.5px]">
            <dt className="font-semibold text-ink">Total</dt>
            <dd className="text-right font-mono font-semibold tabular-nums">{totalClp == null ? "—" : clp(totalClp)}</dd>
          </dl>
          {totalClp == null ? <p className="text-[12.5px] text-warn">Falta el tipo de cambio de un gasto: no hay total.</p> : null}
          {mismatch ? <p className="text-[12.5px] text-warn">El total no cuadra con «Gastos de importación» de la Economía.</p> : null}
        </>
      )}
      <p className="text-[11.5px] text-ink-faint">Uso interno: no aparece en el PDF.</p>
    </section>
  );
}
