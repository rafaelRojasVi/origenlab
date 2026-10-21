/**
 * The strip under a Cotizador line, one compact row: «Me cuesta» (the line's cost in pesos with the
 * import expenses the engine put on it) · the margin pill and «gano $» · «Le cobro (neto)». IVA is
 * only in the quote's total, never per line (owner, 2026-10-21: the line showed its price twice).
 * For the operator to read cost against price at a glance (owner, 2026-10-19; mockup «Lo que cuesta
 * y lo que se cobra»; tightened to one row 2026-10-21). Every number is the API's `line_economics`, only formatted: the browser adds nothing.
 *
 * Internal: costs and margins are for sales and administration (the Cotizador's API refuses anyone
 * else), the editor hides the strip in «Vista cliente», and the printable quote never carries it.
 * The pill is green inside the Preferencias margin range, amber outside it, red at a loss. No pill
 * when the API gives no margin: «Falta precio» for a line with a cost and no price, nothing for a
 * quote whose import expenses have no principal line to go to (`expenses_unallocated`: the section
 * says once to choose one), so a false margin is never shown.
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
  const state = economics.margin_state;
  const costNote =
    cost == null
      ? "Sin costo: no se puede saber el margen."
      : state === "expenses_unallocated"
        ? "gastos sin repartir"
        : expenses && product
          ? `equipo ${product} + gastos ${expenses}`
          : "sin gastos de importación";
  return (
    <div
      data-testid="cost-strip"
      aria-label={`Costo y margen de la línea ${n}`}
      aria-busy={stale || undefined}
      className={`mt-2 flex flex-wrap items-center gap-x-5 gap-y-1 rounded-md bg-canvas-sunken px-3 py-1.5 text-[12.5px] text-ink-muted${stale ? " opacity-50" : ""}`}
    >
      <span className="flex min-w-0 items-baseline gap-1.5">
        <span>Me cuesta</span>
        <span className="font-mono font-medium tabular-nums text-ink">{cost ?? "—"}</span>
        <span className="truncate text-[12px] text-ink-faint">{costNote}</span>
      </span>
      <span className="flex items-center gap-1.5">
        {state === "no_price" ? (
          <span className="whitespace-nowrap rounded-full border border-line px-2 py-px text-[12px] font-semibold text-ink-muted">Falta precio</span>
        ) : pct ? (
          <span
            className={`whitespace-nowrap rounded-full border px-2 py-px font-mono text-[12px] font-medium tabular-nums ${PILL[economics.margin_state] ?? PILL_OUTSIDE}`}
          >
            {pct}
          </span>
        ) : state === "expenses_unallocated" ? null : (
          <span className="font-mono text-ink-faint">—</span>
        )}
        {economics.gain_clp != null ? (
          <span>
            gano <span className="font-mono tabular-nums text-ink">{clp(economics.gain_clp)}</span>
          </span>
        ) : null}
      </span>
      <span className="ml-auto flex items-baseline gap-1.5">
        <span>Le cobro (neto)</span>
        <span className="font-mono font-semibold tabular-nums text-brand-700">{sale ?? "—"}</span>
      </span>
    </div>
  );
}
