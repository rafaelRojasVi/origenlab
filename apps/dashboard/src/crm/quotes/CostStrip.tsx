/**
 * The strip under a Cotizador line: «Me cuesta» (the line's cost in pesos with the import expenses
 * the engine put on it) → the margin pill and «gano $» → «Le cobro (neto)» with «+ IVA =». For the
 * operator to read cost against price at a glance (owner, 2026-10-19; mockup «Lo que cuesta y lo que
 * se cobra»). Every number is the API's `line_economics`, only formatted: the browser adds nothing.
 *
 * Internal: costs and margins are for sales and administration (the Cotizador's API refuses anyone
 * else), the editor hides the strip in «Vista cliente», and the printable quote never carries it.
 * The pill is green inside the Preferencias margin range, amber outside it, red at a loss.
 */
import { formatMoney } from "../money";
import type { LineEconomics } from "./quoteApi";
import { formatPct } from "./quoteFormat";

const clp = (v: string | null | undefined) => formatMoney(v, "CLP");

const PILL: Record<string, string> = {
  on_target: "border-good bg-good-bg text-good",
  loss: "border-bad bg-bad-bg text-bad",
};
const PILL_OUTSIDE = "border-warn bg-warn-bg text-warn";

export function CostStrip({ economics, n, stale = false }: { economics: LineEconomics; n: number; stale?: boolean }) {
  const cost = clp(economics.cost_clp);
  const sale = clp(economics.sale_net_clp);
  const pct = formatPct(economics.gross_margin);
  const expenses = Number(economics.expenses_clp) > 0 ? clp(economics.expenses_clp) : null;
  const product = clp(economics.product_cost_clp);
  return (
    <div
      data-testid="cost-strip"
      aria-label={`Costo y margen de la línea ${n}`}
      aria-busy={stale || undefined}
      className={`mt-2 grid grid-cols-1 gap-2 sm:grid-cols-[1fr_auto_1fr]${stale ? " opacity-50" : ""}`}
    >
      <div className="flex min-w-0 flex-col gap-0.5 rounded-[10px] border border-line bg-canvas-sunken px-3 py-2">
        <span className="text-[12px] text-ink-muted">Me cuesta</span>
        <span className="font-mono text-[17px] font-medium tabular-nums text-ink">{cost ?? "—"}</span>
        <span className="text-[12px] text-ink-muted">
          {cost == null ? "Sin costo: no se puede saber el margen." : expenses && product ? `equipo ${product} + gastos ${expenses}` : "sin gastos de importación"}
        </span>
      </div>
      <div className="flex items-center justify-start gap-2.5 px-1 sm:flex-col sm:justify-center sm:gap-1 sm:text-center">
        <span className="text-[12px] text-ink-faint">margen</span>
        {pct ? (
          <span
            className={`whitespace-nowrap rounded-full border px-3 py-1 font-mono text-[15px] font-medium tabular-nums ${PILL[economics.margin_state] ?? PILL_OUTSIDE}`}
          >
            {pct}
          </span>
        ) : (
          <span className="font-mono text-[15px] text-ink-faint">—</span>
        )}
        {economics.gain_clp != null ? (
          <span className="text-[12px] text-ink-muted">
            gano <span className="font-mono tabular-nums">{clp(economics.gain_clp)}</span>
          </span>
        ) : null}
      </div>
      <div className="flex min-w-0 flex-col gap-0.5 rounded-[10px] border border-brand-700 bg-brand-50 px-3 py-2">
        <span className="text-[12px] text-ink-muted">Le cobro (neto)</span>
        <span className="font-mono text-[17px] font-medium tabular-nums text-brand-700">{sale ?? "—"}</span>
        {economics.total_with_iva_clp != null ? (
          <span className="text-[12px] text-ink-muted">+ IVA = {clp(economics.total_with_iva_clp)}</span>
        ) : null}
      </div>
    </div>
  );
}
