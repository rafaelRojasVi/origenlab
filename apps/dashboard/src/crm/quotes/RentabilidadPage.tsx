/**
 * «Rentabilidad» (`#/crm/cotizador/rentabilidad`, a fixed pane of the Cotizador module): for every
 * quote made in the Cotizador, where the money goes — the sale (neto), the product cost, the quote's
 * own import costs («gastos»: freight, customs agent, duties and customs, local transport) and the
 * gross margin. Company overhead is not included (owner, 2026-10-17). Quotes from before the
 * Cotizador are not included either: their import costs were never recorded.
 *
 * A period (this year by default; this month, the last twelve months or a range of months), a
 * summary strip with the coverage note, a table by month and the quotes, newest first. A row opens
 * its import-cost split and «Abrir cotización» (the quote's tab, `onOpen`).
 *
 * The margin is the API's and only for quotes whose every line has a cost; the totals' margin is
 * over those quotes alone, and the note says how much of the sale that is — a margin is never
 * shown as if it covered what it does not. Drafts are listed (marked) but not added to the totals.
 * Every number is the API's (`GET /v2/quotes/economics`), only formatted here.
 * Sales and administration only: the module refuses anyone else, and so does the API.
 */
import { useId, useState, type FormEvent } from "react";
import { fmtDay } from "../catalog/catalogApi";
import { formatMoney } from "../money";
import { Badge, Button, EmptyState, ResourceGate, Skeleton } from "../ui";
import { useResource } from "../useResource";
import { ImportSplit } from "./EconomicsCard";
import { coverageText, marginStateText } from "./economicsText";
import { fetchQuoteEconomics, type EconomicsReport, type EconomicsRow, type EconomicsTotals } from "./quoteApi";
import { quoteLabel } from "./quoteLabel";
import { formatPct } from "./quoteFormat";
import { BAND_INVERTED, targetBand } from "./SummaryRail";
import type { TabMeta } from "./tabStore";

export const SCOPE_NOTE = "Desde que se cotiza en el sistema; las cotizaciones anteriores no se incluyen.";

type Load = (period: { from: string; to: string }) => Promise<EconomicsReport>;
type Preset = "year" | "month" | "last12" | "custom";
type Period = { from: string; to: string };

const PRESETS: { value: Preset; label: string }[] = [
  { value: "year", label: "Este año" },
  { value: "month", label: "Este mes" },
  { value: "last12", label: "Últimos 12 meses" },
  { value: "custom", label: "Otro período" },
];

/** The longest period the API reads at once (`economics.MAX_MONTHS`). */
const MAX_MONTHS = 36;

const STATUS: Record<EconomicsRow["status"], string> = { draft: "Borrador", approved: "Aprobada", sent: "Enviada" };

/** This month in Santiago, `YYYY-MM`. */
function thisMonth(now: Date): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/Santiago", year: "numeric", month: "2-digit" }).format(now).slice(0, 7);
}

/** `YYYY-MM` moved `n` months (calendar arithmetic on the month, no money). */
function addMonths(month: string, n: number): string {
  const total = Number(month.slice(0, 4)) * 12 + Number(month.slice(5, 7)) - 1 + n;
  return `${Math.floor(total / 12)}-${String((total % 12) + 1).padStart(2, "0")}`;
}

export function presetPeriod(preset: Exclude<Preset, "custom">, now: Date): Period {
  const month = thisMonth(now);
  if (preset === "month") return { from: month, to: month };
  if (preset === "last12") return { from: addMonths(month, -11), to: month };
  return { from: `${month.slice(0, 4)}-01`, to: `${month.slice(0, 4)}-12` };
}

/** «octubre de 2026» from "2026-10". */
export function monthName(month: string): string {
  const d = new Date(Number(month.slice(0, 4)), Number(month.slice(5, 7)) - 1, 1);
  return d.toLocaleDateString("es-CL", { month: "long", year: "numeric" });
}

const clp = (v: string | null | undefined) => formatMoney(v, "CLP") ?? "—";

export function RentabilidadPage({
  onOpen,
  load = fetchQuoteEconomics,
  now = () => new Date(),
}: {
  onOpen: (quoteId: string, meta: TabMeta) => void;
  load?: Load;
  now?: () => Date;
}) {
  const [preset, setPreset] = useState<Preset>("year");
  const [period, setPeriod] = useState<Period>(() => presetPeriod("year", now()));
  const [state, reload, refreshing] = useResource(() => load(period), [period.from, period.to]);
  const choose = (p: Preset) => {
    setPreset(p);
    if (p !== "custom") setPeriod(presetPeriod(p, now()));
  };
  return (
    <div className="space-y-[18px]">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="text-2xl font-bold tracking-tight text-ink">Rentabilidad</h1>
        <span className="text-[13px] text-ink-muted">
          venta, costo de productos, gastos de importación y margen bruto de cada cotización
          {refreshing && state.kind === "ready" ? " · actualizando…" : ""}
        </span>
      </div>
      <PeriodFilter preset={preset} period={period} onPreset={choose} onCustom={setPeriod} />
      <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={6} />}>
        {(report) => <Report report={report} onOpen={onOpen} />}
      </ResourceGate>
    </div>
  );
}

/* ── the period ───────────────────────────────────────────────────────────── */

function PeriodFilter({
  preset,
  period,
  onPreset,
  onCustom,
}: {
  preset: Preset;
  period: Period;
  onPreset: (p: Preset) => void;
  onCustom: (p: Period) => void;
}) {
  const id = useId();
  const [from, setFrom] = useState(period.from);
  const [to, setTo] = useState(period.to);
  const [problem, setProblem] = useState<string | null>(null);
  const apply = (e: FormEvent) => {
    e.preventDefault();
    if (!/^\d{4}-\d{2}$/.test(from) || !/^\d{4}-\d{2}$/.test(to)) {
      setProblem("Elige el mes de inicio y el de término.");
      return;
    }
    if (to < from) {
      setProblem("El mes de término no puede ser anterior al de inicio.");
      return;
    }
    if (addMonths(from, MAX_MONTHS - 1) < to) {
      setProblem(`Elige como máximo ${MAX_MONTHS} meses.`);
      return;
    }
    setProblem(null);
    onCustom({ from, to });
  };
  return (
    <div className="flex flex-wrap items-end gap-2.5">
      <div role="group" aria-label="Período" className="inline-flex overflow-hidden rounded-[10px] border border-line-strong bg-canvas-raised">
        {PRESETS.map((p, i) => {
          const on = p.value === preset;
          return (
            <button
              key={p.value}
              type="button"
              aria-pressed={on}
              onClick={() => {
                if (p.value === "custom") {
                  setFrom(period.from);
                  setTo(period.to);
                }
                onPreset(p.value);
              }}
              className={`inline-flex h-11 items-center px-3.5 text-[13.5px] focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-600 ${
                i > 0 ? "border-l border-line-strong" : ""
              } ${on ? "bg-brand-700 font-semibold text-white" : "text-ink hover:bg-canvas-sunken"}`}
            >
              {p.label}
            </button>
          );
        })}
      </div>
      {preset === "custom" ? (
        <form onSubmit={apply} className="flex flex-wrap items-end gap-2" aria-label="Elegir meses">
          <span className="flex flex-col gap-1">
            <label htmlFor={`${id}-from`} className="text-xs font-medium text-ink">
              Desde
            </label>
            <input
              id={`${id}-from`}
              type="month"
              value={from}
              onChange={(e) => setFrom(e.target.value)}
              aria-invalid={problem ? true : undefined}
              className="h-11 rounded-lg border border-line-strong bg-canvas-raised px-3 text-[14px] text-ink"
            />
          </span>
          <span className="flex flex-col gap-1">
            <label htmlFor={`${id}-to`} className="text-xs font-medium text-ink">
              Hasta
            </label>
            <input
              id={`${id}-to`}
              type="month"
              value={to}
              onChange={(e) => setTo(e.target.value)}
              aria-invalid={problem ? true : undefined}
              className="h-11 rounded-lg border border-line-strong bg-canvas-raised px-3 text-[14px] text-ink"
            />
          </span>
          <Button type="submit" variant="primary" className="min-h-[44px] px-4 text-[14px] font-semibold">
            Ver período
          </Button>
          {problem ? (
            <p role="alert" className="w-full text-xs text-bad">
              {problem}
            </p>
          ) : null}
        </form>
      ) : (
        <span className="pb-3 text-[13px] text-ink-muted">
          {period.from === period.to ? monthName(period.from) : `${monthName(period.from)} a ${monthName(period.to)}`}
        </span>
      )}
    </div>
  );
}

/* ── the report ───────────────────────────────────────────────────────────── */

function Report({ report, onOpen }: { report: EconomicsReport; onOpen: (quoteId: string, meta: TabMeta) => void }) {
  const band = targetBand(report.params);
  const bandText = band.inverted ? BAND_INVERTED : `Objetivo de margen ${band.minText}–${band.maxText} % (Preferencias).`;
  return (
    <>
      <Summary totals={report.totals} />
      {report.rows.length === 0 ? (
        <EmptyState title="No hay cotizaciones en este período.">{SCOPE_NOTE}</EmptyState>
      ) : (
        <>
          {report.months.length > 0 ? <MonthsTable months={report.months} /> : null}
          <QuotesTable rows={report.rows} bandText={bandText} onOpen={onOpen} />
        </>
      )}
    </>
  );
}

const TILE = "flex min-w-[150px] flex-1 flex-col gap-1 rounded-[14px] border border-line bg-canvas-raised px-4 py-3";

/** The note under the totals: how much of the sale the margin covers, what it leaves out, and the drafts. */
export function coverageNote(t: EconomicsReport["totals"]): string[] {
  const out: string[] = [];
  if (t.quotes === 0) out.push("No hay cotizaciones aprobadas ni enviadas en este período.");
  else {
    const c = t.coverage;
    const n = c.full.quotes;
    if (n === t.quotes) out.push(`El margen cubre las ${n === 1 ? "única cotización" : `${n} cotizaciones`}: todas tienen el costo de cada línea.`);
    else {
      const share = formatPct(c.full_share_of_sale) ?? "0,0 %";
      out.push(
        `El margen se calcula sólo sobre ${n} de ${t.quotes} cotizaciones, las que tienen el costo de cada línea: ${clp(c.full.sale_net_clp)} de ${clp(t.sale_net_clp)} en ventas (${share}).`,
      );
      const left = c.partial.quotes + c.none.quotes;
      out.push(`${left} ${left === 1 ? "cotización tiene" : "cotizaciones tienen"} líneas sin costo y no ${left === 1 ? "entra" : "entran"} en el margen.`);
    }
  }
  if (t.drafts.quotes) {
    const one = t.drafts.quotes === 1;
    out.push(`${t.drafts.quotes} ${one ? "borrador" : "borradores"} (${clp(t.drafts.sale_net_clp)}) ${one ? "se lista pero no se suma" : "se listan pero no se suman"}.`);
  }
  out.push(SCOPE_NOTE);
  return out;
}

function Summary({ totals }: { totals: EconomicsReport["totals"] }) {
  const pct = formatPct(totals.gross_margin_pct);
  return (
    <section aria-label="Resumen del período" className="space-y-2">
      <div className="flex flex-wrap gap-2.5">
        <Tile label="Ventas netas" value={clp(totals.sale_net_clp)} />
        <Tile label="Costo productos" value={clp(totals.product_cost_clp)} />
        <Tile label="Gastos de importación" value={clp(totals.import_costs_clp)} />
        <Tile
          label="Margen bruto"
          value={totals.gross_margin_clp == null ? "—" : clp(totals.gross_margin_clp)}
          extra={pct ? `${pct} de la venta con costo completo` : "sin cotizaciones con costo completo"}
        />
      </div>
      <ul aria-label="Cobertura del margen" className="space-y-0.5 text-[12.5px] text-ink-muted">
        {coverageNote(totals).map((t) => (
          <li key={t}>{t}</li>
        ))}
      </ul>
    </section>
  );
}

function Tile({ label, value, extra }: { label: string; value: string; extra?: string }) {
  return (
    <div className={TILE}>
      <span className="text-[12.5px] text-ink-muted">{label}</span>
      <span className="font-mono text-[20px] font-semibold tabular-nums text-ink">{value}</span>
      {extra ? <span className="text-[12px] text-ink-muted">{extra}</span> : null}
    </div>
  );
}

const TH = "px-3 py-2 text-left text-[12px] font-semibold text-ink-muted";
const TH_NUM = `${TH} text-right`;
const TD = "px-3 py-2 align-top";
const TD_NUM = `${TD} text-right font-mono tabular-nums`;

function MonthsTable({ months }: { months: EconomicsReport["months"] }) {
  return (
    <section aria-labelledby="rentabilidad-meses" className="overflow-x-auto rounded-[14px] border border-line bg-canvas-raised">
      <h2 id="rentabilidad-meses" className="px-3 pt-3 text-[15px] font-semibold text-ink">
        Por mes
      </h2>
      <table className="w-full min-w-[720px] text-[13.5px]">
        <thead>
          <tr>
            <th className={TH}>Mes</th>
            <th className={TH_NUM}>Cotizaciones</th>
            <th className={TH_NUM}>Venta neta</th>
            <th className={TH_NUM}>Costo</th>
            <th className={TH_NUM}>Gastos</th>
            <th className={TH_NUM}>Margen $</th>
            <th className={TH_NUM}>Margen %</th>
          </tr>
        </thead>
        <tbody>
          {months.map((m) => (
            <MonthRow key={m.month} m={m} />
          ))}
        </tbody>
      </table>
    </section>
  );
}

function MonthRow({ m }: { m: EconomicsTotals & { month: string } }) {
  const partial = m.coverage.full.quotes < m.quotes;
  return (
    <tr className="border-t border-line">
      <td className={`${TD} capitalize`}>{monthName(m.month)}</td>
      <td className={TD_NUM}>{m.quotes}</td>
      <td className={TD_NUM}>{clp(m.sale_net_clp)}</td>
      <td className={TD_NUM}>{clp(m.product_cost_clp)}</td>
      <td className={TD_NUM}>{clp(m.import_costs_clp)}</td>
      <td className={TD_NUM}>{m.gross_margin_clp == null ? "—" : clp(m.gross_margin_clp)}</td>
      <td className={TD_NUM}>
        {formatPct(m.gross_margin_pct) ?? "—"}
        {partial ? (
          <span className="block text-[11.5px] font-sans text-ink-muted">
            sobre {m.coverage.full.quotes} de {m.quotes}
          </span>
        ) : null}
      </td>
    </tr>
  );
}

function QuotesTable({
  rows,
  bandText,
  onOpen,
}: {
  rows: EconomicsRow[];
  bandText: string;
  onOpen: (quoteId: string, meta: TabMeta) => void;
}) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <section aria-labelledby="rentabilidad-cotizaciones" className="overflow-x-auto rounded-[14px] border border-line bg-canvas-raised">
      <div className="flex flex-wrap items-baseline justify-between gap-2 px-3 pt-3">
        <h2 id="rentabilidad-cotizaciones" className="text-[15px] font-semibold text-ink">
          Cotizaciones
        </h2>
        <span className="text-[12px] text-ink-muted">{bandText}</span>
      </div>
      <table className="w-full min-w-[980px] text-[13.5px]">
        <thead>
          <tr>
            <th className={TH}>Número</th>
            <th className={TH}>Cliente</th>
            <th className={TH}>Fecha</th>
            <th className={TH_NUM}>Venta neta</th>
            <th className={TH_NUM}>Costo</th>
            <th className={TH_NUM}>Gastos</th>
            <th className={TH_NUM}>Margen $</th>
            <th className={TH_NUM}>Margen %</th>
            <th className={TH}>Cobertura</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <QuoteRows
              key={r.quote_id}
              r={r}
              expanded={open === r.quote_id}
              onToggle={() => setOpen((o) => (o === r.quote_id ? null : r.quote_id))}
              onOpen={onOpen}
            />
          ))}
        </tbody>
      </table>
    </section>
  );
}

function QuoteRows({
  r,
  expanded,
  onToggle,
  onOpen,
}: {
  r: EconomicsRow;
  expanded: boolean;
  onToggle: () => void;
  onOpen: (quoteId: string, meta: TabMeta) => void;
}) {
  const label = quoteLabel({ label: r.label, quote_number: r.quote_number });
  const e = r.economics;
  const st = marginStateText(e?.margin_state);
  const pct = formatPct(e?.gross_margin_pct);
  const detailId = `rentabilidad-detalle-${r.quote_id}`;
  return (
    <>
      <tr className={`border-t border-line ${expanded ? "bg-canvas-sunken/60" : "hover:bg-canvas-sunken/40"}`} onClick={onToggle}>
        <td className={TD}>
          <button
            type="button"
            aria-expanded={expanded}
            aria-controls={detailId}
            onClick={(ev) => {
              ev.stopPropagation();
              onToggle();
            }}
            className="inline-flex min-h-[44px] items-center gap-1.5 rounded-md font-mono font-medium tabular-nums text-brand-700 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
          >
            <span aria-hidden="true">{expanded ? "▾" : "▸"}</span>
            {label}
            <span className="sr-only">, ver gastos</span>
          </button>
          {r.draft ? (
            <span className="ml-1.5">
              <Badge tone="warn">Borrador</Badge>
            </span>
          ) : null}
        </td>
        <td className={`${TD} max-w-[220px] truncate`}>{r.client ?? "—"}</td>
        <td className={`${TD} whitespace-nowrap tabular-nums`}>{fmtDay(r.date)}</td>
        <td className={TD_NUM}>{e ? clp(e.sale_net_clp) : "—"}</td>
        <td className={TD_NUM}>{e ? clp(e.product_cost_clp) : "—"}</td>
        <td className={TD_NUM}>{e ? clp(e.import_costs_clp) : "—"}</td>
        <td className={TD_NUM}>{e?.gross_margin_clp == null ? "—" : clp(e.gross_margin_clp)}</td>
        <td className={`${TD} text-right`}>
          {pct ? (
            <span className={`font-semibold tabular-nums ${st.cls}`}>
              {pct}
              <span className="block text-[11.5px] font-medium">{st.label}</span>
            </span>
          ) : (
            <span className="text-ink-muted">—</span>
          )}
        </td>
        <td className={TD}>{e ? coverageText(e.cost_coverage) : "No se pudo calcular"}</td>
      </tr>
      {expanded ? (
        <tr id={detailId} className="bg-canvas-sunken/60">
          <td colSpan={9} className="px-3 pb-3 pt-1">
            <div className="flex flex-wrap items-end justify-between gap-3">
              {e ? <ImportSplit economics={e} /> : <p className="text-[13px] text-ink-muted">Esta cotización no se pudo calcular.</p>}
              <Button
                className="min-h-[44px] px-4 text-[14px] font-semibold"
                onClick={(ev) => {
                  ev.stopPropagation();
                  onOpen(r.quote_id, { label, client: r.client, status: r.status });
                }}
              >
                Abrir cotización
              </Button>
            </div>
            <p className="mt-1 text-[12px] text-ink-muted">{STATUS[r.status]}</p>
          </td>
        </tr>
      ) : null}
    </>
  );
}
