import { useId, useRef, useState } from "react";
import { formatMoney } from "../money";
import { Button, toast } from "../ui";
import { invoiceRefusal, markInvoicePaid, type Invoice } from "./invoiceApi";

/**
 * «Registrar pago…»: the day and how much — all that is left by default, «50 %» one click away (the
 * owner's usual advance: half of the factura), any other amount typed. Less than what is left records an abono and the
 * factura stays «Por cobrar» with what is still owed; the payment that leaves nothing makes it
 * «Pagada». Used by the list row and by «Ver factura»; `large` is the factura's size.
 */
const clp = (n: number) => formatMoney(String(n), "CLP") ?? "—";
const digits = (text: string) => Number(text.replace(/\D/g, "") || "0");
const grouped = (n: number) => (n > 0 ? n.toLocaleString("es-CL") : "");

function refusal(err: unknown): string {
  const r = invoiceRefusal(err);
  switch (r?.code) {
    case "payment_exceeds_balance":
      return "Ese monto es mayor que lo que falta por pagar.";
    case "invoice_already_paid":
      return "Esta factura ya estaba pagada.";
    case "invoice_version_conflict":
      return "Alguien cambió esta factura recién. Se recargó la lista: vuelve a intentarlo.";
    case "paid_on_in_future":
      return "La fecha de pago no puede ser futura.";
    default:
      return "No se pudo guardar el pago. Revisa la conexión y vuelve a intentar.";
  }
}

export function PayForm({
  invoice: i,
  today,
  onDone,
  large = false,
}: {
  invoice: Invoice;
  today: string;
  onDone: () => void;
  large?: boolean;
}) {
  const left = i.balance ?? i.total_amount;
  const [open, setOpen] = useState(false);
  const [day, setDay] = useState(today);
  const [amount, setAmount] = useState(left);
  const [busy, setBusy] = useState(false);
  const opener = useRef<HTMLButtonElement>(null);
  const dayId = useId();
  const amountId = useId();
  const label = `${i.doc_type === 34 ? "Factura exenta" : "Factura"} ${i.folio}`;
  const validDay = /^\d{4}-\d{2}-\d{2}$/.test(day) && day <= today;
  const validAmount = amount > 0 && amount <= left;
  const size = large ? "min-h-11 px-4 text-[15px]" : "";
  const field = large ? "h-11 text-base" : "h-9 text-[14px]";

  const close = () => {
    setOpen(false);
    opener.current?.focus();
  };
  if (!open) {
    return (
      <button
        ref={opener}
        type="button"
        className={`inline-flex items-center justify-center rounded-md bg-accent font-medium text-on-solid hover:opacity-90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600 ${large ? "min-h-11 px-4 text-[15px]" : "h-8 px-3 text-[13px]"}`}
        onClick={() => {
          setAmount(left);
          setDay(today);
          setOpen(true);
        }}
      >
        Registrar pago…
      </button>
    );
  }

  async function save() {
    setBusy(true);
    try {
      await markInvoicePaid({ invoice_id: i.invoice_id, invoice_version: i.version, paid_on: day, ...(amount < left ? { amount } : {}) });
      toast(amount < left ? `Abono de ${clp(amount)} registrado: faltan ${clp(left - amount)}` : `${label} pagada`);
      setOpen(false);
    } catch (err) {
      toast(refusal(err), "bad");
    } finally {
      setBusy(false);
      onDone();
    }
  }

  // Half of the factura (the owner's usual advance), offered while it is less than what is left.
  const half = Math.round((i.billed ?? i.total_amount) / 2);
  const chip = (value: number, text: string) => (
    <button
      type="button"
      aria-pressed={amount === value}
      onClick={() => setAmount(value)}
      className={`min-h-8 rounded-full border px-3 text-[13px] font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-600 ${
        amount === value ? "border-accent bg-accent text-on-solid" : "border-line-strong text-ink hover:bg-canvas-sunken"
      }`}
    >
      {text}
    </button>
  );

  return (
    <form
      className="flex w-full flex-col gap-3 rounded-lg border border-line bg-canvas p-3"
      aria-label={`Pago de la ${label}`}
      onSubmit={(e) => {
        e.preventDefault();
        if (validDay && validAmount) void save();
      }}
      onKeyDown={(e) => e.key === "Escape" && close()}
    >
      <div className="flex flex-col gap-1">
        <label htmlFor={amountId} className="text-sm font-medium text-ink">
          ¿Cuánto pagó? <span className="font-normal text-ink-muted">Falta {clp(left)}</span>
        </label>
        <div className="flex flex-wrap items-center gap-2">
          <input
            id={amountId}
            name="amount"
            inputMode="numeric"
            autoComplete="off"
            autoFocus
            value={grouped(amount)}
            onChange={(e) => setAmount(digits(e.target.value))}
            aria-invalid={!validAmount || undefined}
            className={`${field} w-40 rounded-md border border-line bg-canvas-raised px-3 tabular-nums text-ink focus:border-brand-600 focus:outline-none focus:ring-1 focus:ring-brand-600`}
          />
          {chip(left, "Todo")}
          {half > 0 && half < left ? chip(half, "50 %") : null}
        </div>
        {!validAmount ? (
          <p className="text-[13px] text-bad">{amount > left ? `No puede ser más de ${clp(left)}.` : "Escribe el monto pagado."}</p>
        ) : amount < left ? (
          <p className="text-[13px] text-ink-muted">Queda un saldo de {clp(left - amount)} por cobrar.</p>
        ) : null}
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor={dayId} className="text-sm font-medium text-ink">¿Qué día pagó?</label>
        <input
          id={dayId}
          name="paid_on"
          type="date"
          value={day}
          max={today}
          onChange={(e) => setDay(e.target.value)}
          aria-invalid={!validDay || undefined}
          className={`${field} w-44 rounded-md border border-line bg-canvas-raised px-3 tabular-nums text-ink focus:border-brand-600 focus:outline-none focus:ring-1 focus:ring-brand-600`}
        />
        {!validDay ? <p className="text-[13px] text-bad">Elige un día de hoy o antes.</p> : null}
      </div>
      <div className="flex flex-wrap gap-2">
        <Button variant="quiet" className={size} disabled={busy} onClick={close}>Cancelar</Button>
        <Button type="submit" variant="primary" className={size} busy={busy} busyLabel="Guardando…" disabled={!validDay || !validAmount}>
          {amount < left ? "Guardar abono" : "Guardar pago"}
        </Button>
      </div>
    </form>
  );
}
