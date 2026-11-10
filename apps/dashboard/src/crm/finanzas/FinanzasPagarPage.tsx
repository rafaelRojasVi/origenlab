/**
 * «Pagar» — Finanzas v3 third tab.
 *
 * Merges the two old pages (FinanzasPorPagarPage and FinanzasGastosPage) under a single
 * «Pedidos · Gastos» segmented toggle, plus a «Pedidos estimados» strip from the
 * /v2/workspace/finance/resumen endpoint showing won cases whose supplier order
 * hasn't been registered yet.
 */
import { type FormEvent, useState } from "react";
import { newCaseCommandKey } from "../caseCommands";
import { crmHash, type CrmSection } from "../crmRoute";
import { formatMoney } from "../money";
import { Button, Drawer, FormField, Skeleton, TextInput, toast } from "../ui";
import { useResource } from "../useResource";
import { FinanzasGastosPage } from "./FinanzasGastosPage";
import { FinanzasPorPagarPage } from "./FinanzasPorPagarPage";
import { fetchFinanceResumen, upsertSupplierOrder, type PedidoEstimado } from "./financeApi";

type Navigate = (section: CrmSection, id?: string | null) => void;
type Pane = "pedidos" | "gastos";

const clp = (n: number) => formatMoney(String(n), "CLP") ?? "—";
const today = () => new Date().toISOString().slice(0, 10);

/** Mini drawer for confirming a supplier order from an estimated row. */
function ConfirmarPedidoDrawer({
  item,
  onClose,
  onDone,
}: {
  item: PedidoEstimado;
  onClose: () => void;
  onDone: () => void;
}) {
  const [supplierName, setSupplierName] = useState(item.supplier_name);
  const [orderedOn, setOrderedOn] = useState(today());
  const [amountClp, setAmountClp] = useState(String(item.estimated_cost_clp));
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const amt = parseInt(amountClp.replace(/\D/g, ""), 10);
    if (!supplierName.trim() || !orderedOn || isNaN(amt) || amt <= 0) return;
    setBusy(true);
    try {
      await upsertSupplierOrder({
        opportunity_id: item.opportunity_id,
        supplier_name: supplierName.trim(),
        ordered_on: orderedOn,
        amount_original: amt,
        currency: "CLP",
        amount_clp: amt,
      }, newCaseCommandKey());
      toast("Pedido registrado");
      onDone();
      onClose();
    } catch {
      toast("No se pudo registrar. Revisa la conexión.", "bad");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Drawer open title="Confirmar pedido de proveedor" onClose={onClose}>
      <form onSubmit={(e) => void submit(e)} className="flex flex-col gap-4 p-4">
        <p className="text-[13px] text-ink-muted">{item.title}</p>
        <FormField label="Proveedor" htmlFor="cp-supplier">
          <TextInput id="cp-supplier" value={supplierName} onChange={setSupplierName} placeholder="Nombre del proveedor" required />
        </FormField>
        <FormField label="Fecha del pedido" htmlFor="cp-ordered-on">
          <TextInput id="cp-ordered-on" value={orderedOn} onChange={setOrderedOn} placeholder="YYYY-MM-DD" required />
        </FormField>
        <FormField label="Monto (CLP)" htmlFor="cp-amount">
          <TextInput id="cp-amount" value={amountClp} onChange={setAmountClp} placeholder="Monto estimado" required />
        </FormField>
        <div className="flex gap-2 justify-end">
          <Button variant="quiet" type="button" onClick={onClose}>Cancelar</Button>
          <Button variant="primary" type="submit" busy={busy} busyLabel="Registrando…">Registrar pedido</Button>
        </div>
      </form>
    </Drawer>
  );
}

function FinanzasTabs({ active, navigate }: { active: "fin-resumen" | "fin-cobrar" | "fin-pagar"; navigate: Navigate }) {
  const tabs: Array<{ id: "fin-resumen" | "fin-cobrar" | "fin-pagar"; label: string }> = [
    { id: "fin-resumen", label: "Resumen" },
    { id: "fin-cobrar", label: "Cobrar" },
    { id: "fin-pagar", label: "Pagar" },
  ];
  return (
    <nav className="inline-flex gap-0.5 rounded-lg bg-sunken p-0.5" aria-label="Pestañas de Finanzas">
      {tabs.map((t) => (
        <a
          key={t.id}
          href={crmHash(t.id)}
          onClick={(e) => { e.preventDefault(); navigate(t.id); }}
          aria-current={active === t.id ? "page" : undefined}
          className={[
            "inline-flex h-8 items-center rounded-md px-3.5 text-[13px] font-semibold transition-colors",
            active === t.id
              ? "bg-raised text-ink shadow-sm"
              : "text-ink-muted hover:text-ink",
          ].join(" ")}
        >
          {t.label}
        </a>
      ))}
    </nav>
  );
}

function PedidosEstimadosStrip({ items, navigate, onConfirmed }: { items: PedidoEstimado[]; navigate: Navigate; onConfirmed: () => void }) {
  const [confirming, setConfirming] = useState<PedidoEstimado | null>(null);
  if (items.length === 0) return null;
  return (
    <>
      <section className="rounded-xl border border-line bg-raised px-4 pb-3 pt-3.5" aria-labelledby="estimados-h">
        <div className="flex items-baseline justify-between gap-3 mb-2.5">
          <h3 id="estimados-h" className="text-[13px] font-bold">
            Pedidos estimados por registrar
          </h3>
          <p className="text-xs text-ink-faint">Casos ganados sin pedido de proveedor aún</p>
        </div>
        <div className="flex flex-col">
          {items.map((p) => (
            <div
              key={p.opportunity_id}
              className="flex items-center gap-3 py-2 border-t border-line first:border-t-0"
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <a
                    className="text-[13px] font-semibold text-accent hover:underline underline-offset-2 truncate"
                    href={crmHash("oportunidades", p.opportunity_id)}
                    onClick={(e) => { e.preventDefault(); navigate("oportunidades", p.opportunity_id); }}
                  >
                    {p.title}
                  </a>
                  <span className="inline-flex items-center h-5 px-2 rounded-full bg-sunken border border-line text-[11px] font-semibold text-ink-muted">
                    Estimado
                  </span>
                </div>
                <span className="text-xs text-ink-faint">{p.supplier_name}</span>
              </div>
              <div className="flex items-center gap-3">
                <span className="text-[14px] font-semibold whitespace-nowrap">{clp(p.estimated_cost_clp)}</span>
                <Button variant="quiet" onClick={() => setConfirming(p)}>Confirmar pago</Button>
              </div>
            </div>
          ))}
        </div>
        <p className="mt-2 text-xs text-ink-faint">
          El costo es el de la cotización ganada · confirma el pedido real para que salga de aquí.
        </p>
      </section>
      {confirming ? (
        <ConfirmarPedidoDrawer item={confirming} onClose={() => setConfirming(null)} onDone={onConfirmed} />
      ) : null}
    </>
  );
}

export function FinanzasPagarPage({ navigate }: { navigate: Navigate }) {
  const [pane, setPane] = useState<Pane>("pedidos");
  const [resState, reloadRes] = useResource(fetchFinanceResumen);

  const pedidosEstimados = resState.kind === "ready" ? resState.data.pedidos_estimados : [];

  return (
    <div className="flex flex-col gap-3.5 pb-10">
      <div className="flex items-end justify-between gap-3 flex-wrap">
        <h1 className="text-[17px] font-semibold tracking-tight">Finanzas</h1>
        <FinanzasTabs active="fin-pagar" navigate={navigate} />
      </div>

      {/* Pedidos estimados strip — shown only on Pedidos pane and when there's data */}
      {pane === "pedidos" && (
        resState.kind === "loading" ? (
          <Skeleton rows={2} />
        ) : pedidosEstimados.length > 0 ? (
          <PedidosEstimadosStrip items={pedidosEstimados} navigate={navigate} onConfirmed={reloadRes} />
        ) : null
      )}

      {/* Pedidos / Gastos toggle */}
      <div className="flex items-center gap-3">
        <nav className="inline-flex gap-0.5 rounded-lg bg-sunken p-0.5" aria-label="Vista de Pagar">
          {(["pedidos", "gastos"] as Pane[]).map((p) => (
            <button
              key={p}
              type="button"
              onClick={() => setPane(p)}
              aria-pressed={pane === p}
              className={[
                "inline-flex h-8 items-center rounded-md px-3.5 text-[13px] font-semibold transition-colors",
                pane === p
                  ? "bg-raised text-ink shadow-sm"
                  : "text-ink-muted hover:text-ink",
              ].join(" ")}
            >
              {p === "pedidos" ? "Pedidos de proveedor" : "Gastos operacionales"}
            </button>
          ))}
        </nav>
      </div>

      {pane === "pedidos" ? (
        <FinanzasPorPagarPage />
      ) : (
        <FinanzasGastosPage />
      )}
    </div>
  );
}
