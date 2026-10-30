import { fmtInt } from "../ui";
import { AUTO_KINDS, BLOCK_KIND_LABEL, type BlockKind, type MarketingAnalytics } from "./analytics";
import { CountUp, Donut, WeeklyBars } from "./charts";

const ORIGIN = [
  { key: "auto", label: "Rebote automático", kinds: AUTO_KINDS, color: "var(--chart-cat-1)" },
  { key: "revisado", label: "Rebote revisado a mano", kinds: ["rebote_revisado"] as BlockKind[], color: "var(--chart-cat-2)" },
  { key: "historico", label: "Rebote histórico (V1)", kinds: ["rebote_historico"] as BlockKind[], color: "var(--chart-cat-3)" },
  { key: "baja", label: "Pidió la baja", kinds: ["baja"] as BlockKind[], color: "var(--chart-cat-4)" },
  { key: "otro", label: "Otro bloqueo", kinds: ["otro"] as BlockKind[], color: "var(--chart-other)" },
];

const WEEKS_SHOWN = 12;

/**
 * «Bajas y rebotes» at a glance: how many addresses are blocked, where each block came from, the
 * automatic bounce kinds, and blocks added per week. Read from the same analytics as Campañas.
 */
export function BlocksOverview({ data }: { data: MarketingAnalytics }) {
  const n = Object.fromEntries(data.blocks.by_kind.map((k) => [k.kind, k.n])) as Partial<Record<BlockKind, number>>;
  const of = (kinds: BlockKind[]) => kinds.reduce((t, k) => t + (n[k] ?? 0), 0);
  const total = data.blocks.by_kind.reduce((t, k) => t + k.n, 0);
  const auto = of(AUTO_KINDS);
  const bajas = of(["baja"]);
  const weeks = data.blocks.by_week.slice(-WEEKS_SHOWN);
  const last = weeks.at(-1);
  const thisWeek = last ? last.rebote + last.baja + last.otro : 0;
  const maxAuto = Math.max(1, ...AUTO_KINDS.map((k) => n[k] ?? 0));
  const kpis = [
    { key: "total", label: "Direcciones bloqueadas", value: total, hint: "nunca reciben una campaña" },
    { key: "auto", label: "Rebotes bloqueados solos", value: auto, hint: "desde el aviso de rebote, sin revisar" },
    { key: "bajas", label: "Pidieron la baja", value: bajas, hint: "permanente; no se re-suscribe" },
    { key: "week", label: "Esta semana", value: thisWeek, hint: "bloqueos nuevos" },
  ];
  return (
    <div className="space-y-3" data-testid="blocks-overview">
      <dl className="grid grid-cols-2 gap-2.5 lg:grid-cols-4">
        {kpis.map((k, i) => (
          <div key={k.key} style={{ animationDelay: `${i * 50}ms` }}
               className="crm-card-in rounded-xl border border-line bg-canvas-raised px-3.5 py-3" data-testid={`blocks-kpi-${k.key}`}>
            <dt className="text-[11px] font-medium text-ink-faint">{k.label}</dt>
            <dd className="mt-1 text-2xl font-bold tracking-tight text-ink"><CountUp value={k.value} /></dd>
            <p className="mt-1 text-[11px] leading-4 text-ink-faint">{k.hint}</p>
          </div>
        ))}
      </dl>
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <section className="rounded-xl border border-line bg-canvas-raised p-4" aria-label="Por qué está bloqueada cada dirección">
          <h2 className="mb-3 text-[13px] font-semibold text-ink">Por qué está bloqueada</h2>
          <Donut
            label="Direcciones bloqueadas por origen"
            centerLabel="bloqueadas"
            slices={ORIGIN.map((o) => ({ key: o.key, label: o.label, value: of(o.kinds), color: o.color })).filter((s) => s.value > 0)}
          />
          <div className="mt-4 space-y-1.5" data-testid="auto-kinds">
            <p className="text-[11px] font-medium text-ink-faint">Rebotes automáticos, por tipo</p>
            {AUTO_KINDS.map((k) => (
              <div key={k} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)_40px] items-center gap-2 text-xs">
                <span className="truncate text-ink-muted">{BLOCK_KIND_LABEL[k]}</span>
                <span className="h-2 overflow-hidden rounded-full bg-canvas-sunken">
                  {n[k] ? <span className="chart-bar-x block h-full rounded-full" style={{ width: `${((n[k] ?? 0) / maxAuto) * 100}%`, background: "var(--chart-cat-1)" }} /> : null}
                </span>
                <span className="text-right tabular-nums text-ink">{fmtInt(n[k] ?? 0)}</span>
              </div>
            ))}
          </div>
        </section>
        <section className="rounded-xl border border-line bg-canvas-raised p-4" aria-label="Bloqueos nuevos por semana">
          <h2 className="mb-2 text-[13px] font-semibold text-ink">Bloqueos nuevos por semana</h2>
          {weeks.length ? (
            <WeeklyBars
              label="Bloqueos nuevos por semana"
              points={weeks.map((w) => ({ week: w.week, values: { rebote: w.rebote, baja: w.baja, otro: w.otro } }))}
              series={[
                { key: "rebote", label: "Rebote", color: "var(--chart-bounce)" },
                { key: "baja", label: "Baja", color: "var(--chart-optout)" },
                { key: "otro", label: "Otro", color: "var(--chart-other)" },
              ]}
            />
          ) : (
            <p className="text-xs text-ink-muted">Sin bloqueos registrados.</p>
          )}
          <p className="mt-2 text-[11px] text-ink-faint">
            Los rebotes se bloquean solos cuando el aviso dice que la dirección no puede recibir correo: casilla inexistente, ya no
            está en la organización, dominio inexistente o cuenta desactivada. Los rebotes temporales no se bloquean.
          </p>
        </section>
      </div>
    </div>
  );
}
