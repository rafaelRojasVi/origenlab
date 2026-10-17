/**
 * «Economía de la cotización»: a card of the Cotizador's rail — the sale (neto), the product cost,
 * the import costs with their split, and the gross margin $ and %. Internal only: it lives in the
 * rail, never in the client's document (`printableQuote` copies nothing of it).
 *
 * A draft shows the API's live computation (`computed.economics`, the same pass as the margin
 * above it); an issued revision what approval froze (`current.economics`). The margin is shown only
 * when every line has a cost; otherwise the card says how many lines lack one. Every number is the
 * API's, only formatted.
 */
import { useId } from "react";
import { formatMoney } from "../money";
import { IMPORT_COST_KINDS, coverageText, isZeroAmount, marginStateText } from "./economicsText";
import type { QuoteEconomics } from "./quoteApi";
import { formatPct } from "./quoteFormat";

const clp = (v: string | null | undefined) => formatMoney(v, "CLP") ?? "—";

/** The import costs by kind; a kind with nothing is left out. */
export function ImportSplit({ economics }: { economics: QuoteEconomics }) {
  const kinds = IMPORT_COST_KINDS.filter((k) => !isZeroAmount(economics.import_costs_split[k.kind]));
  if (kinds.length === 0) return <p className="text-[13px] text-ink-muted">Sin gastos de importación.</p>;
  return (
    <dl aria-label="Gastos de importación por tipo" className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-6 gap-y-0.5 text-[13px]">
      {kinds.map((k) => (
        <div key={k.kind} className="contents">
          <dt className="text-ink-muted">{k.label}</dt>
          <dd className="text-right font-mono tabular-nums">{clp(economics.import_costs_split[k.kind])}</dd>
        </div>
      ))}
    </dl>
  );
}

export function EconomicsCard({ economics, stale = false }: { economics: QuoteEconomics | null | undefined; stale?: boolean }) {
  const headingId = useId();
  const pct = formatPct(economics?.gross_margin_pct);
  const st = marginStateText(economics?.margin_state);
  return (
    <section
      aria-labelledby={headingId}
      aria-busy={stale || undefined}
      data-testid="economia-cotizacion"
      className={`flex flex-col gap-2 rounded-[14px] border border-line bg-canvas-raised px-4 py-3.5${stale ? " opacity-60" : ""}`}
    >
      <h2 id={headingId} className="text-[15px] font-semibold text-ink">
        Economía de la cotización
      </h2>
      {!economics ? (
        <p className="text-[13px] text-ink-muted">Todavía no hay precios calculados.</p>
      ) : (
        <>
          <dl className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-1 text-[13.5px]">
            <dt className="text-ink-muted">Venta neta</dt>
            <dd className="text-right font-mono tabular-nums">{clp(economics.sale_net_clp)}</dd>
            <dt className="text-ink-muted">Costo de productos</dt>
            <dd className="text-right font-mono tabular-nums">{clp(economics.product_cost_clp)}</dd>
            <dt className="text-ink-muted">Gastos de importación</dt>
            <dd className="text-right font-mono tabular-nums">{clp(economics.import_costs_clp)}</dd>
          </dl>
          <div className="pl-3">
            <ImportSplit economics={economics} />
          </div>
          <div className="h-px bg-line" />
          <div className="flex items-baseline justify-between gap-3">
            <span className="text-[13.5px] font-semibold text-ink">Margen bruto</span>
            {economics.gross_margin_clp == null ? (
              <span className="text-[13px] text-ink-muted">—</span>
            ) : (
              <span className="text-right">
                <span className="font-mono font-semibold tabular-nums">{clp(economics.gross_margin_clp)}</span>
                {pct ? <span className={`ml-2 font-semibold tabular-nums ${st.cls}`}>{pct}</span> : null}
              </span>
            )}
          </div>
          {economics.gross_margin_clp == null ? (
            <p className="text-[12.5px] text-ink-muted">Sin margen: {coverageText(economics.cost_coverage).toLowerCase()}.</p>
          ) : (
            <p className={`text-[12.5px] ${st.cls}`}>{st.label}</p>
          )}
          <p className="text-[11.5px] text-ink-faint">Uso interno: no aparece en el PDF. No incluye gastos generales de la empresa.</p>
        </>
      )}
    </section>
  );
}
