/** «Seguimiento»'s KPI strip: the whole team's numbers for today, whatever the filters. */
import type { ProfileCard } from "../../api/authClient";
import { compactClp, formatMoney } from "../money";
import type { seguimientoKpis } from "../seguimiento";
import { initials } from "../ui";

type Kpis = ReturnType<typeof seguimientoKpis>;

/** «Llamadas hoy» per person (every profile, zero included) plus anyone else who called today. */
function callsPerPerson(kpis: Kpis, profiles: ProfileCard[]): { id: string; name: string; today: number }[] {
  const byId = new Map(kpis.callsByOperator.map((o) => [o.operatorId, o]));
  const people = profiles.map((p) => ({ id: p.id, name: p.displayName, today: byId.get(p.id)?.today ?? 0 }));
  const others = kpis.callsByOperator.filter((o) => !profiles.some((p) => p.id === o.operatorId));
  return [...people, ...others.map((o) => ({ id: o.operatorId, name: o.name, today: o.today }))];
}

export function KpiStrip({ kpis, profiles, rowsCount }: { kpis: Kpis; profiles: ProfileCard[]; rowsCount: number }) {
  const pct = kpis.goal > 0 ? Math.min(100, Math.round((kpis.callsToday / kpis.goal) * 100)) : 0;
  const tile = "flex min-w-0 flex-col gap-1 rounded-xl border border-line bg-canvas-raised px-4 py-3.5";
  const label = "text-[12px] font-semibold text-ink-muted";
  const value = "text-[26px] font-extrabold leading-tight tracking-tight tabular-nums text-ink";
  const sub = "text-[12px] text-ink-faint";
  return (
    <section aria-label="Indicadores de hoy" className="grid grid-cols-2 gap-3 min-[900px]:grid-cols-[1.5fr_repeat(4,minmax(0,1fr))]">
      <div className={`${tile} col-span-2 min-[900px]:col-span-1`} data-testid="kpi-calls">
        <span className={label}>Llamadas hoy</span>
        <span className={value}>
          {kpis.callsToday} <span className="text-[14px] font-semibold text-ink-faint">de {kpis.goal}</span>
        </span>
        <div className="mt-0.5 flex flex-wrap gap-x-3 gap-y-1">
          {callsPerPerson(kpis, profiles).map((p) => (
            <span key={p.id} className="inline-flex items-center gap-1.5 text-[12px] text-ink-muted" data-testid={`kpi-calls-${p.id}`}>
              <span aria-hidden="true" className="grid h-[22px] w-[22px] place-items-center rounded-full bg-canvas-sunken text-[10px] font-bold text-ink-muted">
                {initials(p.name)}
              </span>
              {p.name} <b className="text-[15px] text-ink">{p.today}</b>
            </span>
          ))}
        </div>
        {/* The bar alone, no track: how far today's calls are toward the goal. */}
        <div aria-hidden="true" className="mt-1.5 h-1 rounded-sm bg-accent" style={{ width: `${pct}%` }} />
      </div>
      <div className={tile} data-testid="kpi-touched">
        <span className={label}>Casos tocados hoy</span>
        <span className={value}>{kpis.touchedToday}</span>
        <span className={sub}>llamada o correo</span>
      </div>
      <div className={tile} data-testid="kpi-late">
        <span className={label}>Sin toque 3+ días</span>
        <span className={`${value} ${kpis.late > 0 ? "!text-warn" : ""}`}>{kpis.late}</span>
        <span className={sub}>ritmo de 3 días</span>
      </div>
      <div className={tile} data-testid="kpi-emails">
        <span className={label}>Correos enviados hoy</span>
        <span className={value}>{kpis.emailsToday}</span>
        <span className={sub}>se cuentan solos</span>
      </div>
      <div className={tile} data-testid="kpi-money">
        <span className={label}>En seguimiento</span>
        <span className={value} title={formatMoney(String(kpis.moneyClp), "CLP") ?? undefined} data-testid="kpi-money-value">
          {compactClp(kpis.moneyClp)}
        </span>
        <span className={sub}>{rowsCount} {rowsCount === 1 ? "cotización" : "cotizaciones"}</span>
      </div>
    </section>
  );
}
