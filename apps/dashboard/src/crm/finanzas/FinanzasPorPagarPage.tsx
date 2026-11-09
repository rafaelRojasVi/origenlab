/**
 * «Por pagar y pedidos» — supplier orders placed but not yet received or not yet paid.
 *
 * Shows all supplier orders (most recent first). In-flight orders (received_on is null) are
 * flagged. The total capital atado (CLP) is shown in the header. Each row: supplier, ordered_on,
 * expected_arrival_on, amount in original currency and CLP equivalent, case link.
 */
import { formatMoney } from "../money";
import { EmptyState, PageHeader, ResourceGate, Skeleton, StatLine, fmtDate, fmtInt } from "../ui";
import { useResource } from "../useResource";
import type { SupplierOrder } from "./financeApi";
import { fetchFinanceSupplierOrders } from "./financeApi";

const clp = (n: number | null) => (n != null ? (formatMoney(String(n), "CLP") ?? "—") : "—");
const money = (n: number, ccy: string) => formatMoney(String(n), ccy) ?? "—";

function OrderRow({ order }: { order: SupplierOrder }) {
  const inFlight = order.received_on == null;
  return (
    <tr className="border-t border-line last:border-b">
      <td className="py-2 pr-3 text-sm font-medium text-ink">{order.supplier_name}</td>
      <td className="py-2 pr-3 text-xs text-ink-muted">{fmtDate(order.ordered_on)}</td>
      <td className="py-2 pr-3 text-xs text-ink-muted">
        {order.expected_arrival_on ? fmtDate(order.expected_arrival_on) : "—"}
      </td>
      <td className="py-2 pr-3 text-right text-xs tabular-nums text-ink-muted">
        {order.currency !== "CLP" ? money(order.amount_original, order.currency) : "—"}
      </td>
      <td className="py-2 pr-3 text-right text-sm font-medium tabular-nums text-ink">{clp(order.amount_clp)}</td>
      <td className="py-2 pr-3 text-xs text-ink-muted">
        {order.opportunity_title ?? "—"}
      </td>
      <td className="py-2 text-xs">
        {inFlight ? (
          <span className="rounded-full bg-warn-bg px-1.5 py-0.5 text-warn">En tránsito</span>
        ) : (
          <span className="text-ink-faint">Recibido {fmtDate(order.received_on)}</span>
        )}
      </td>
    </tr>
  );
}

export function FinanzasPorPagarPage() {
  const [state, reload] = useResource(fetchFinanceSupplierOrders);

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Por pagar y pedidos"
        subtitle="Órdenes a proveedores pendientes de recepción o pago"
      />
      <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={4} />} errorTitle="No se pudo leer pedidos">
        {(data) => (
          <>
            <StatLine
              items={[
                { label: "Capital atado", value: formatMoney(String(data.in_flight_total), "CLP") ?? "—" },
                { label: "Pedidos en tránsito", value: fmtInt(data.in_flight_count) },
                { label: "Total pedidos", value: fmtInt(data.items.length) },
              ]}
            />
            {data.items.length === 0 ? (
              <EmptyState title="Sin pedidos registrados">
                Registra un pedido a proveedor desde un caso o directamente.
              </EmptyState>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-full text-left">
                  <thead>
                    <tr className="text-xs text-ink-faint">
                      <th scope="col" className="pb-1.5 pr-3 font-medium">Proveedor</th>
                      <th scope="col" className="pb-1.5 pr-3 font-medium">Fecha pedido</th>
                      <th scope="col" className="pb-1.5 pr-3 font-medium">Llegada est.</th>
                      <th scope="col" className="pb-1.5 pr-3 text-right font-medium">Monto orig.</th>
                      <th scope="col" className="pb-1.5 pr-3 text-right font-medium">CLP</th>
                      <th scope="col" className="pb-1.5 pr-3 font-medium">Caso</th>
                      <th scope="col" className="pb-1.5 font-medium">Estado</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.map((o) => <OrderRow key={o.order_id} order={o} />)}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </ResourceGate>
    </div>
  );
}
