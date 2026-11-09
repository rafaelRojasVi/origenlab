/**
 * «Por pagar y pedidos» — supplier orders with full CRUD.
 *
 * Features:
 * - Capital atado total header (in-flight orders only)
 * - Add / edit supplier orders via upsert-supplier-order command
 * - «Marcar recibido» button on in-flight rows
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
  TextInput,
  fmtDate,
  fmtInt,
  toast,
} from "../ui";
import { useResource } from "../useResource";
import type { SupplierOrder } from "./financeApi";
import { fetchFinanceSupplierOrders, upsertSupplierOrder } from "./financeApi";

const clp = (n: number | null) => (n != null ? (formatMoney(String(n), "CLP") ?? "—") : "—");
const money = (n: number, ccy: string) => formatMoney(String(n), ccy) ?? "—";

// ── currency options ──────────────────────────────────────────────────────────

const CURRENCY_OPTIONS = [
  { value: "CLP", label: "CLP — Pesos" },
  { value: "USD", label: "USD — Dólares" },
  { value: "EUR", label: "EUR — Euros" },
  { value: "CNY", label: "CNY — Yuan" },
  { value: "JPY", label: "JPY — Yen" },
];

// ── form state ────────────────────────────────────────────────────────────────

interface FormState {
  order_id?: string;
  supplier_name: string;
  ordered_on: string;
  paid_on: string;
  amount_original: string;
  currency: string;
  amount_clp: string;
  expected_arrival_on: string;
  received_on: string;
  order_notes: string;
}

function todayStr(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/Santiago" }).format(new Date());
}

function blankForm(): FormState {
  return {
    supplier_name: "",
    ordered_on: todayStr(),
    paid_on: "",
    amount_original: "",
    currency: "USD",
    amount_clp: "",
    expected_arrival_on: "",
    received_on: "",
    order_notes: "",
  };
}

function orderToForm(o: SupplierOrder): FormState {
  return {
    order_id: o.order_id,
    supplier_name: o.supplier_name,
    ordered_on: o.ordered_on,
    paid_on: o.paid_on ?? "",
    amount_original: String(o.amount_original),
    currency: o.currency,
    amount_clp: o.amount_clp != null ? String(o.amount_clp) : "",
    expected_arrival_on: o.expected_arrival_on ?? "",
    received_on: o.received_on ?? "",
    order_notes: o.notes ?? "",
  };
}

// ── order form ────────────────────────────────────────────────────────────────

function OrderForm({ initial, onDone }: { initial: FormState; onDone: () => void }) {
  const [form, setForm] = useState<FormState>(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const keyRef = useRef(newCaseCommandKey());

  const set = (k: keyof FormState) => (v: string) => setForm((f) => ({ ...f, [k]: v }));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const amount = parseFloat(form.amount_original.replace(",", "."));
    if (!amount || amount <= 0) {
      setError("El monto debe ser mayor que 0.");
      return;
    }
    if (!form.supplier_name.trim()) {
      setError("Ingresa el nombre del proveedor.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await upsertSupplierOrder(
        {
          ...(form.order_id ? { order_id: form.order_id } : {}),
          supplier_name: form.supplier_name.trim(),
          ordered_on: form.ordered_on,
          ...(form.paid_on ? { paid_on: form.paid_on } : {}),
          amount_original: amount,
          currency: form.currency,
          ...(form.amount_clp ? { amount_clp: Math.round(parseFloat(form.amount_clp.replace(",", "."))) } : {}),
          ...(form.expected_arrival_on ? { expected_arrival_on: form.expected_arrival_on } : {}),
          ...(form.received_on ? { received_on: form.received_on } : {}),
          ...(form.order_notes.trim() ? { order_notes: form.order_notes.trim() } : {}),
        },
        keyRef.current,
      );
      toast(form.order_id ? "Pedido actualizado" : "Pedido registrado");
      onDone();
    } catch (err) {
      keyRef.current = newCaseCommandKey();
      setError(err instanceof Error ? err.message : "Error al guardar");
    } finally {
      setBusy(false);
    }
  }

  const NUM = "w-full min-w-0 rounded-md border border-line bg-canvas-raised px-2.5 py-1.5 text-xs text-ink placeholder:text-ink-faint focus:border-brand-600 focus:outline-none focus:ring-1 focus:ring-brand-600 disabled:cursor-not-allowed disabled:opacity-50";

  return (
    <form onSubmit={(e) => void submit(e)} className="flex flex-col gap-4 p-4">
      <FormField label="Proveedor" htmlFor="ord-supplier" required>
        <TextInput id="ord-supplier" value={form.supplier_name} onChange={set("supplier_name")} disabled={busy} required placeholder="Ej: Hielscher GmbH" />
      </FormField>
      <FormField label="Fecha del pedido" htmlFor="ord-date" required>
        <TextInput id="ord-date" type="date" value={form.ordered_on} onChange={set("ordered_on")} disabled={busy} required />
      </FormField>
      <div className="flex gap-2">
        <FormField label={`Monto (${form.currency})`} htmlFor="ord-amount" required>
          <input id="ord-amount" type="number" min="0.01" step="any" value={form.amount_original} onChange={(e) => set("amount_original")(e.target.value)} disabled={busy} required placeholder="Ej: 3200" className={NUM} />
        </FormField>
        <FormField label="Moneda" htmlFor="ord-currency">
          <SelectInput id="ord-currency" value={form.currency} onChange={set("currency")} disabled={busy} options={CURRENCY_OPTIONS} />
        </FormField>
      </div>
      {form.currency !== "CLP" && (
        <FormField label="Equivalente CLP" htmlFor="ord-clp" hint="Para calcular capital atado">
          <input id="ord-clp" type="number" min="0" step="1" value={form.amount_clp} onChange={(e) => set("amount_clp")(e.target.value)} disabled={busy} placeholder="Ej: 2850000" className={NUM} />
        </FormField>
      )}
      <FormField label="Llegada estimada" htmlFor="ord-arrival">
        <TextInput id="ord-arrival" type="date" value={form.expected_arrival_on} onChange={set("expected_arrival_on")} disabled={busy} />
      </FormField>
      <FormField label="Fecha de pago" htmlFor="ord-paid" hint="Deja vacío si aún no está pagado">
        <TextInput id="ord-paid" type="date" value={form.paid_on} onChange={set("paid_on")} disabled={busy} />
      </FormField>
      <FormField label="Fecha de recepción" htmlFor="ord-recv" hint="Deja vacío si aún en tránsito">
        <TextInput id="ord-recv" type="date" value={form.received_on} onChange={set("received_on")} disabled={busy} />
      </FormField>
      <FormField label="Notas" htmlFor="ord-notes">
        <TextareaInput id="ord-notes" value={form.order_notes} onChange={set("order_notes")} disabled={busy} rows={2} placeholder="Número de orden, link, detalles…" />
      </FormField>
      {error && <p className="text-sm text-bad">{error}</p>}
      <Button type="submit" variant="primary" disabled={busy}>
        {busy ? "Guardando…" : form.order_id ? "Actualizar pedido" : "Registrar pedido"}
      </Button>
    </form>
  );
}

// ── mark received helper ──────────────────────────────────────────────────────

function MarkReceivedButton({ order, onDone }: { order: SupplierOrder; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const keyRef = useRef(newCaseCommandKey());

  async function handleClick() {
    setBusy(true);
    try {
      await upsertSupplierOrder(
        {
          order_id: order.order_id,
          supplier_name: order.supplier_name,
          ordered_on: order.ordered_on,
          amount_original: order.amount_original,
          currency: order.currency,
          received_on: todayStr(),
        },
        keyRef.current,
      );
      toast("Pedido marcado como recibido");
      onDone();
    } catch (err) {
      keyRef.current = newCaseCommandKey();
      toast(err instanceof Error ? err.message : "Error al marcar recibido", "bad");
    } finally {
      setBusy(false);
    }
  }

  return (
    <button
      type="button"
      disabled={busy}
      onClick={() => void handleClick()}
      className="text-xs font-semibold text-good hover:underline disabled:opacity-50"
    >
      {busy ? "…" : "Recibido"}
    </button>
  );
}

// ── order row ─────────────────────────────────────────────────────────────────

function OrderRow({
  order,
  onEdit,
  onDone,
}: {
  order: SupplierOrder;
  onEdit: (o: SupplierOrder) => void;
  onDone: () => void;
}) {
  const inFlight = order.received_on == null;
  return (
    <tr className="border-t border-line last:border-b hover:bg-sunken">
      <td className="py-2 pr-3 text-sm font-medium text-ink">{order.supplier_name}</td>
      <td className="py-2 pr-3 text-xs text-ink-muted">{fmtDate(order.ordered_on)}</td>
      <td className="py-2 pr-3 text-xs text-ink-muted">
        {order.expected_arrival_on ? fmtDate(order.expected_arrival_on) : "—"}
      </td>
      <td className="py-2 pr-3 text-right text-xs tabular-nums text-ink-muted">
        {order.currency !== "CLP" ? money(order.amount_original, order.currency) : "—"}
      </td>
      <td className="py-2 pr-3 text-right text-sm font-mono font-medium tabular-nums text-ink">{clp(order.amount_clp)}</td>
      <td className="py-2 pr-3 text-xs text-ink-muted">{order.opportunity_title ?? "—"}</td>
      <td className="py-2 pr-3 text-xs">
        {inFlight ? (
          <span className="rounded-full bg-warn/10 px-1.5 py-0.5 text-[11px] font-medium text-warn">En tránsito</span>
        ) : (
          <span className="text-ink-faint">Recibido {fmtDate(order.received_on)}</span>
        )}
      </td>
      <td className="py-2 text-right">
        <div className="flex items-center justify-end gap-2">
          {inFlight && <MarkReceivedButton order={order} onDone={onDone} />}
          <button
            type="button"
            onClick={() => onEdit(order)}
            className="text-xs font-semibold text-accent hover:underline"
          >
            Editar
          </button>
        </div>
      </td>
    </tr>
  );
}

// ── main page ─────────────────────────────────────────────────────────────────

export function FinanzasPorPagarPage() {
  const [state, reload] = useResource(fetchFinanceSupplierOrders);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<FormState | null>(null);

  function openCreate() {
    setEditing(blankForm());
    setDrawerOpen(true);
  }

  function openEdit(o: SupplierOrder) {
    setEditing(orderToForm(o));
    setDrawerOpen(true);
  }

  function onDone() {
    setDrawerOpen(false);
    reload();
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <PageHeader
          title="Por pagar y pedidos"
          subtitle="Órdenes a proveedores pendientes de recepción o pago"
        />
        <Button type="button" variant="primary" onClick={openCreate}>
          + Registrar pedido
        </Button>
      </div>
      <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={4} />} errorTitle="No se pudo leer pedidos">
        {(data) => (
          <>
            <StatLine
              items={[
                { label: "Capital atado (en tránsito)", value: formatMoney(String(data.in_flight_total), "CLP") ?? "—" },
                { label: "Pedidos en tránsito", value: fmtInt(data.in_flight_count) },
                { label: "Total pedidos", value: fmtInt(data.items.length) },
              ]}
            />
            {data.items.length === 0 ? (
              <EmptyState title="Sin pedidos registrados">
                <button type="button" onClick={openCreate} className="mt-2 text-sm font-semibold text-accent underline underline-offset-2">
                  Registrar el primer pedido
                </button>
              </EmptyState>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-full text-left">
                  <thead>
                    <tr className="text-[11px] font-mono uppercase tracking-wide text-ink-faint">
                      <th scope="col" className="pb-2 pr-3 font-medium">Proveedor</th>
                      <th scope="col" className="pb-2 pr-3 font-medium">Pedido</th>
                      <th scope="col" className="pb-2 pr-3 font-medium">Llegada est.</th>
                      <th scope="col" className="pb-2 pr-3 text-right font-medium">Monto orig.</th>
                      <th scope="col" className="pb-2 pr-3 text-right font-medium">CLP</th>
                      <th scope="col" className="pb-2 pr-3 font-medium">Caso</th>
                      <th scope="col" className="pb-2 pr-3 font-medium">Estado</th>
                      <th scope="col" className="pb-2 font-medium" />
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.map((o) => (
                      <OrderRow key={o.order_id} order={o} onEdit={openEdit} onDone={reload} />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </ResourceGate>

      <Drawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        title={editing?.order_id ? "Editar pedido" : "Registrar pedido"}
      >
        {editing && <OrderForm initial={editing} onDone={onDone} />}
      </Drawer>
    </div>
  );
}
