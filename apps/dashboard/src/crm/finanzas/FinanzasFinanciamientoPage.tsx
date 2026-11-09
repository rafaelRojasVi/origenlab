/**
 * «Financiamiento» — factoring calculator and liquidity summary.
 *
 * Shows:
 * - Factorizable hoy: por-cobrar aging 0–30 d + 31–60 d (invoices ≤ 60 days old to companies)
 * - Cost simulator: amount × days × monthly rate → cost, net received
 * - Por cobrar breakdown by aging bucket
 * - Capital atado (supplier orders in flight)
 * - Ley 21.131 (Pago a 30 días) note
 * - FOGAPE note
 */
import { useMemo, useState } from "react";
import { formatMoney } from "../money";
import { EmptyState, PageHeader, ResourceGate, Skeleton, StatLine } from "../ui";
import { useResource } from "../useResource";
import { factoringCost } from "./financeCalc";
import type { MesData } from "./financeApi";
import { fetchFinanceMes } from "./financeApi";

const clp = (n: number) => formatMoney(String(Math.round(n)), "CLP") ?? "—";
const pct = (n: number) => `${(n * 100).toFixed(1)} %`;

const AGE_LABEL: Record<string, string> = {
  "0-30": "0–30 días",
  "31-60": "31–60 días",
  "61-90": "61–90 días",
  "90+": "+ 90 días",
};

// ── factoring simulator ───────────────────────────────────────────────────────

function FactoringSimulator({ factorizable }: { factorizable: number }) {
  const [amount, setAmount] = useState(String(Math.round(factorizable)));
  const [days, setDays] = useState("30");
  const [rate, setRate] = useState("1.5");
  const [fee, setFee] = useState("0");

  const result = useMemo(() => {
    const a = parseFloat(amount.replace(/\./g, "").replace(",", "."));
    const d = parseInt(days, 10);
    const r = parseFloat(rate.replace(",", ".")) / 100;
    const f = parseFloat(fee.replace(/\./g, "").replace(",", ".")) || 0;
    if (!a || !d || !r || a <= 0 || d <= 0 || r <= 0) return null;
    return factoringCost(a, d, r, f);
  }, [amount, days, rate, fee]);

  return (
    <div className="rounded-xl border border-line bg-sunken p-4">
      <p className="text-[13px] font-semibold text-ink">Simulador de factoring</p>
      <p className="mt-0.5 text-xs text-ink-muted">Factoring con recursos propios — costo estimado.</p>
      <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <label className="flex flex-col gap-1">
          <span className="text-[11px] font-medium text-ink-muted">Monto (CLP c/IVA)</span>
          <input
            type="text"
            inputMode="numeric"
            className="h-9 rounded-lg border border-line-strong bg-raised px-2.5 text-sm font-mono tabular-nums text-ink focus:border-accent focus:outline-none"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[11px] font-medium text-ink-muted">Días</span>
          <input
            type="number"
            min="1"
            max="90"
            className="h-9 rounded-lg border border-line-strong bg-raised px-2.5 text-sm font-mono tabular-nums text-ink focus:border-accent focus:outline-none"
            value={days}
            onChange={(e) => setDays(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[11px] font-medium text-ink-muted">Tasa mensual (%)</span>
          <input
            type="text"
            inputMode="decimal"
            className="h-9 rounded-lg border border-line-strong bg-raised px-2.5 text-sm font-mono tabular-nums text-ink focus:border-accent focus:outline-none"
            value={rate}
            onChange={(e) => setRate(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[11px] font-medium text-ink-muted">Fee fijo (CLP)</span>
          <input
            type="text"
            inputMode="numeric"
            className="h-9 rounded-lg border border-line-strong bg-raised px-2.5 text-sm font-mono tabular-nums text-ink focus:border-accent focus:outline-none"
            value={fee}
            onChange={(e) => setFee(e.target.value)}
          />
        </label>
      </div>
      {result ? (
        <div className="mt-3 grid grid-cols-3 gap-3 border-t border-line pt-3">
          <div>
            <p className="text-[11px] font-mono uppercase tracking-wide text-ink-faint">Costo</p>
            <p className="mt-0.5 font-mono text-sm font-semibold text-bad">{clp(result.cost)}</p>
            <p className="text-[10px] text-ink-faint">{pct(result.cost / (parseFloat(amount.replace(/\./g, "").replace(",", ".")) || 1))}</p>
          </div>
          <div>
            <p className="text-[11px] font-mono uppercase tracking-wide text-ink-faint">Neto recibido</p>
            <p className="mt-0.5 font-mono text-sm font-semibold text-good">{clp(result.netReceived)}</p>
          </div>
          <div>
            <p className="text-[11px] font-mono uppercase tracking-wide text-ink-faint">Tasa ef. mensual</p>
            <p className="mt-0.5 font-mono text-sm font-semibold text-ink">{pct(result.effectiveMonthlyRate)}</p>
          </div>
        </div>
      ) : (
        <p className="mt-3 text-xs text-ink-faint">Ingresa valores válidos para ver la simulación.</p>
      )}
    </div>
  );
}

// ── liquidity panel ───────────────────────────────────────────────────────────

function LiquidityPanel({ data }: { data: MesData }) {
  const byAge = data.por_cobrar.by_age;

  // Factorizable = invoices ≤ 60 days (0–30 + 31–60 buckets), con IVA
  const factorizable = (byAge["0-30"] ?? 0) + (byAge["31-60"] ?? 0);

  // All por-cobrar with IVA
  const totalConIva = data.por_cobrar.total;

  const ages = (["0-30", "31-60", "61-90", "90+"] as const).filter((b) => (byAge[b] ?? 0) > 0);

  return (
    <div className="flex flex-col gap-4">
      {/* Factorizable hoy */}
      <div className="rounded-xl border border-line bg-raised px-4 py-4">
        <p className="text-[11px] font-mono uppercase tracking-wide text-ink-muted">Factorizable hoy</p>
        <p className="mt-1 text-[26px] font-semibold tabular-nums leading-none text-ink">{clp(factorizable)}</p>
        <p className="mt-1 text-xs text-ink-muted">
          Facturas ≤ 60 días (0–30 d y 31–60 d) — susceptibles de factoring con recursos propios o banco.
        </p>
        {factorizable < totalConIva && (
          <p className="mt-1 text-xs text-ink-faint">
            {clp(totalConIva - factorizable)} en facturas &gt;60 d no factorizables sin descuento adicional.
          </p>
        )}
      </div>

      {/* Aging breakdown */}
      {ages.length > 0 ? (
        <div className="rounded-xl border border-line bg-sunken px-4 py-3">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-muted">Por cobrar por antigüedad</p>
          <div className="mt-2 flex flex-col gap-1.5">
            {ages.map((bucket) => {
              const val = byAge[bucket] ?? 0;
              const barPct = totalConIva > 0 ? Math.round((val / totalConIva) * 100) : 0;
              const tone = bucket === "0-30" ? "good" : bucket === "31-60" ? "warn" : "bad";
              return (
                <div key={bucket} className="flex items-center gap-2">
                  <span className="w-16 shrink-0 text-xs text-ink-muted">{AGE_LABEL[bucket] ?? bucket}</span>
                  <div className="flex-1 overflow-hidden rounded-full bg-canvas h-2">
                    <div
                      className={`h-full rounded-full ${tone === "good" ? "bg-good" : tone === "warn" ? "bg-warn" : "bg-bad"}`}
                      style={{ width: `${barPct}%` }}
                      aria-label={`${barPct}%`}
                    />
                  </div>
                  <span className="w-28 shrink-0 text-right text-xs font-mono tabular-nums text-ink">{clp(val)}</span>
                </div>
              );
            })}
          </div>
        </div>
      ) : null}

      {/* Capital atado */}
      <div className="grid grid-cols-2 gap-3">
        <div className="rounded-xl border border-line bg-raised px-4 py-3">
          <p className="text-[11px] font-mono uppercase tracking-wide text-ink-muted">Capital atado (pedidos)</p>
          <p className="mt-1 text-[22px] font-semibold tabular-nums leading-none text-ink">{clp(data.capital_atado.total)}</p>
          <p className="mt-0.5 text-xs text-ink-muted">{data.capital_atado.count} pedido{data.capital_atado.count === 1 ? "" : "s"} en tránsito</p>
        </div>
        <div className="rounded-xl border border-line bg-raised px-4 py-3">
          <p className="text-[11px] font-mono uppercase tracking-wide text-ink-muted">Gastos mes</p>
          <p className="mt-1 text-[22px] font-semibold tabular-nums leading-none text-ink">{clp(data.gastos.total)}</p>
          <p className="mt-0.5 text-xs text-ink-muted">{data.gastos.count} ítem{data.gastos.count === 1 ? "" : "s"} registrado{data.gastos.count === 1 ? "" : "s"}</p>
        </div>
      </div>

      {/* Factoring simulator */}
      <FactoringSimulator factorizable={factorizable} />

      {/* Legal notes */}
      <div className="rounded-xl border border-line/60 bg-sunken/40 px-4 py-3 text-xs text-ink-muted space-y-2">
        <p>
          <strong className="font-semibold text-ink">Ley 21.131 — Pago a 30 días.</strong>{" "}
          Desde el 1 de enero de 2020, las facturas emitidas a grandes empresas vencen en 30 días corridos.
          Para PYMES el plazo máximo es también 30 días. El incumplimiento da derecho a cobro de intereses y gastos de cobranza.
          Fuente: Ministerio de Economía, Fomento y Turismo.
        </p>
        <p>
          <strong className="font-semibold text-ink">FOGAPE.</strong>{" "}
          El Fondo de Garantía para Pequeños Empresarios permite a PYMES acceder a crédito bancario con garantía estatal de hasta el
          80 % del capital. Contacta tu banco comercial para solicitar una línea FOGAPE. Aplica cuando el capital de trabajo requerido
          supera la liquidez inmediata.
        </p>
      </div>
    </div>
  );
}

// ── main page ─────────────────────────────────────────────────────────────────

export function FinanzasFinanciamientoPage() {
  const [state, reload] = useResource(fetchFinanceMes);

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Financiamiento"
        subtitle="Factorizable hoy, capital atado y herramientas de liquidez"
      />
      <ResourceGate
        state={state}
        reload={reload}
        skeleton={<Skeleton rows={4} />}
        errorTitle="No se pudo leer Finanzas"
      >
        {(data) => {
          const factorizable = (data.por_cobrar.by_age["0-30"] ?? 0) + (data.por_cobrar.by_age["31-60"] ?? 0);
          return (
            <>
              <StatLine
                items={[
                  { label: "Por cobrar (c/IVA)", value: clp(data.por_cobrar.total) },
                  { label: "Factorizable hoy", value: clp(factorizable) },
                  { label: "Capital atado", value: clp(data.capital_atado.total) },
                ]}
              />
              {data.por_cobrar.total === 0 && data.capital_atado.total === 0 ? (
                <EmptyState title="Sin compromisos financieros activos">
                  Cuando haya facturas por cobrar o pedidos en tránsito aparecerán aquí.
                </EmptyState>
              ) : (
                <LiquidityPanel data={data} />
              )}
            </>
          );
        }}
      </ResourceGate>
    </div>
  );
}
