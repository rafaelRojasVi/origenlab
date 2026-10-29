import { fmtInt } from "../ui";
import {
  COLUMN_LABEL,
  OVERVIEW_LABEL,
  SEGMENT_LABEL,
  pct,
  type CoverageColumn,
  type MarketingAnalytics as Analytics,
  type OverviewMetric,
  type PeopleQuery,
} from "./analytics";

export type OpenPeople = (query: PeopleQuery, title: string) => void;

const KPI_HINT: Record<OverviewMetric, string> = {
  writable: "alcanzadas, sin rebote ni baja",
  reached: "recibieron al menos una campaña",
  bounced: "la dirección no existe; bloqueadas",
  baja: "pidieron no recibir más",
  with_open_case: "de las alcanzadas, con un caso en curso",
};

const KPI_ORDER: OverviewMetric[] = ["writable", "reached", "bounced", "baja", "with_open_case"];

/** «Antes del próximo envío»: only when a bounce notice sits in a campaign thread with no block. */
export function AnalyticsAlert({ data }: { data: Analytics }) {
  const n = data.alerts.ndr_unblocked;
  if (!n) return null;
  return (
    <div role="alert" className="flex flex-wrap items-start gap-2 rounded-lg border border-warn/40 bg-warn-bg px-3.5 py-2.5 text-xs text-warn" data-testid="analytics-alert">
      <span>
        <b>Antes del próximo envío:</b> {fmtInt(n)} {n === 1 ? "aviso de rebote" : "avisos de rebote"} en hilos de campaña sin bloquear.
        Si no se bloquean, la próxima campaña vuelve a escribir a direcciones que no existen.
      </span>
    </div>
  );
}

/** Five figures, the lead one first, each opening the people it counts. */
export function AnalyticsKpis({ data, onOpen }: { data: Analytics; onOpen: OpenPeople }) {
  const o = data.overview;
  return (
    <dl className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-5" data-testid="analytics-kpis">
      {KPI_ORDER.map((m) => {
        const lead = m === "writable";
        const tone = m === "bounced" ? "text-bad" : lead ? "text-accent-strong" : "text-ink";
        const share = m === "bounced" ? pct(o.bounced, o.reached) : null;
        return (
          <button
            key={m}
            type="button"
            onClick={() => onOpen({ kind: "overview", metric: m }, OVERVIEW_LABEL[m])}
            className={`rounded-lg border px-3.5 py-3 text-left hover:border-line-strong ${
              lead ? "border-accent/30 bg-brand-50" : "border-line bg-canvas-raised"
            }`}
            data-testid={`kpi-${m}`}
          >
            <dt className="text-[11px] font-medium text-ink-faint">{OVERVIEW_LABEL[m]}</dt>
            <dd className={`mt-1 text-2xl font-bold tabular-nums tracking-tight ${tone}`}>{fmtInt(o[m])}</dd>
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
export function ContactBaseBar({ data }: { data: Analytics }) {
  const o = data.overview;
  const parts = [
    { key: "writable", n: o.writable, label: "se puede escribir", color: "bg-accent" },
    { key: "bounced", n: o.bounced, label: "rebote", color: "bg-bad" },
    { key: "baja", n: o.baja, label: "baja", color: "bg-warn" },
    { key: "other", n: o.other_blocks, label: "otro bloqueo", color: "bg-ink-faint" },
  ].filter((p) => p.n > 0);
  const whole = Math.max(o.reached, 1);
  return (
    <section className="rounded-lg border border-line bg-canvas-raised p-4" aria-label="Tu base de contactos" data-testid="contact-base">
      <h2 className="mb-2.5 text-[13px] font-semibold text-ink">Tu base de contactos</h2>
      <div className="flex h-3 overflow-hidden rounded-full bg-canvas-sunken" role="img"
           aria-label={parts.map((p) => `${p.label}: ${p.n}`).join(", ")}>
        {parts.map((p) => (
          <span key={p.key} className={p.color} style={{ width: `${(p.n / whole) * 100}%` }} />
        ))}
      </div>
      <div className="mt-2.5 flex flex-wrap gap-x-5 gap-y-1 text-xs text-ink-muted">
        {parts.map((p) => (
          <span key={p.key}>
            <span aria-hidden="true" className={`mr-1.5 inline-block h-2 w-2 rounded-sm ${p.color}`} />
            <b className="mr-1 tabular-nums text-ink">{fmtInt(p.n)}</b>
            {p.label}
          </span>
        ))}
      </div>
    </section>
  );
}

const COLUMNS: CoverageColumn[] = ["contacted", "not_contacted", "blocked"];

/** «Cobertura del CRM»: everyone in the CRM with an email, by case situation × contacted. */
export function CoverageCard({ data, onOpen }: { data: Analytics; onOpen: OpenPeople }) {
  const rows = data.coverage;
  const total = rows.reduce(
    (t, r) => ({ people: t.people + r.people, contacted: t.contacted + r.contacted, not_contacted: t.not_contacted + r.not_contacted, blocked: t.blocked + r.blocked }),
    { people: 0, contacted: 0, not_contacted: 0, blocked: 0 },
  );
  const cell = (segment: PeopleQuery & { kind: "coverage" }, n: number, label: string, strong = false) => (
    <td className="px-2 py-2 text-right">
      {n ? (
        <button type="button" onClick={() => onOpen(segment, label)} className={`tabular-nums hover:underline ${strong ? "font-semibold text-ink" : "text-ink"}`}>
          {fmtInt(n)}
        </button>
      ) : (
        <span className="tabular-nums text-ink-faint">0</span>
      )}
    </td>
  );
  return (
    <section className="rounded-lg border border-line bg-canvas-raised p-4" aria-label="Cobertura del CRM" data-testid="coverage-card">
      <h2 className="text-[13px] font-semibold text-ink">Cobertura del CRM</h2>
      <p className="mt-0.5 text-[11px] text-ink-faint">
        Todas las personas del CRM con correo (sin proveedores), según su caso más avanzado. «Sin contactar» = nunca recibieron
        una campaña y aún se les puede escribir.
      </p>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full min-w-[520px] text-xs">
          <thead>
            <tr className="border-b border-line text-[11px] text-ink-faint">
              <th className="px-2 py-1.5 text-left font-medium">Situación</th>
              <th className="px-2 py-1.5 text-right font-medium">Personas</th>
              {COLUMNS.map((c) => (
                <th key={c} className="px-2 py-1.5 text-right font-medium">{COLUMN_LABEL[c]}</th>
              ))}
              <th className="w-28 px-2 py-1.5" aria-hidden="true" />
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.segment} className="border-b border-line/70" data-testid={`coverage-${r.segment}`}>
                <td className="px-2 py-2 text-ink">{SEGMENT_LABEL[r.segment]}</td>
                {cell({ kind: "coverage", segment: r.segment }, r.people, SEGMENT_LABEL[r.segment], true)}
                {COLUMNS.map((c) => (
                  <Cell key={c} n={r[c]} onOpen={() => onOpen({ kind: "coverage", segment: r.segment, column: c }, `${SEGMENT_LABEL[r.segment]} · ${COLUMN_LABEL[c]}`)} />
                ))}
                <td className="px-2 py-2"><MiniBar contacted={r.contacted} notContacted={r.not_contacted} blocked={r.blocked} /></td>
              </tr>
            ))}
            <tr className="font-semibold" data-testid="coverage-total">
              <td className="px-2 py-2 text-ink">Total CRM</td>
              {cell({ kind: "coverage", segment: "total" }, total.people, "Total CRM", true)}
              {COLUMNS.map((c) => (
                <Cell key={c} n={total[c]} onOpen={() => onOpen({ kind: "coverage", segment: "total", column: c }, `Total CRM · ${COLUMN_LABEL[c]}`)} />
              ))}
              <td className="px-2 py-2"><MiniBar contacted={total.contacted} notContacted={total.not_contacted} blocked={total.blocked} /></td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Cell({ n, onOpen }: { n: number; onOpen: () => void }) {
  return (
    <td className="px-2 py-2 text-right">
      {n ? (
        <button type="button" onClick={onOpen} className="tabular-nums text-ink hover:underline">{fmtInt(n)}</button>
      ) : (
        <span className="tabular-nums text-ink-faint">0</span>
      )}
    </td>
  );
}

function MiniBar({ contacted, notContacted, blocked }: { contacted: number; notContacted: number; blocked: number }) {
  const whole = Math.max(contacted + notContacted + blocked, 1);
  return (
    <div className="flex h-1.5 overflow-hidden rounded-full bg-canvas-sunken" aria-hidden="true">
      <span className="bg-accent" style={{ width: `${(contacted / whole) * 100}%` }} />
      <span className="bg-info" style={{ width: `${(notContacted / whole) * 100}%` }} />
      <span className="bg-bad" style={{ width: `${(blocked / whole) * 100}%` }} />
    </div>
  );
}
