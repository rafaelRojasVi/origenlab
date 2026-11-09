/**
 * «Gastos» — operating expenses for the current calendar month, grouped by category.
 *
 * The month selector changes which month's expenses are shown. Expenses are entered manually
 * (source='manual'); future SII purchase-document imports will add source='sii_purchase' rows.
 * Each row: category, amount, paid_by, notes.
 */
import { formatMoney } from "../money";
import { EmptyState, PageHeader, ResourceGate, Skeleton, StatLine, fmtInt } from "../ui";
import { useResource } from "../useResource";
import type { Expense } from "./financeApi";
import { fetchFinanceExpenses } from "./financeApi";

const clp = (n: number) => formatMoney(String(n), "CLP") ?? "—";

const CATEGORY_LABEL: Record<string, string> = {
  arriendo: "Arriendo",
  servidores: "Servidores",
  contador: "Contador",
  telefono: "Teléfono",
  software: "Software",
  viajes: "Viajes",
  sueldos: "Sueldos",
  honorarios: "Honorarios",
  otros: "Otros",
};

const PAID_BY_LABEL: Record<string, string> = {
  empresa: "Empresa",
  tatiana: "Tatiana",
  sebastian: "Sebastián",
};

function ExpenseRow({ expense }: { expense: Expense }) {
  return (
    <tr className="border-t border-line last:border-b">
      <td className="py-2 pr-3 text-sm text-ink">{CATEGORY_LABEL[expense.category] ?? expense.category}</td>
      <td className="py-2 pr-3 text-right text-sm font-medium tabular-nums text-ink">{clp(expense.amount_clp)}</td>
      <td className="py-2 pr-3 text-xs text-ink-muted">{PAID_BY_LABEL[expense.paid_by] ?? expense.paid_by}</td>
      <td className="py-2 text-xs text-ink-muted">{expense.notes ?? "—"}</td>
    </tr>
  );
}

export function FinanzasGastosPage() {
  const [state, reload] = useResource(fetchFinanceExpenses);

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Gastos"
        subtitle="Gastos operativos del mes registrados manualmente"
      />
      <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={4} />} errorTitle="No se pudo leer gastos">
        {(data) => (
          <>
            <StatLine
              items={[
                { label: "Total mes", value: clp(data.total_clp) },
                { label: "Ítems", value: fmtInt(data.items.length) },
              ]}
            />
            {data.items.length === 0 ? (
              <EmptyState title="Sin gastos registrados este mes">
                Los gastos del mes se ingresan desde Ajustes o desde esta pantalla.
              </EmptyState>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-full text-left">
                  <thead>
                    <tr className="text-xs text-ink-faint">
                      <th scope="col" className="pb-1.5 pr-3 font-medium">Categoría</th>
                      <th scope="col" className="pb-1.5 pr-3 text-right font-medium">Monto</th>
                      <th scope="col" className="pb-1.5 pr-3 font-medium">Paga</th>
                      <th scope="col" className="pb-1.5 font-medium">Notas</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.map((e) => <ExpenseRow key={e.expense_id} expense={e} />)}
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
