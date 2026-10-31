import { useState } from "react";
import type { CampaignSummary } from "../crmTypes";
import { fmtInt } from "../ui";
import {
  COLUMN_LABEL,
  OVERVIEW_LABEL,
  SEGMENT_LABEL,
  pct,
  type CoverageColumn,
  type CoverageRow,
  type MarketingAnalytics as Analytics,
  type OverviewMetric,
  type PeopleQuery,
  type Segment,
} from "./analytics";
import { CountUp, Donut, ShareBar } from "./charts";

export type OpenPeople = (query: PeopleQuery, title: string) => void;

const KPI_HINT: Record<OverviewMetric, string> = {
  writable: "alcanzadas, sin rebote ni baja",
  reached: "recibieron al menos una campaña",
  bounced: "la dirección no existe; bloqueadas",
  baja: "pidieron no recibir más",
  with_open_case: "de las alcanzadas, con un caso en curso",
};

const KPI_ORDER: OverviewMetric[] = ["writable", "reached", "bounced", "baja", "with_open_case"];

/** «Antes del próximo envío»: only permanent bounce notices the worker has not blocked. */
export function AnalyticsAlert({ data }: { data: Analytics }) {
  const n = data.alerts.ndr_unblocked;
  if (!n) return null;
  return (
    <div role="alert" className="flex flex-wrap items-start gap-2 rounded-lg border border-warn/40 bg-warn-bg px-3.5 py-2.5 text-xs text-warn" data-testid="analytics-alert">
      <span>
        <b>Antes del próximo envío:</b> {fmtInt(n)} {n === 1 ? "rebote permanente" : "rebotes permanentes"} en hilos de campaña
        siguen sin bloquear. Si no se bloquean, la próxima campaña vuelve a escribir a direcciones que no existen.
      </span>
    </div>
  );
}

/** Five figures, the lead one first, each opening the people it counts. */
export function AnalyticsKpis({ data, onOpen }: { data: Analytics; onOpen: OpenPeople }) {
  const o = data.overview;
  return (
    <dl className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-5" data-testid="analytics-kpis">
      {KPI_ORDER.map((m, i) => {
        const lead = m === "writable";
        const tone = m === "bounced" ? "text-bad" : lead ? "text-accent-strong" : "text-ink";
        const share = m === "bounced" ? pct(o.bounced, o.reached) : null;
        return (
          <button
            key={m}
            type="button"
            onClick={() => onOpen({ kind: "overview", metric: m }, OVERVIEW_LABEL[m])}
            style={{ animationDelay: `${i * 50}ms` }}
            className={`crm-card-in rounded-xl border px-3.5 py-3 text-left transition hover:-translate-y-0.5 hover:border-line-strong hover:shadow-sm motion-reduce:transition-none motion-reduce:hover:translate-y-0 ${
              lead ? "border-accent/30 bg-brand-50" : "border-line bg-canvas-raised"
            }`}
            data-testid={`kpi-${m}`}
          >
            <dt className="text-[11px] font-medium text-ink-faint">{OVERVIEW_LABEL[m]}</dt>
            <dd className={`mt-1 text-2xl font-bold tracking-tight ${tone}`}>
              <CountUp value={o[m]} />
            </dd>
            <p className="mt-1 text-[11px] leading-4 text-ink-faint">
              {share ? `${share} de las alcanzadas · ` : ""}
              {KPI_HINT[m]}
            </p>
          </button>
        );
      })}
    </dl>
  );
}

/** «Tu base de contactos»: everyone reached, split by whether we may still write to them. */
export function ContactBase({ data }: { data: Analytics }) {
  const o = data.overview;
  const soft = data.alerts.soft_bounces;
  return (
    <section className="rounded-xl border border-line bg-canvas-raised p-4" aria-label="Tu base de contactos" data-testid="contact-base">
      <h2 className="mb-3 text-[13px] font-semibold text-ink">Tu base de contactos</h2>
      <Donut
        label="Tu base de contactos"
        centerLabel="alcanzadas"
        slices={[
          { key: "writable", label: "Se puede escribir", value: o.writable, color: "var(--chart-ok)" },
          { key: "bounced", label: "Rebote", value: o.bounced, color: "var(--chart-bounce)" },
          { key: "baja", label: "Baja", value: o.baja, color: "var(--chart-optout)" },
          ...(o.other_blocks ? [{ key: "other", label: "Otro bloqueo", value: o.other_blocks, color: "var(--chart-other)" }] : []),
        ]}
      />
      {soft ? (
        <p className="mt-3 text-[11px] text-ink-faint" data-testid="soft-bounces">
          {fmtInt(soft)} {soft === 1 ? "rebote temporal" : "rebotes temporales"} (casilla llena, servidor ocupado…) no se bloquean: esas
          direcciones pueden volver a funcionar.
        </p>
      ) : null}
    </section>
  );
}

const RESULT_SERIES = [
  { key: "responded", label: "Respondieron", color: "var(--chart-cat-1)" },
  { key: "quote_request", label: "Pidieron cotización", color: "var(--chart-cat-2)" },
  { key: "cases_after", label: "Casos después", color: "var(--chart-cat-3)" },
] as const;

/** What each campaign produced, side by side. One axis (people / cases); «—» is not measured. */
export function CampaignResultsChart({ data, campaigns, onOpen }: { data: Analytics; campaigns: CampaignSummary[]; onOpen: OpenPeople }) {
  const rows = campaigns
    .filter((c) => data.campaigns[c.campaign_id])
    .sort((a, b) => (b.first_sent_at ?? "").localeCompare(a.first_sent_at ?? ""));
  const max = Math.max(1, ...rows.flatMap((c) => RESULT_SERIES.map((s) => data.campaigns[c.campaign_id][s.key] ?? 0)));
  return (
    <section className="rounded-xl border border-line bg-canvas-raised p-4" aria-label="Resultados por campaña" data-testid="results-chart">
      <div className="mb-2 flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <h2 className="text-[13px] font-semibold text-ink">Resultados por campaña</h2>
        <span className="text-[11px] text-ink-faint">hasta {data.reply_window_days} días después de cada envío</span>
      </div>
      <div className="mb-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-ink-muted">
        {RESULT_SERIES.map((s) => (
          <span key={s.key}>
            <span className="mr-1.5 inline-block h-2 w-2 rounded-sm" style={{ background: s.color }} />
            {s.label}
          </span>
        ))}
      </div>
      <div className="space-y-3">
        {rows.map((c) => {
          const f = data.campaigns[c.campaign_id];
          return (
            <div key={c.campaign_id}>
              <p className="mb-1 truncate text-xs font-medium text-ink" title={c.name}>{c.name}</p>
              <div className="space-y-1">
                {RESULT_SERIES.map((s) => {
                  const v = f[s.key];
                  return (
                    <button
                      key={s.key}
                      type="button"
                      disabled={!v}
                      onClick={() => onOpen({ kind: "campaign", metric: s.key, campaignId: c.campaign_id }, `${c.name} · ${s.label}`)}
                      className="group flex w-full items-center gap-2 text-left"
                      aria-label={`${s.label}: ${v === null ? "aún no medido" : v}`}
                    >
                      <span className="h-2.5 flex-1 overflow-hidden rounded-full bg-canvas-sunken">
                        {v ? (
                          <span className="chart-bar-x block h-full rounded-full group-hover:opacity-80"
                                style={{ width: `${(v / max) * 100}%`, background: s.color }} />
                        ) : null}
                      </span>
                      <span className="w-8 text-right text-[11px] tabular-nums text-ink">
                        {v === null ? <span className="text-ink-faint" title="Aún no medido">—</span> : fmtInt(v)}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

const COLUMNS: CoverageColumn[] = ["contacted", "not_contacted", "blocked"];
const COLUMN_COLOR: Record<CoverageColumn, string> = {
  contacted: "var(--chart-ok)",
  not_contacted: "var(--chart-new)",
  blocked: "var(--chart-bounce)",
};
const OPEN: Segment[] = ["solicitada", "enviada", "conversacion", "pausa"];

interface Group {
  key: "ganada" | "abierto" | "perdida" | "sin_caso";
  label: string;
  hint: string;
  parts: Segment[];
}

const GROUPS: Group[] = [
  { key: "ganada", label: "Clientes", hint: "con un caso ganado", parts: ["ganada"] },
  { key: "abierto", label: "Con caso abierto", hint: "solicitada, enviada, en conversación o en pausa", parts: OPEN },
  { key: "perdida", label: "Caso perdido", hint: "su último caso se perdió", parts: ["perdida"] },
  { key: "sin_caso", label: "Sin caso", hint: "nunca tuvieron un caso", parts: ["sin_caso"] },
];

function sum(rows: CoverageRow[]): Omit<CoverageRow, "segment"> {
  return rows.reduce(
    (t, r) => ({ people: t.people + r.people, contacted: t.contacted + r.contacted, not_contacted: t.not_contacted + r.not_contacted, blocked: t.blocked + r.blocked }),
    { people: 0, contacted: 0, not_contacted: 0, blocked: 0 },
  );
}

/** «Cobertura del CRM»: everyone in the CRM with an email, by case situation × written to. */
export function CoverageCard({ data, onOpen }: { data: Analytics; onOpen: OpenPeople }) {
  const [open, setOpen] = useState(false);
  const by = Object.fromEntries(data.coverage.map((r) => [r.segment, r])) as Record<Segment, CoverageRow>;
  const total = sum(data.coverage);
  const segQuery = (g: Group): PeopleQuery & { kind: "coverage" } =>
    ({ kind: "coverage", segment: g.key === "abierto" ? "abierto" : g.parts[0] });
  return (
    <section className="rounded-xl border border-line bg-canvas-raised p-4" aria-label="Cobertura del CRM" data-testid="coverage-card">
      <div className="flex flex-wrap items-baseline gap-x-3">
        <h2 className="text-[13px] font-semibold text-ink">Cobertura del CRM</h2>
        <span className="text-[11px] text-ink-faint" data-testid="coverage-total">{fmtInt(total.people)} personas con correo, sin proveedores</span>
      </div>
      <p className="mt-0.5 text-[11px] text-ink-faint">
        Una persona tiene un caso cuando figura en él o escribió (o le escribimos) en sus correos.
      </p>
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-ink-muted">
        {COLUMNS.map((c) => (
          <span key={c}>
            <span className="mr-1.5 inline-block h-2 w-2 rounded-sm" style={{ background: COLUMN_COLOR[c] }} />
            {COLUMN_LABEL[c]}
          </span>
        ))}
      </div>
      <div className="mt-2 hidden grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_repeat(3,96px)] gap-x-4 border-b border-line pb-1.5 text-[11px] font-medium text-ink-faint md:grid">
        <span>Situación</span>
        <span />
        {COLUMNS.map((c) => (
          <span key={c} className="text-right">{COLUMN_LABEL[c]}</span>
        ))}
      </div>
      <div className="divide-y divide-line/70">
        {GROUPS.map((g) => {
          const r = sum(g.parts.map((p) => by[p]).filter(Boolean));
          return (
            <div key={g.key} className="grid grid-cols-1 gap-x-4 gap-y-1.5 py-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_repeat(3,96px)] md:items-center" data-testid={`coverage-${g.key}`}>
              <div className="min-w-0">
                <button type="button" disabled={!r.people} onClick={() => onOpen(segQuery(g), g.label)} className="text-left text-[13px] font-semibold text-ink enabled:hover:underline">
                  {g.label} <span className="ml-1 font-normal tabular-nums text-ink-muted">{fmtInt(r.people)}</span>
                </button>
                <p className="text-[11px] text-ink-faint">
                  {g.hint}
                  {g.key === "abierto" ? (
                    <button type="button" onClick={() => setOpen((v) => !v)} className="ml-1.5 font-medium text-accent hover:underline">
                      {open ? "ocultar detalle" : "ver detalle"}
                    </button>
                  ) : null}
                </p>
              </div>
              <ShareBar
                label={g.label}
                slices={COLUMNS.map((c) => ({ key: c, label: COLUMN_LABEL[c], value: r[c], color: COLUMN_COLOR[c] }))}
              />
              {COLUMNS.map((c) => (
                <Figure key={c} n={r[c]} label={COLUMN_LABEL[c]}
                        onOpen={() => onOpen({ ...segQuery(g), column: c }, `${g.label} · ${COLUMN_LABEL[c]}`)} />
              ))}
              {g.key === "abierto" && open ? (
                <div className="col-span-full mt-1 space-y-1 rounded-lg bg-canvas-sunken/60 p-2" data-testid="coverage-open-detail">
                  {OPEN.map((s) => {
                    const row = by[s];
                    return (
                      <div key={s} className="grid grid-cols-[minmax(0,1fr)_repeat(3,96px)] items-center gap-x-4 text-xs">
                        <span className="text-ink-muted">{SEGMENT_LABEL[s]} · <b className="tabular-nums text-ink">{fmtInt(row?.people ?? 0)}</b></span>
                        {COLUMNS.map((c) => (
                          <Figure key={c} n={row?.[c] ?? 0} label={COLUMN_LABEL[c]}
                                  onOpen={() => onOpen({ kind: "coverage", segment: s, column: c }, `${SEGMENT_LABEL[s]} · ${COLUMN_LABEL[c]}`)} />
                        ))}
                      </div>
                    );
                  })}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function Figure({ n, label, onOpen }: { n: number; label: string; onOpen: () => void }) {
  return (
    <span className="flex items-baseline justify-between gap-2 md:block md:text-right">
      <span className="text-[11px] text-ink-faint md:hidden">{label}</span>
      {n ? (
        <button type="button" onClick={onOpen} className="text-[13px] tabular-nums text-ink hover:underline" aria-label={`${label}: ${n}`}>
          {fmtInt(n)}
        </button>
      ) : (
        <span className="text-[13px] tabular-nums text-ink-faint">0</span>
      )}
    </span>
  );
}
