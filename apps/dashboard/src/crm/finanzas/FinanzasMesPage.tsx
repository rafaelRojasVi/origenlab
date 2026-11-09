/**
 * «Mes» — the five KPI cards for the current Santiago month.
 *
 * Layout: a row of 5 stat tiles (wraps to 2×3 on mobile), each with a label, a headline
 * number, and a secondary stat or progress bar. Monochrome + single teal accent; status
 * colors only for distance-to-meta (good ≥ 80%, warn 50–80%, bad < 50%).
 *
 * Cards (left → right):
 *   1. Vendido   — net invoiced vs. meta, with a progress bar
 *   2. Por cobrar — total balance of unpaid invoices + count
 *   3. Pipeline  — weighted net of open cases + stage breakdown
 *   4. Capital atado — supplier orders in-flight + count
 *   5. Gastos    — operating expenses this month + count
 */
import type { ReactNode } from "react";
import { formatMoney } from "../money";
import { EmptyState, PageHeader, ResourceGate, Skeleton, fmtInt } from "../ui";
import { useResource } from "../useResource";
import type { MesData, PipelineStage } from "./financeApi";
import { fetchFinanceMes } from "./financeApi";

const clp = (n: number) => formatMoney(String(n), "CLP") ?? "—";

// ── KPI tile ──────────────────────────────────────────────────────────────────

interface TileProps {
  label: string;
  headline: ReactNode;
  secondary?: ReactNode;
  bar?: { pct: number; tone: "good" | "warn" | "bad" };
  children?: ReactNode;
}

function Tile({ label, headline, secondary, bar, children }: TileProps) {
  return (
    <article className="flex min-w-0 flex-col gap-2 rounded-lg border border-line bg-surface-raised px-4 py-3">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-muted">{label}</p>
      <p className="text-[22px] font-semibold tabular-nums leading-none text-ink">{headline}</p>
      {secondary ? <p className="text-xs text-ink-muted">{secondary}</p> : null}
      {bar ? (
        <div
          className="relative mt-1 h-1 w-full overflow-hidden rounded-full bg-line"
          role="progressbar"
          aria-valuenow={Math.min(bar.pct, 100)}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div
            className={`h-full rounded-full transition-[width] duration-500 ${
              bar.tone === "good" ? "bg-good" : bar.tone === "warn" ? "bg-warn" : "bg-bad"
            }`}
            style={{ width: `${Math.min(bar.pct, 100)}%` }}
          />
        </div>
      ) : null}
      {children}
    </article>
  );
}

// ── pipeline stage breakdown ──────────────────────────────────────────────────

const STAGE_LABEL: Record<string, string> = {
  lead: "Lead",
  qualifying: "Calificando",
  qualified: "Calificado",
  quoting: "Cotizando",
  negotiating: "Negociando",
};

function PipelineDetail({ by_stage }: { by_stage: Record<string, PipelineStage> }) {
  const stages = Object.entries(by_stage).sort((a, b) => b[1].weighted_clp - a[1].weighted_clp);
  if (stages.length === 0) return null;
  return (
    <table className="mt-1 w-full border-collapse text-right text-xs">
      <thead>
        <tr className="text-ink-faint">
          <th scope="col" className="pb-0.5 text-left font-normal">Etapa</th>
          <th scope="col" className="pb-0.5 font-normal">Casos</th>
          <th scope="col" className="pb-0.5 font-normal">Ponderado</th>
        </tr>
      </thead>
      <tbody>
        {stages.map(([stage, s]) => (
          <tr key={stage} className="border-t border-line/50">
            <td className="py-0.5 text-left text-ink-muted">{STAGE_LABEL[stage] ?? stage}</td>
            <td className="tabular-nums text-ink-muted">{s.count}</td>
            <td className="tabular-nums font-medium text-ink">{clp(s.weighted_clp)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ── mes page ──────────────────────────────────────────────────────────────────

function barTone(pct: number): "good" | "warn" | "bad" {
  if (pct >= 80) return "good";
  if (pct >= 50) return "warn";
  return "bad";
}

function MesTiles({ data }: { data: MesData }) {
  const tone = barTone(data.vendido.pct_meta);

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5">
      {/* 1. Vendido vs meta */}
      <Tile
        label="Vendido"
        headline={clp(data.vendido.net)}
        secondary={`Meta ${clp(data.vendido.meta_net)} · ${data.vendido.pct_meta.toFixed(0)} %`}
        bar={{ pct: data.vendido.pct_meta, tone }}
      />
      {/* 2. Por cobrar */}
      <Tile
        label="Por cobrar"
        headline={clp(data.por_cobrar.total)}
        secondary={`${fmtInt(data.por_cobrar.count)} factura${data.por_cobrar.count === 1 ? "" : "s"} pendiente${data.por_cobrar.count === 1 ? "" : "s"}`}
      />
      {/* 3. Pipeline ponderado — with stage breakdown */}
      <Tile label="Pipeline" headline={clp(data.pipeline.ponderado_net)} secondary="ponderado por etapa">
        <PipelineDetail by_stage={data.pipeline.by_stage} />
      </Tile>
      {/* 4. Capital atado */}
      <Tile
        label="Capital atado"
        headline={clp(data.capital_atado.total)}
        secondary={`${fmtInt(data.capital_atado.count)} pedido${data.capital_atado.count === 1 ? "" : "s"} en tránsito`}
      />
      {/* 5. Gastos */}
      <Tile
        label="Gastos mes"
        headline={clp(data.gastos.total)}
        secondary={`${fmtInt(data.gastos.count)} ítem${data.gastos.count === 1 ? "" : "s"} registrado${data.gastos.count === 1 ? "" : "s"}`}
      />
    </div>
  );
}

export function FinanzasMesPage() {
  const [state, reload] = useResource(fetchFinanceMes);

  const mesLabel =
    state.kind === "ready"
      ? new Date(state.data.mes + "T12:00:00").toLocaleDateString("es-CL", { month: "long", year: "numeric" })
      : null;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader title="Mes" subtitle={mesLabel ?? undefined} />
      <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={2} />} errorTitle="No se pudo leer Finanzas">
        {(data) => <MesTiles data={data} />}
      </ResourceGate>
    </div>
  );
}
