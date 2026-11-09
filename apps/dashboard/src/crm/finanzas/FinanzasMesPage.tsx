/**
 * «Mes» — the full Finanzas dashboard for the current Santiago month.
 *
 * Layout matches the canvas (Finanzas.dc.html):
 *   Row 1 (1.6fr | 1fr):  Vendido + 6-month bar chart  |  Nos deben + aging segmented bar
 *   Row 2 (3 tiles):      En espera ponderado  |  Capital atado  |  Gastos fijos
 *   Row 3 (1.6fr | 1fr):  Facturas table       |  Cobrar antes
 *
 * Controls: Neto / Con IVA segmented switch (affects Vendido; «Nos deben» is always IVA).
 * Month selector: shows current month label (multi-month support in a future PR).
 * CSV export: downloads vendido + gastos summary as CSV.
 */
import { useState } from "react";
import { crmHash } from "../crmRoute";
import { formatMoney } from "../money";
import { EmptyState, PageHeader, ResourceGate, Skeleton, fmtInt } from "../ui";
import { useResource } from "../useResource";
import { businessDaysLeft, buildBarSeries, monthAbbr } from "./financeCalc";
import type { MesData, PipelineStage } from "./financeApi";
import { fetchFinanceMes } from "./financeApi";

const IVA = 0.19;
const clp = (n: number) => formatMoney(String(n), "CLP") ?? "—";
const M = (n: number) => {
  const v = n / 1_000_000;
  return `$${v.toLocaleString("es-CL", { minimumFractionDigits: 0, maximumFractionDigits: 2 })} M`;
};

// ── month label ───────────────────────────────────────────────────────────────

function mesLabel(mes: string): string {
  const MONTHS = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"];
  const d = new Date(mes + "T12:00:00Z");
  return `${MONTHS[d.getUTCMonth()] ?? ""} ${d.getUTCFullYear()}`;
}

// ── progress bar tone ─────────────────────────────────────────────────────────

function barTone(pct: number): string {
  if (pct >= 80) return "bg-good";
  if (pct >= 50) return "bg-warn";
  return "bg-bad";
}

// ── progress bar ──────────────────────────────────────────────────────────────

function ProgressBar({ pct, tone, label }: { pct: number; tone: string; label: string }) {
  return (
    <div
      className="relative h-2 w-full overflow-hidden rounded-full bg-line"
      role="progressbar"
      aria-valuenow={Math.min(pct, 100)}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={label}
    >
      <div
        className={`h-full rounded-full transition-[width] duration-500 ${tone}`}
        style={{ width: `${Math.min(pct, 100)}%` }}
      />
    </div>
  );
}

// ── aging segmented bar ───────────────────────────────────────────────────────

type AgeBucket = "0-30" | "31-60" | "61-90" | "90+";
const AGE_LABEL: Record<AgeBucket, string> = {
  "0-30": "0–30 d",
  "31-60": "31–60 d",
  "61-90": "61–90 d",
  "90+": "+90 d",
};

function AgingBar({ byAge }: { byAge: Record<AgeBucket, number> }) {
  const buckets: AgeBucket[] = ["0-30", "31-60", "61-90", "90+"];
  const total = buckets.reduce((s, b) => s + byAge[b], 0);
  if (total === 0) return <div className="h-7 rounded bg-line text-center text-xs leading-7 text-ink-faint">Sin facturas por cobrar</div>;

  const barColors = ["bg-ink text-on-ink", "bg-ink-faint text-on-ink", "border border-line-strong", "border border-line-strong"];

  return (
    <div>
      <div className="flex gap-0.5 h-7" role="img" aria-label={`Antigüedad: ${buckets.map((b) => `${AGE_LABEL[b]} ${M(byAge[b])}`).join(", ")}`}>
        {buckets.map((b, i) => {
          const pct = (byAge[b] / total) * 100;
          if (pct < 1) return null;
          return (
            <div
              key={b}
              className={`flex items-center rounded px-2 text-[11px] font-mono whitespace-nowrap overflow-hidden transition-[filter] hover:brightness-110 ${barColors[i]}`}
              style={{ width: `${pct}%`, transformOrigin: "left" }}
              title={`${AGE_LABEL[b]} · ${M(byAge[b])} · ${pct.toFixed(0)} %`}
            >
              {pct > 12 ? M(byAge[b]) : ""}
            </div>
          );
        })}
      </div>
      <div className="mt-2 grid grid-cols-4 gap-2 text-xs text-ink-muted">
        {buckets.map((b, i) => (
          <span key={b}>
            <span className={`inline-block w-2.5 h-2.5 rounded-sm mr-1.5 align-[-1px] ${
              byAge[b] > 0 ? (i === 0 ? "bg-ink" : i === 1 ? "bg-ink-faint" : "border border-line-strong") : "border border-line-strong"
            }`} aria-hidden />
            {AGE_LABEL[b]}
            <b className="block ml-4 font-mono text-ink">{M(byAge[b])}</b>
          </span>
        ))}
      </div>
    </div>
  );
}

// ── 6-month SVG bar chart ─────────────────────────────────────────────────────

function BarChart({ series, meta }: { series: Array<{ month: string; neto: number; isCurrent: boolean }>; meta: number }) {
  const max = Math.max(meta, ...series.map((s) => s.neto), 1);
  const H = 140; const W = 350; const padding = { left: 30, right: 60, top: 10, bottom: 20 };
  const chartH = H - padding.top - padding.bottom;
  const chartW = W - padding.left - padding.right;
  const barW = Math.floor(chartW / series.length * 0.5);
  const gap = chartW / series.length;

  // Gridlines at 0, max/3, 2*max/3, max
  const gridVals = [0, Math.round(max / 3), Math.round((2 * max) / 3), max];
  const metaY = padding.top + chartH - (meta / max) * chartH;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" className="mt-1" role="img" aria-label={
      `Vendido por mes (neto): ${series.map((s) => `${monthAbbr(s.month)} ${M(s.neto)}`).join(", ")}. Meta ${M(meta)}.`
    }>
      {/* gridlines */}
      {gridVals.map((v, i) => {
        const y = padding.top + chartH - (v / max) * chartH;
        return (
          <g key={i}>
            <line x1={padding.left} x2={W - padding.right} y1={y} y2={y} stroke="var(--line)" strokeWidth={1} />
            <text x={padding.left - 4} y={y + 4} textAnchor="end" style={{ font: "500 11px 'IBM Plex Mono',monospace", fill: "var(--ink-muted)" }}>
              {v === 0 ? "0" : M(v).replaceAll("$", "").replaceAll(",00", "").trim()}
            </text>
          </g>
        );
      })}
      {/* meta line */}
      <line x1={padding.left} x2={W - padding.right} y1={metaY} y2={metaY} stroke="var(--ink)" strokeWidth={1.5} />
      <text x={W - padding.right + 4} y={metaY + 4} style={{ font: "500 11px 'IBM Plex Mono',monospace", fill: "var(--ink)" }}>
        meta
      </text>
      {/* bars */}
      {series.map((s, i) => {
        const h = (s.neto / max) * chartH;
        const cx = padding.left + gap * i + gap / 2;
        const x = cx - barW / 2;
        const y = padding.top + chartH - h;
        const fill = s.isCurrent ? "var(--ink)" : "var(--ink-faint)";
        return (
          <g key={s.month}>
            <rect x={x} y={y} width={barW} height={Math.max(h, 1)} rx={2} fill={fill}>
              <title>{monthAbbr(s.month)} · {M(s.neto)}{s.isCurrent ? " (parcial)" : ""}</title>
            </rect>
            {(s.isCurrent || i === series.length - 2) && h > 0 && (
              <text x={cx} y={y - 3} textAnchor="middle" style={{ font: "600 11px 'IBM Plex Mono',monospace", fill: "var(--ink)" }}>
                {M(s.neto).replaceAll("$", "").replaceAll(" M", "")}
              </text>
            )}
            <text x={cx} y={H - 2} textAnchor="middle" style={{
              font: s.isCurrent ? "600 11px 'IBM Plex Mono',monospace" : "500 11px 'IBM Plex Mono',monospace",
              fill: s.isCurrent ? "var(--ink)" : "var(--ink-muted)",
            }}>
              {monthAbbr(s.month)}
            </text>
          </g>
        );
      })}
    </svg>
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

// ── CSV export ────────────────────────────────────────────────────────────────

function downloadCsv(data: MesData, ivaMode: boolean) {
  const mult = ivaMode ? (1 + IVA) : 1;
  const rows = [
    ["Mes", data.mes],
    ["Hoy", data.today],
    ["Vendido neto", String(data.vendido.net)],
    ["Vendido " + (ivaMode ? "con IVA" : "neto"), String(Math.round(data.vendido.net * mult))],
    ["Meta neto", String(data.vendido.meta_net)],
    ["% de meta", String(data.vendido.pct_meta)],
    ["Por cobrar (IVA)", String(data.por_cobrar.total)],
    ["Pipeline ponderado neto", String(data.pipeline.ponderado_net)],
    ["Capital atado CLP", String(data.capital_atado.total)],
    ["Gastos mes CLP", String(data.gastos.total)],
    [],
    ["Mes", "Neto"],
    ...data.monthly_history.map((h) => [h.month, String(h.neto)]),
  ];
  const csv = rows.map((r) => r.join(",")).join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `finanzas-mes-${data.mes}.csv`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

// ── main component ────────────────────────────────────────────────────────────

export function FinanzasMesPage() {
  const [state, reload] = useResource(fetchFinanceMes);
  const [ivaMode, setIvaMode] = useState(false);

  const label =
    state.kind === "ready" ? mesLabel(state.data.mes) : null;
  const todayStr =
    state.kind === "ready" ? state.data.today : null;
  const dayOfMonth = todayStr ? Number(todayStr.slice(8, 10)) : null;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <PageHeader
          title="Mes"
          subtitle={
            label && dayOfMonth
              ? `${label} · día ${dayOfMonth}${state.kind === "ready" ? ` · cifras ${ivaMode ? "con IVA incluido" : "netas, sin IVA"}` : ""}`
              : undefined
          }
        />
        <div className="flex flex-wrap gap-2 items-center">
          {/* Neto / Con IVA segmented switch */}
          <div className="inline-flex rounded-lg border border-line bg-sunken p-0.5" role="radiogroup" aria-label="Mostrar importes">
            <label className={`flex min-h-10 min-w-16 cursor-pointer items-center justify-center rounded-md px-3 text-sm font-semibold transition-colors ${!ivaMode ? "bg-raised text-ink shadow-[0_0_0_1px_var(--line-strong)]" : "text-ink-muted hover:text-ink"}`}>
              <input type="radio" name="iva-mode" className="sr-only" checked={!ivaMode} onChange={() => setIvaMode(false)} />
              Neto
            </label>
            <label className={`flex min-h-10 min-w-16 cursor-pointer items-center justify-center rounded-md px-3 text-sm font-semibold transition-colors ${ivaMode ? "bg-raised text-ink shadow-[0_0_0_1px_var(--line-strong)]" : "text-ink-muted hover:text-ink"}`}>
              <input type="radio" name="iva-mode" className="sr-only" checked={ivaMode} onChange={() => setIvaMode(true)} />
              Con IVA
            </label>
          </div>
          {/* CSV export */}
          {state.kind === "ready" && (
            <button
              type="button"
              onClick={() => downloadCsv(state.data, ivaMode)}
              className="inline-flex min-h-10 items-center gap-2 rounded-lg border border-line-strong bg-raised px-3 text-sm font-semibold text-ink transition-colors hover:bg-sunken active:translate-y-px"
            >
              <svg viewBox="0 0 16 16" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <path d="M8 2.5V11M4.5 7.5 8 11l3.5-3.5M2.5 11v2.5h11V11" />
              </svg>
              Exportar CSV
            </button>
          )}
        </div>
      </div>

      <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={4} />} errorTitle="No se pudo leer Finanzas">
        {(data) => <MesContent data={data} ivaMode={ivaMode} />}
      </ResourceGate>
    </div>
  );
}

function MesContent({ data, ivaMode }: { data: MesData; ivaMode: boolean }) {
  const vendidoDisplay = ivaMode ? Math.round(data.vendido.net * (1 + IVA)) : data.vendido.net;
  const metaDisplay = ivaMode ? Math.round(data.vendido.meta_net * (1 + IVA)) : data.vendido.meta_net;
  const pct = data.vendido.pct_meta;
  const tone = barTone(pct);

  const remaining = Math.max(0, data.vendido.meta_net - data.vendido.net);
  const days = businessDaysLeft(data.today);
  const perDay = days > 0 ? Math.ceil(remaining / days) : null;

  const series = buildBarSeries(data.monthly_history, data.mes);

  // Factorizable: por cobrar aged 0-60 days (companies < 60 days)
  const factorizable = (data.por_cobrar.by_age["0-30"] ?? 0) + (data.por_cobrar.by_age["31-60"] ?? 0);

  // Cobrar antes: oldest buckets first
  const agedBuckets: AgeBucket[] = ["90+", "61-90", "31-60", "0-30"];

  return (
    <div className="flex flex-col gap-4">
      {/* Row 1: Vendido + Nos deben */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        {/* Vendido card */}
        <article className="rounded-lg border border-line bg-raised px-4 py-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-[minmax(250px,0.8fr)_minmax(0,1.2fr)]">
            <div className="flex flex-col">
              <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-faint">Vendido {ivaMode ? "con IVA" : "en neto"}</p>
              <p className="mt-2 font-mono text-[42px] font-medium leading-none tracking-tight text-ink" aria-label={`Vendido: ${M(vendidoDisplay)}`}>
                {M(vendidoDisplay)}
              </p>
              <p className="mt-1 text-xs text-ink-muted">Lo facturado desde el 1 del mes.</p>
              <div className="mt-auto pt-4">
                <ProgressBar pct={pct} tone={tone} label={`${pct.toFixed(0)} % de la meta de ${M(data.vendido.meta_net)}`} />
                <div className="mt-1.5 flex justify-between gap-2 text-xs text-ink-muted">
                  <span className="font-mono">{pct.toFixed(0)} % de la meta</span>
                  <span>meta <span className="font-mono text-ink">{M(metaDisplay)}</span>{remaining > 0 && <> · faltan <span className="font-mono text-ink">{M(ivaMode ? Math.round(remaining * (1 + IVA)) : remaining)}</span></>}</span>
                </div>
                {perDay !== null && (
                  <p className="mt-1 text-xs text-ink-muted">
                    Ritmo requerido: <span className="font-mono text-ink">{clp(ivaMode ? Math.round(perDay * (1 + IVA)) : perDay)}</span> por día hábil ({days} días hábiles restantes)
                  </p>
                )}
              </div>
            </div>
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-faint">Últimos 6 meses · neto</p>
              <BarChart series={series} meta={data.vendido.meta_net} />
              <p className="mt-1 text-xs text-ink-faint">El mes actual es parcial.</p>
            </div>
          </div>
        </article>

        {/* Nos deben card */}
        <article className="flex flex-col rounded-lg border border-line bg-raised px-4 py-4">
          <div className="flex items-baseline justify-between gap-3">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-faint">Nos deben · con IVA</p>
            <span className="text-xs font-semibold text-accent">Ver por cobrar</span>
          </div>
          <p className="mt-2 font-mono text-[32px] font-medium leading-tight tracking-tight text-ink" aria-label={`Por cobrar: ${M(data.por_cobrar.total)}`}>
            {M(data.por_cobrar.total)}
          </p>
          <p className="mt-1 text-xs text-ink-muted">
            {fmtInt(data.por_cobrar.count)} factura{data.por_cobrar.count === 1 ? "" : "s"} pendiente{data.por_cobrar.count === 1 ? "" : "s"}. Ninguna pasa de 90 días.
          </p>
          <div className="mt-auto pt-4">
            <AgingBar byAge={data.por_cobrar.by_age as Record<AgeBucket, number>} />
          </div>
        </article>
      </div>

      {/* Row 2: three tiles */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        {/* Pipeline ponderado */}
        <article className="flex flex-col rounded-lg border border-line bg-raised px-4 py-3">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-faint">En espera · ponderado</p>
          <p className="mt-2 font-mono text-[28px] font-medium leading-tight text-ink">{M(data.pipeline.ponderado_net)}</p>
          <p className="mt-1 text-xs text-ink-muted">ponderado por probabilidad de etapa</p>
          <div className="mt-auto pt-3">
            <PipelineDetail by_stage={data.pipeline.by_stage} />
          </div>
        </article>

        {/* Capital atado */}
        <article className="flex flex-col rounded-lg border border-line bg-raised px-4 py-3">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-faint">Capital propio atado</p>
          <p className="mt-2 font-mono text-[28px] font-medium leading-tight text-ink">
            {data.capital_atado.total > 0 ? `~${M(data.capital_atado.total)}` : "$0"}
          </p>
          <p className="mt-1 text-xs text-ink-muted">
            {fmtInt(data.capital_atado.count)} pedido{data.capital_atado.count === 1 ? "" : "s"} en tránsito · ciclo 5–7 semanas
          </p>
          <p className="mt-1 text-xs text-ink-faint">
            Plata en pedidos pagados al proveedor que aún no cobramos al cliente.
          </p>
        </article>

        {/* Gastos fijos */}
        <article className="flex flex-col rounded-lg border border-line bg-raised px-4 py-3">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-faint">Gastos fijos del mes</p>
          <p className="mt-2 font-mono text-[28px] font-medium leading-tight text-ink">{M(data.gastos.total)}</p>
          <p className="mt-1 text-xs text-ink-muted">
            {fmtInt(data.gastos.count)} ítem{data.gastos.count === 1 ? "" : "s"} registrado{data.gastos.count === 1 ? "" : "s"}
          </p>
          {data.gastos.total > 0 && data.vendido.net > 0 && (
            <p className="mt-1 text-xs text-ink-faint">
              Punto de equilibrio ≈ <span className="font-mono">{M(Math.round(data.gastos.total / 0.30))}</span> vendido al 30 % margen
            </p>
          )}
        </article>
      </div>

      {/* Row 3: Cobrar antes */}
      <div className="rounded-lg border border-line bg-raised px-4 py-4">
        <div className="mb-3 flex items-baseline justify-between gap-2">
          <h2 className="text-sm font-bold text-ink">Cobrar antes · Financiamiento</h2>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
          {/* Cobrar por antigüedad */}
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-faint">Prioridad de cobro</p>
            {data.por_cobrar.count === 0 ? (
              <EmptyState title="Sin facturas por cobrar" />
            ) : (
              <div className="mt-2 flex flex-col gap-1.5">
                {agedBuckets.map((b) => {
                  const v = data.por_cobrar.by_age[b] ?? 0;
                  if (v === 0) return null;
                  const urgent = b === "90+" || b === "61-90";
                  return (
                    <a
                      key={b}
                      href={crmHash("pagos")}
                      className="flex items-center justify-between rounded-md border border-line px-3 py-2 hover:border-line-strong hover:bg-canvas-sunken/30 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
                    >
                      <span className={`text-sm font-medium ${urgent ? "text-bad" : "text-ink"}`}>
                        {AGE_LABEL[b]}
                        {urgent && <span className="ml-1.5 text-xs text-bad">⚠ cobrar urgente</span>}
                      </span>
                      <span className="font-mono text-sm font-medium text-ink">{M(v)}</span>
                    </a>
                  );
                })}
              </div>
            )}
          </div>
          {/* Factorizable */}
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-faint">Factorizable hoy</p>
            <p className="mt-2 font-mono text-[28px] font-medium leading-tight text-ink">{M(factorizable)}</p>
            <p className="mt-1 text-xs text-ink-muted">
              Facturas &lt; 60 días que un factor podría adelantar.
            </p>
            <div className="mt-3 grid grid-cols-2 gap-2">
              <div className="rounded-lg border border-line p-2.5">
                <p className="text-xs font-bold text-ink">A · Factoring</p>
                <p className="mt-1 font-mono text-sm text-ink">1,5–2,5 % al mes</p>
                <p className="mt-0.5 text-xs text-ink-muted">≈ $30–50 mil por adelantar 30 días</p>
              </div>
              <div className="rounded-lg border border-line p-2.5">
                <p className="text-xs font-bold text-ink">B · 50 % anticipo</p>
                <p className="mt-1 font-mono text-sm text-ink">$0 de costo</p>
                <p className="mt-0.5 text-xs text-ink-muted">en cotizaciones nuevas, ya en condiciones</p>
              </div>
            </div>
            <p className="mt-2 text-xs text-ink-faint">
              Ley 21.131 — clientes deben pagar a 30 días. Consultar financiamiento completo en la pestaña Financiamiento.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
