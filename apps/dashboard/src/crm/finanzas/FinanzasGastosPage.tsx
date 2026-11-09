/**
 * «Gastos» — operating expenses for the selected calendar month, with full CRUD.
 *
 * Features:
 * - Month selector (default: current Santiago month)
 * - Add / edit / soft-delete expenses via upsert-expense command
 * - Totals by category
 * - CSV export
 * - Loading / empty / error states
 */
import { useRef, useState } from "react";
import { newCaseCommandKey } from "../caseCommands";
import { formatMoney } from "../money";
import {
  Button,
  Drawer,
  EmptyState,
  FormField,
  PageHeader,
  ResourceGate,
  SelectInput,
  Skeleton,
  StatLine,
  TextareaInput,
  fmtDate,
  fmtInt,
  toast,
} from "../ui";
import { useResource } from "../useResource";
import type { Expense } from "./financeApi";
import {
  fetchFinanceExpenses,
  upsertExpense,
} from "./financeApi";

const clp = (n: number) => formatMoney(String(n), "CLP") ?? "—";

// ── labels ────────────────────────────────────────────────────────────────────

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

const CATEGORIES = Object.entries(CATEGORY_LABEL).map(([v, l]) => ({ value: v, label: l }));

const PAID_BY_LABEL: Record<string, string> = {
  empresa: "Empresa",
  tatiana: "Tatiana",
  sebastian: "Sebastián",
};

const PAID_BY_OPTIONS = Object.entries(PAID_BY_LABEL).map(([v, l]) => ({ value: v, label: l }));

// ── month helpers ─────────────────────────────────────────────────────────────

function currentMonthValue(): string {
  // Returns "YYYY-MM" for the current Santiago month
  const now = new Intl.DateTimeFormat("en-CA", { timeZone: "America/Santiago", year: "numeric", month: "2-digit" }).format(new Date());
  return now; // "YYYY-MM"
}

function monthInputToFirstDay(ym: string): string {
  return `${ym}-01`;
}

function monthLabel(ym: string): string {
  const MONTHS = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"];
  const [, m] = ym.split("-").map(Number);
  const [y] = ym.split("-").map(Number);
  return `${MONTHS[(m ?? 1) - 1] ?? ""} ${y}`;
}

// ── CSV export ────────────────────────────────────────────────────────────────

function exportCsv(items: Expense[], month: string) {
  const rows = [
    ["ID", "Mes", "Categoría", "Monto CLP", "Pagado por", "Notas", "Fuente"],
    ...items.map((e) => [
      e.expense_id,
      e.month,
      CATEGORY_LABEL[e.category] ?? e.category,
      String(e.amount_clp),
      PAID_BY_LABEL[e.paid_by] ?? e.paid_by,
      e.notes ?? "",
      e.source,
    ]),
  ];
  const csv = rows.map((r) => r.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(",")).join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `gastos-${month}.csv`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

// ── expense form ──────────────────────────────────────────────────────────────

interface FormState {
  expense_id?: string;
  month: string;    // "YYYY-MM"
  category: string;
  amount_clp: string;
  paid_by: string;
  expense_notes: string;
}

function blankForm(month: string): FormState {
  return { month, category: "otros", amount_clp: "", paid_by: "empresa", expense_notes: "" };
}

function ExpenseForm({
  initial,
  onDone,
}: {
  initial: FormState;
  onDone: () => void;
}) {
  const [form, setForm] = useState<FormState>(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const keyRef = useRef(newCaseCommandKey());

  const set = (k: keyof FormState) => (v: string) => setForm((f) => ({ ...f, [k]: v }));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const amount = Number(form.amount_clp.replace(/\./g, "").replace(",", "."));
    if (!amount || amount <= 0) {
      setError("El monto debe ser mayor que 0.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await upsertExpense(
        {
          ...(form.expense_id ? { expense_id: form.expense_id } : {}),
          month: monthInputToFirstDay(form.month),
          category: form.category,
          amount_clp: Math.round(amount),
          paid_by: form.paid_by,
          expense_notes: form.expense_notes.trim() || undefined,
        },
        keyRef.current,
      );
      toast(form.expense_id ? "Gasto actualizado" : "Gasto registrado");
      onDone();
    } catch (err) {
      keyRef.current = newCaseCommandKey();
      setError(err instanceof Error ? err.message : "Error al guardar");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={(e) => void submit(e)} className="flex flex-col gap-4 p-4">
      <FormField label="Mes" htmlFor="exp-month" hint="Mes al que corresponde el gasto">
        <input
          type="month"
          id="exp-month"
          className="h-10 w-full rounded-lg border border-line-strong bg-raised px-3 text-sm text-ink focus:border-accent focus:outline-none"
          value={form.month}
          onChange={(e) => set("month")(e.target.value)}
          required
          disabled={busy}
        />
      </FormField>
      <FormField label="Categoría" htmlFor="exp-cat" required>
        <SelectInput id="exp-cat" value={form.category} onChange={set("category")} disabled={busy} options={CATEGORIES} />
      </FormField>
      <FormField label="Monto (CLP)" htmlFor="exp-amount" required hint="Sin puntos ni comas">
        <input
          id="exp-amount"
          type="number"
          min="1"
          step="1"
          value={form.amount_clp}
          onChange={(e) => set("amount_clp")(e.target.value)}
          disabled={busy}
          placeholder="Ej: 450000"
          required
          className="w-full min-w-0 rounded-md border border-line bg-canvas-raised px-2.5 py-1.5 text-xs text-ink placeholder:text-ink-faint focus:border-brand-600 focus:outline-none focus:ring-1 focus:ring-brand-600 disabled:cursor-not-allowed disabled:opacity-50"
        />
      </FormField>
      <FormField label="Pagado por" htmlFor="exp-paid-by">
        <SelectInput id="exp-paid-by" value={form.paid_by} onChange={set("paid_by")} disabled={busy} options={PAID_BY_OPTIONS} />
      </FormField>
      <FormField label="Notas" htmlFor="exp-notes">
        <TextareaInput id="exp-notes" value={form.expense_notes} onChange={set("expense_notes")} disabled={busy} rows={2} placeholder="Opcional" />
      </FormField>
      {error && <p className="text-sm text-bad">{error}</p>}
      <div className="flex gap-2">
        <Button type="submit" variant="primary" disabled={busy}>{busy ? "Guardando…" : form.expense_id ? "Actualizar" : "Registrar gasto"}</Button>
      </div>
    </form>
  );
}

// ── main page ─────────────────────────────────────────────────────────────────

export function FinanzasGastosPage() {
  const [selectedMonth, setSelectedMonth] = useState(currentMonthValue);
  const month = monthInputToFirstDay(selectedMonth);
  const [state, reload] = useResource(() => fetchFinanceExpenses(month), [month]);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<FormState | null>(null);

  function openCreate() {
    setEditing(blankForm(selectedMonth));
    setDrawerOpen(true);
  }

  function openEdit(exp: Expense) {
    setEditing({
      expense_id: exp.expense_id,
      month: exp.month.slice(0, 7),
      category: exp.category,
      amount_clp: String(exp.amount_clp),
      paid_by: exp.paid_by,
      expense_notes: exp.notes ?? "",
    });
    setDrawerOpen(true);
  }

  function onDone() {
    setDrawerOpen(false);
    reload();
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <PageHeader title="Gastos" subtitle="Gastos operativos del mes registrados manualmente" />
        <div className="flex flex-wrap items-center gap-2">
          <div>
            <label htmlFor="gastos-month" className="sr-only">Mes</label>
            <input
              type="month"
              id="gastos-month"
              className="h-10 rounded-lg border border-line-strong bg-raised px-3 text-sm text-ink focus:border-accent focus:outline-none"
              value={selectedMonth}
              onChange={(e) => setSelectedMonth(e.target.value)}
            />
          </div>
          {state.kind === "ready" && state.data.items.length > 0 && (
            <Button type="button" variant="secondary" onClick={() => exportCsv(state.data.items, selectedMonth)}>
              Exportar CSV
            </Button>
          )}
          <Button type="button" variant="primary" onClick={openCreate}>
            + Registrar gasto
          </Button>
        </div>
      </div>

      <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={4} />} errorTitle="No se pudo leer gastos">
        {(data) => (
          <>
            <StatLine
              items={[
                { label: "Total mes", value: clp(data.total_clp) },
                { label: "Mes", value: monthLabel(selectedMonth) },
                { label: "Ítems", value: fmtInt(data.items.length) },
              ]}
            />
            {data.items.length === 0 ? (
              <EmptyState title="Sin gastos registrados este mes">
                <button type="button" onClick={openCreate} className="mt-2 text-sm font-semibold text-accent underline underline-offset-2">
                  Registrar el primer gasto
                </button>
              </EmptyState>
            ) : (
              <>
                {/* Category totals */}
                <CategoryTotals items={data.items} />
                {/* Full list */}
                <div className="overflow-x-auto">
                  <table className="min-w-full text-left">
                    <thead>
                      <tr className="text-[11px] font-mono uppercase tracking-wide text-ink-faint">
                        <th scope="col" className="pb-2 pr-3 font-medium">Categoría</th>
                        <th scope="col" className="pb-2 pr-3 font-medium">Mes</th>
                        <th scope="col" className="pb-2 pr-3 text-right font-medium">Monto</th>
                        <th scope="col" className="pb-2 pr-3 font-medium">Paga</th>
                        <th scope="col" className="pb-2 pr-3 font-medium">Notas</th>
                        <th scope="col" className="pb-2 font-medium" />
                      </tr>
                    </thead>
                    <tbody>
                      {data.items.map((e) => (
                        <tr key={e.expense_id} className="border-t border-line last:border-b hover:bg-sunken">
                          <td className="py-2 pr-3 text-sm font-medium text-ink">{CATEGORY_LABEL[e.category] ?? e.category}</td>
                          <td className="py-2 pr-3 text-xs text-ink-muted">{fmtDate(e.month)}</td>
                          <td className="py-2 pr-3 text-right text-sm font-mono font-medium tabular-nums text-ink">{clp(e.amount_clp)}</td>
                          <td className="py-2 pr-3 text-xs text-ink-muted">{PAID_BY_LABEL[e.paid_by] ?? e.paid_by}</td>
                          <td className="py-2 pr-3 text-xs text-ink-muted">{e.notes ?? "—"}</td>
                          <td className="py-2 text-right">
                            <button
                              type="button"
                              onClick={() => openEdit(e)}
                              className="text-xs font-semibold text-accent hover:underline"
                            >
                              Editar
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                    <tfoot>
                      <tr className="border-t border-line-strong">
                        <td className="py-2 pr-3 text-sm font-bold text-ink">Total</td>
                        <td />
                        <td className="py-2 pr-3 text-right text-sm font-mono font-bold text-ink">{clp(data.total_clp)}</td>
                        <td colSpan={3} />
                      </tr>
                    </tfoot>
                  </table>
                </div>
              </>
            )}
          </>
        )}
      </ResourceGate>

      <Drawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        title={editing?.expense_id ? "Editar gasto" : "Registrar gasto"}
        subtitle={monthLabel(selectedMonth)}
      >
        {editing && <ExpenseForm initial={editing} onDone={onDone} />}
      </Drawer>
    </div>
  );
}

function CategoryTotals({ items }: { items: Expense[] }) {
  const totals: Record<string, number> = {};
  for (const e of items) totals[e.category] = (totals[e.category] ?? 0) + e.amount_clp;
  const sorted = Object.entries(totals).sort((a, b) => b[1] - a[1]);
  if (sorted.length <= 1) return null;
  return (
    <div className="flex flex-wrap gap-2">
      {sorted.map(([cat, total]) => (
        <div key={cat} className="rounded-lg border border-line bg-sunken px-3 py-2">
          <p className="text-[11px] font-mono uppercase tracking-wide text-ink-faint">{CATEGORY_LABEL[cat] ?? cat}</p>
          <p className="mt-0.5 font-mono text-sm font-medium text-ink">{clp(total)}</p>
        </div>
      ))}
    </div>
  );
}
