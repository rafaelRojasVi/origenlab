/**
 * «Socios» (admin only) — partner distribution for the current month.
 *
 * Shows:
 * - Current rule and when it was set
 * - «A repartir» equation: margen bruto − gastos fijos = a repartir
 * - Current rule applied to this month's numbers
 * - Alternatives A–D side by side for comparison
 *
 * This tab is admin-only in the nav (adminOnly: true in nav.ts).
 */
import { formatMoney } from "../money";
import { EmptyState, PageHeader, Skeleton, fmtDate } from "../ui";
import { useResource } from "../useResource";
import { computeApartir } from "./financeCalc";
import type { FinanceSettings, MesData } from "./financeApi";
import { fetchFinanceMes, fetchFinanceSettings } from "./financeApi";

const clp = (n: number) => formatMoney(String(Math.round(n)), "CLP") ?? "—";
const pct = (n: number) => `${(n * 100).toFixed(0)} %`;

const RULE_LABEL: Record<string, string> = {
  actual_50_50: "Actual 50/50",
  opcion_a: "Opción A",
  opcion_b: "Opción B",
  opcion_c: "Opción C",
  opcion_d: "Opción D",
};

const RULE_DESCRIPTION: Record<string, string> = {
  actual_50_50: "Reparto igualitario del resultado después de gastos.",
  opcion_a: "Tatiana absorbe el arriendo; la empresa cubre una fracción acordada.",
  opcion_b: "Sueldo fijo a Tatiana primero; residual 50/50.",
  opcion_c: "Idéntica a B con un parámetro de sueldo diferente.",
  opcion_d: "Sebastián aporta capital a tasa acordada; residual 50/50.",
};

// All five rule variants to compare
const ALTERNATIVE_RULES = [
  { rule_name: "actual_50_50", arriendo_clp: 0, tasa_sebastian: null, sueldo_tatiana: 0, arriendo_pct_empresa: 0, split_pct: 0.5 },
  { rule_name: "opcion_a", arriendo_clp: 600_000, tasa_sebastian: null, sueldo_tatiana: 0, arriendo_pct_empresa: 0.5, split_pct: 0.5 },
  { rule_name: "opcion_b", arriendo_clp: 600_000, tasa_sebastian: null, sueldo_tatiana: 800_000, arriendo_pct_empresa: 0, split_pct: 0.5 },
  { rule_name: "opcion_c", arriendo_clp: 600_000, tasa_sebastian: null, sueldo_tatiana: 1_200_000, arriendo_pct_empresa: 0, split_pct: 0.5 },
  { rule_name: "opcion_d", arriendo_clp: 600_000, tasa_sebastian: 0.02, sueldo_tatiana: 0, arriendo_pct_empresa: 0, split_pct: 0.5 },
];

// ── components ─────────────────────────────────────────────────────────────────

function EquationRow({ label, value, op }: { label: string; value: string; op?: string }) {
  return (
    <div className="flex flex-col items-center gap-0.5 text-center">
      <p className="text-[11px] font-mono uppercase tracking-wide text-ink-muted">{label}</p>
      <p className="font-mono text-[18px] font-semibold tabular-nums text-ink">{value}</p>
      {op && <p className="text-[13px] font-light text-ink-faint">{op}</p>}
    </div>
  );
}

function PartnerEquation({ margen, gastos }: { margen: number; gastos: number }) {
  const arepartir = margen - gastos;
  return (
    <div className="rounded-xl border border-line bg-raised px-4 py-4">
      <p className="mb-3 text-[11px] font-semibold uppercase tracking-wide text-ink-muted">A repartir este mes</p>
      <div className="flex flex-wrap items-center justify-center gap-x-3 gap-y-2">
        <EquationRow label="Margen bruto" value={clp(margen)} />
        <span className="mt-4 text-xl font-light text-ink-muted">−</span>
        <EquationRow label="Gastos fijos" value={clp(gastos)} />
        <span className="mt-4 text-xl font-light text-ink-muted">=</span>
        <EquationRow label="A repartir" value={clp(arepartir)} />
      </div>
      {arepartir < 0 && (
        <p className="mt-2 text-center text-xs text-bad">Los gastos superan el margen este mes.</p>
      )}
    </div>
  );
}

function RuleCard({
  rule,
  margen,
  gastos,
  highlight = false,
}: {
  rule: typeof ALTERNATIVE_RULES[0];
  margen: number;
  gastos: number;
  highlight?: boolean;
}) {
  const result = computeApartir(margen, gastos, rule);
  return (
    <div className={`flex flex-col rounded-xl border px-3 py-3 ${highlight ? "border-accent bg-accent/5" : "border-line bg-sunken"}`}>
      <div className="flex items-baseline gap-2">
        <p className={`text-[13px] font-semibold ${highlight ? "text-accent" : "text-ink"}`}>{RULE_LABEL[rule.rule_name] ?? rule.rule_name}</p>
        {highlight && <span className="rounded-full bg-accent/10 px-1.5 py-0.5 text-[10px] font-semibold text-accent">Vigente</span>}
      </div>
      <p className="mt-0.5 text-[11px] text-ink-muted">{RULE_DESCRIPTION[rule.rule_name] ?? ""}</p>
      <div className="mt-2.5 grid grid-cols-2 gap-x-2 gap-y-1 border-t border-line/60 pt-2.5">
        <div>
          <p className="text-[10px] font-mono uppercase tracking-wide text-ink-faint">Tatiana</p>
          <p className="font-mono text-[15px] font-semibold tabular-nums text-ink">{clp(result.tatiana)}</p>
        </div>
        <div>
          <p className="text-[10px] font-mono uppercase tracking-wide text-ink-faint">Sebastián</p>
          <p className="font-mono text-[15px] font-semibold tabular-nums text-ink">{clp(result.sebastian)}</p>
        </div>
      </div>
    </div>
  );
}

function SociosContent({
  settings,
  mes,
}: {
  settings: FinanceSettings;
  mes: MesData;
}) {
  const rule = settings.partner_rule;

  // We need margen bruto from the mes data.
  // "Margen bruto" ≈ vendido neto + pipeline ponderado (conservative = vendido only)
  // For the distribution equation we use vendido.net as a conservative base,
  // since that's what's actually been invoiced.
  const margenBruto = mes.vendido.net;
  const gastosFijos = mes.gastos.total;

  if (!rule) {
    return (
      <>
        <PartnerEquation margen={margenBruto} gastos={gastosFijos} />
        <EmptyState title="Sin regla de socios configurada">
          Configura la regla desde Ajustes → Metas y socios.
        </EmptyState>
      </>
    );
  }

  // Build the full rule with actual persisted parameters
  const activeRule = {
    rule_name: rule.rule_name,
    arriendo_clp: rule.arriendo_clp,
    tasa_sebastian: rule.tasa_sebastian,
    sueldo_tatiana: rule.sueldo_tatiana,
    arriendo_pct_empresa: rule.arriendo_pct_empresa,
    split_pct: rule.split_pct,
  };

  const activeResult = computeApartir(margenBruto, gastosFijos, activeRule);

  return (
    <div className="flex flex-col gap-4">
      {/* Equation */}
      <PartnerEquation margen={margenBruto} gastos={gastosFijos} />

      {/* Current rule summary */}
      <div className="rounded-xl border border-accent/40 bg-accent/5 px-4 py-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-wide text-accent">Regla vigente</p>
            <p className="mt-0.5 text-[15px] font-semibold text-ink">{RULE_LABEL[rule.rule_name] ?? rule.rule_name}</p>
            <p className="text-xs text-ink-muted">desde {fmtDate(rule.valid_from)}</p>
          </div>
          <dl className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
            {rule.arriendo_clp > 0 && (
              <div><dt className="text-ink-faint">Arriendo</dt><dd className="font-medium tabular-nums">{clp(rule.arriendo_clp)}</dd></div>
            )}
            {rule.sueldo_tatiana > 0 && (
              <div><dt className="text-ink-faint">Sueldo Tatiana</dt><dd className="font-medium tabular-nums">{clp(rule.sueldo_tatiana)}</dd></div>
            )}
            {rule.tasa_sebastian != null && (
              <div><dt className="text-ink-faint">Tasa Sebastián</dt><dd className="font-medium tabular-nums">{pct(rule.tasa_sebastian)} / mes</dd></div>
            )}
            <div><dt className="text-ink-faint">Split</dt><dd className="font-medium">{pct(rule.split_pct)}/{pct(1 - rule.split_pct)}</dd></div>
          </dl>
        </div>
        <div className="mt-3 grid grid-cols-2 gap-3 border-t border-accent/20 pt-3">
          <div>
            <p className="text-[11px] font-mono uppercase tracking-wide text-ink-faint">Tatiana</p>
            <p className="font-mono text-[22px] font-semibold tabular-nums text-ink">{clp(activeResult.tatiana)}</p>
          </div>
          <div>
            <p className="text-[11px] font-mono uppercase tracking-wide text-ink-faint">Sebastián</p>
            <p className="font-mono text-[22px] font-semibold tabular-nums text-ink">{clp(activeResult.sebastian)}</p>
          </div>
        </div>
        {rule.notes && <p className="mt-2 text-xs text-ink-muted">{rule.notes}</p>}
      </div>

      {/* Alternatives */}
      <div>
        <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-ink-muted">Comparar alternativas</p>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
          {ALTERNATIVE_RULES.map((r) => (
            <RuleCard
              key={r.rule_name}
              rule={r.rule_name === rule.rule_name ? activeRule : r}
              margen={margenBruto}
              gastos={gastosFijos}
              highlight={r.rule_name === rule.rule_name}
            />
          ))}
        </div>
        <p className="mt-2 text-[11px] text-ink-faint">
          Las alternativas usan los parámetros de referencia; sólo la regla vigente usa los valores configurados.
          Ajusta los parámetros desde Ajustes → Metas y socios.
        </p>
      </div>
    </div>
  );
}

// ── main page ─────────────────────────────────────────────────────────────────

export function FinanzasSociosPage() {
  const [settingsState, reloadSettings] = useResource(fetchFinanceSettings);
  const [mesState, reloadMes] = useResource(fetchFinanceMes);

  // Loading / error states for both resources
  if (settingsState.kind === "loading" || mesState.kind === "loading") {
    return (
      <div className="flex flex-col gap-5">
        <PageHeader title="Socios" subtitle="Distribución entre socios (sólo administración)" />
        <Skeleton rows={3} />
      </div>
    );
  }

  if (settingsState.kind !== "ready" || mesState.kind !== "ready") {
    return (
      <div className="flex flex-col gap-5">
        <PageHeader title="Socios" subtitle="Distribución entre socios (sólo administración)" />
        <div className="flex flex-wrap gap-2">
          {settingsState.kind !== "ready" && (
            <button type="button" onClick={reloadSettings} className="text-sm text-accent underline">
              Reintentar ajustes
            </button>
          )}
          {mesState.kind !== "ready" && (
            <button type="button" onClick={reloadMes} className="text-sm text-accent underline">
              Reintentar datos del mes
            </button>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-5">
      <PageHeader title="Socios" subtitle="Distribución entre socios (sólo administración)" />
      <SociosContent settings={settingsState.data} mes={mesState.data} />
    </div>
  );
}
