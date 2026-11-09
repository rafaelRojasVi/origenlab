/**
 * «Financiamiento» — una vista de resumen financiero que combina por cobrar, capital atado
 * y gastos en un cuadro de liquidez simplificado.
 *
 * Muestra: ingresos esperados (facturas por cobrar), capital comprometido (pedidos en tránsito)
 * y gastos del mes. Es una lectura sencilla de los mismos datos del Mes, pensada para revisar
 * la posición de caja del mes rápidamente.
 */
import { formatMoney } from "../money";
import { EmptyState, PageHeader, ResourceGate, Skeleton, StatLine } from "../ui";
import { useResource } from "../useResource";
import { fetchFinanceMes } from "./financeApi";

const clp = (n: number) => formatMoney(String(n), "CLP") ?? "—";

export function FinanzasFinanciamientoPage() {
  const [state, reload] = useResource(fetchFinanceMes);

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Financiamiento"
        subtitle="Posición de liquidez del mes: ingresos esperados vs. compromisos"
      />
      <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={3} />} errorTitle="No se pudo leer Finanzas">
        {(data) => (
          <>
            <StatLine
              items={[
                { label: "Por cobrar", value: clp(data.por_cobrar.total) },
                { label: "Capital atado", value: clp(data.capital_atado.total) },
                { label: "Gastos mes", value: clp(data.gastos.total) },
              ]}
            />
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <article className="rounded-lg border border-line bg-surface-raised px-4 py-3">
                <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-muted">Por cobrar</p>
                <p className="mt-2 text-[22px] font-semibold tabular-nums leading-none text-ink">
                  {clp(data.por_cobrar.total)}
                </p>
                <p className="mt-1 text-xs text-ink-muted">
                  {data.por_cobrar.count} factura{data.por_cobrar.count === 1 ? "" : "s"} pendiente{data.por_cobrar.count === 1 ? "" : "s"}
                </p>
              </article>
              <article className="rounded-lg border border-line bg-surface-raised px-4 py-3">
                <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-muted">Capital comprometido</p>
                <p className="mt-2 text-[22px] font-semibold tabular-nums leading-none text-ink">
                  {clp(data.capital_atado.total)}
                </p>
                <p className="mt-1 text-xs text-ink-muted">
                  {data.capital_atado.count} pedido{data.capital_atado.count === 1 ? "" : "s"} en tránsito
                </p>
              </article>
              <article className="rounded-lg border border-line bg-surface-raised px-4 py-3">
                <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-muted">Gastos mes</p>
                <p className="mt-2 text-[22px] font-semibold tabular-nums leading-none text-ink">
                  {clp(data.gastos.total)}
                </p>
                <p className="mt-1 text-xs text-ink-muted">
                  {data.gastos.count} ítem{data.gastos.count === 1 ? "" : "s"} registrado{data.gastos.count === 1 ? "" : "s"}
                </p>
              </article>
            </div>
          </>
        )}
      </ResourceGate>
    </div>
  );
}
