import { useEffect, useRef, useState, type ReactNode } from "react";
import { useMayRunCaseCommands } from "../caseCommands";
import { fetchPipeline } from "../crmApi";
import { crmHash, type CrmSection } from "../crmRoute";
import type { OpportunityCardData } from "../crmTypes";
import { formatMoney } from "../money";
import { Button, EmptyState, Panel, ResourceGate, SearchInput, Skeleton, toast, type Tone } from "../ui";
import { useResource } from "../useResource";
import { factoringCost } from "../finanzas/financeCalc";
import {
  fetchInvoices,
  importInvoices,
  invoiceRefusal,
  markInvoicePaid,
  markInvoiceUnpaid,
  setInvoiceCase,
  type AgeBucket,
  type ImportResult,
  type Invoice,
  type InvoiceList,
  type InvoiceStatus,
} from "./invoiceApi";
import { InvoiceSheet, purchaseOrders } from "./InvoiceSheet";
import { PayForm } from "./PayForm";
import { PorFacturar, casesToInvoice } from "./PorFacturar";

/**
 * «Cobrar» — Finanzas v3: las facturas del Registro de Ventas del SII, su caso y lo que falta
 * por cobrar. El CSV mensual se sube aquí («Subir CSV del SII»); repetir un mes no duplica nada.
 * La cabecera muestra lo que falta, segmentado por antigüedad. Cada fila es un card friendly:
 * nombre del cliente, término «vence en N días / vencida hace N días», chip «paga a N d», importe
 * a la derecha. 44 px de toque mínimo en todas las acciones.
 */
type Navigate = (section: CrmSection, id?: string | null) => void;
type Filter = "por_cobrar" | "sin_caso" | "pagadas" | "todas";

const DOC_LABEL: Record<number, string> = { 33: "Factura", 34: "Factura exenta", 56: "Nota de débito", 61: "Nota de crédito" };
const STATUS: Record<InvoiceStatus, { label: string; tone: Tone }> = {
  unpaid: { label: "Por cobrar", tone: "warn" },
  paid: { label: "Pagada", tone: "good" },
  credited: { label: "Anulada por nota de crédito", tone: "neutral" },
  nota: { label: "Nota", tone: "neutral" },
};
const AGE_LABEL_SHORT: Record<AgeBucket, string> = { "0-30": "0–30 d", "31-60": "31–60 d", "61-90": "61–90 d", "90+": "+90 d" };

/** A text-sized inline action with a visible focus ring. */
const LINK_ACTION =
  "inline-flex min-h-6 items-center rounded px-1 -mx-1 text-[12.5px] underline-offset-2 hover:underline " +
  "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-brand-600 disabled:opacity-50";

const clp = (n: number) => formatMoney(String(n), "CLP") ?? "—";
const collectable = (i: Invoice) => i.doc_type === 33 || i.doc_type === 34;

function refusalText(err: unknown): string {
  const r = invoiceRefusal(err);
  if (!r) return "No se pudo guardar. Revisa la conexión y vuelve a intentar.";
  switch (r.code) {
    case "sii_file_invalid": return r.message;
    case "sii_file_too_large": return "El archivo pesa más de 2 MB: no parece el Registro de Ventas de un mes.";
    case "invoice_version_conflict": return "Alguien cambió esta factura recién. Se recargó la lista: vuelve a intentarlo.";
    case "invoice_already_paid": return "Esta factura ya estaba marcada como pagada.";
    case "invoice_not_paid": return "Esta factura no tiene pagos que deshacer.";
    case "payment_exceeds_balance": return "Ese monto es mayor que lo que falta por pagar.";
    case "invoice_not_collectable": return "Una nota de crédito o débito no se cobra: ajusta la factura que menciona.";
    case "paid_on_in_future": return "La fecha de pago no puede ser futura.";
    case "case_not_found": return "Ese caso ya no existe.";
    default: return r.message || "No se pudo guardar.";
  }
}

function importSummary(results: ImportResult[]): string {
  const sum = (k: "imported" | "already_on_record" | "attached_to_case") => results.reduce((n, r) => n + r[k], 0);
  const [imported, already, attached] = [sum("imported"), sum("already_on_record"), sum("attached_to_case")];
  const parts = [`${imported} ${imported === 1 ? "documento nuevo" : "documentos nuevos"}`];
  if (already) parts.push(`${already} ya ${already === 1 ? "estaba" : "estaban"}`);
  if (attached) parts.push(`${attached} ${attached === 1 ? "asignado" : "asignados"} a su caso`);
  return parts.join(" · ");
}

/** Aging segmented bar: proportional blocks coloured by bucket risk. */
function AgeBar({ byAge, total }: { byAge: Record<AgeBucket, number>; total: number }) {
  if (total <= 0) return null;
  const buckets: Array<{ key: AgeBucket; bg: string }> = [
    { key: "0-30", bg: "bg-good" },
    { key: "31-60", bg: "bg-warn" },
    { key: "61-90", bg: "bg-orange-500" },
    { key: "90+", bg: "bg-bad" },
  ];
  return (
    <div className="mt-2 space-y-1" aria-hidden="true">
      <div className="flex h-2 w-full overflow-hidden rounded-full bg-line">
        {buckets.map(({ key, bg }) => {
          const pct = (byAge[key] ?? 0) / total;
          return pct > 0 ? <div key={key} className={`h-full ${bg}`} style={{ width: `${pct * 100}%` }} /> : null;
        })}
      </div>
      <div className="flex gap-3 flex-wrap">
        {buckets.map(({ key, bg }) => {
          const v = byAge[key] ?? 0;
          if (v <= 0) return null;
          return (
            <span key={key} className="inline-flex items-center gap-1 text-[11.5px] text-ink-muted">
              <span className={`inline-block h-2 w-2 rounded-sm ${bg}`} />
              {AGE_LABEL_SHORT[key]}: <span className="tabular-nums">{clp(v)}</span>
            </span>
          );
        })}
      </div>
    </div>
  );
}

/**
 * «paga a N d» chip: good tone ≤30 d (Ley 21.131), warn tone >30 d, faint when no payment history.
 */
function PagaChip({ avgDays, hasHistory }: { avgDays: number; hasHistory: boolean }) {
  if (!hasHistory) return <span className="text-[11px] text-ink-faint">sin historial</span>;
  const good = avgDays <= 30;
  return (
    <span className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10.5px] font-semibold border ${good ? "border-good/30 bg-good/10 text-good" : "border-warn/30 bg-warn/10 text-warn"}`}>
      paga a {avgDays} d
    </span>
  );
}

/** Inline factoring simulator mini-panel for one invoice. */
function FactorizarPanel({ amount, days, onClose }: { amount: number; days: number; onClose: () => void }) {
  const [rate, setRate] = useState("1.5");
  const result = (() => {
    const r = parseFloat(rate.replace(",", ".")) / 100;
    if (!r || r <= 0 || amount <= 0 || days <= 0) return null;
    return factoringCost(amount, days, r);
  })();
  const clpV = (n: number) => formatMoney(String(Math.round(n)), "CLP") ?? "—";
  return (
    <div className="mt-1.5 rounded-lg border border-line bg-canvas-sunken px-3 py-2 text-xs space-y-2">
      <div className="flex items-center justify-between">
        <p className="font-semibold text-ink">Factorizar esta factura</p>
        <button type="button" onClick={onClose} className="text-ink-muted hover:text-ink" aria-label="Cerrar simulador">✕</button>
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        <label className="flex items-center gap-1.5">
          <span className="text-ink-muted">Tasa mensual (%)</span>
          <input
            type="text"
            inputMode="decimal"
            className="h-7 w-16 rounded border border-line bg-canvas-raised px-2 font-mono tabular-nums text-ink focus:border-accent focus:outline-none"
            value={rate}
            onChange={(e) => setRate(e.target.value)}
          />
        </label>
        {result ? (
          <span className="text-ink-muted">
            Costo: <strong className="text-bad">{clpV(result.cost)}</strong>
            {" · "}Neto recibido: <strong className="text-good">{clpV(result.netReceived)}</strong>
          </span>
        ) : (
          <span className="text-ink-faint">Ingresa una tasa válida</span>
        )}
      </div>
    </div>
  );
}

/**
 * Floating «•••» overflow menu: uses position:fixed to escape any parent overflow:hidden.
 * Items: Pagada entera, Factorizar, Abrir factura, (Deshacer pago).
 */
interface OverflowItem { label: string; onClick: () => void; disabled?: boolean; tone?: "bad" }
function OverflowMenu({ items }: { items: OverflowItem[] }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState({ top: 0, left: 0 });
  const btn = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  function toggle() {
    if (!open && btn.current) {
      const r = btn.current.getBoundingClientRect();
      setPos({ top: r.bottom + 4, left: r.right });
    }
    setOpen((v) => !v);
  }

  // Focus the first enabled menuitem when the menu opens.
  useEffect(() => {
    if (!open) return;
    const first = menuRef.current?.querySelector<HTMLButtonElement>('[role="menuitem"]:not(:disabled)');
    first?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const close = (e: Event) => {
      if (btn.current && btn.current.contains(e.target as Node)) return;
      setOpen(false);
    };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("click", close, true);
    document.addEventListener("keydown", esc);
    // Close when the page scrolls so the menu doesn't drift away from its anchor.
    document.addEventListener("scroll", close, true);
    return () => {
      document.removeEventListener("click", close, true);
      document.removeEventListener("keydown", esc);
      document.removeEventListener("scroll", close, true);
    };
  }, [open]);

  return (
    <>
      <button
        ref={btn}
        type="button"
        aria-label="Más acciones"
        aria-expanded={open}
        onClick={toggle}
        className="inline-flex h-8 min-h-11 w-8 items-center justify-center rounded-md border border-line text-ink-muted hover:bg-canvas-sunken hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-600 dark:border-line dark:hover:bg-canvas-sunken"
      >
        •••
      </button>
      {open ? (
        <div
          ref={menuRef}
          role="menu"
          aria-label="Acciones adicionales"
          style={{ position: "fixed", top: pos.top, left: pos.left, transform: "translateX(-100%)", zIndex: 50 }}
          className="min-w-[160px] rounded-lg border border-line bg-canvas-raised py-1 shadow-lg"
        >
          {items.map((item) => (
            <button
              key={item.label}
              role="menuitem"
              type="button"
              disabled={item.disabled}
              className={`block min-h-9 w-full px-4 py-1.5 text-left text-[13px] hover:bg-canvas-sunken disabled:opacity-50 ${item.tone === "bad" ? "text-bad" : "text-ink"}`}
              onClick={() => { item.onClick(); setOpen(false); }}
            >
              {item.label}
            </button>
          ))}
        </div>
      ) : null}
    </>
  );
}

/** Term-based aging line: «vence en N días» or «vencida hace N días».
 *  Tone escalates: warn for 1–30 days past term, bad for >30 days past term. */
function AgingLine({ invoice: i }: { invoice: Invoice }) {
  if (i.status !== "unpaid") return null;
  const term = i.avg_days_client ?? 30;
  // Compute days remaining: if expected_pay_date is set use it; else issued_on + term.
  const dueStr = i.expected_pay_date ?? addDays(i.issued_on, term);
  const remaining = daysBetween(i.issued_on, dueStr) - i.days_open;
  if (remaining < -1) {
    const pastDue = Math.abs(remaining);
    const tone = pastDue > 30 ? "text-bad" : "text-warn";
    return <span className={`font-medium ${tone}`}>vencida hace {pastDue} días</span>;
  }
  if (remaining <= 0)
    return <span className="font-medium text-warn">vence hoy</span>;
  if (remaining === 1)
    return <span className="text-ink-muted">vence mañana</span>;
  return <span className="text-ink-muted">vence en {remaining} días</span>;
}

function addDays(dateStr: string, days: number): string {
  const d = new Date(`${dateStr}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

function daysBetween(start: string, end: string): number {
  return Math.round((Date.parse(end) - Date.parse(start)) / 86_400_000);
}

export function PagosPage({ navigate }: { navigate: Navigate }) {
  const [state, reload] = useResource(fetchInvoices);
  const [pipeline] = useResource(fetchPipeline);
  const may = useMayRunCaseCommands();
  const [filter, setFilter] = useState<Filter>("por_cobrar");
  const [uploading, setUploading] = useState(false);
  const [chosenFile, setChosenFile] = useState<string | null>(null);
  const [lastUpload, setLastUpload] = useState<{ text: string; ok: boolean } | null>(null);
  const [help, setHelp] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const cases = pipeline.kind === "ready" ? pipeline.data.items : [];

  async function upload(files: FileList | null) {
    const list = files ? Array.from(files) : [];
    if (list.length === 0) return;
    setChosenFile(list.length === 1 ? (list[0]?.name ?? null) : `${list.length} archivos`);
    setUploading(true);
    setLastUpload(null);
    const results: ImportResult[] = [];
    try {
      for (const file of list) results.push(await importInvoices(file));
      const text = importSummary(results);
      setLastUpload({ text: `Listo: ${text}.`, ok: true });
      toast(text);
    } catch (err) {
      const text = refusalText(err);
      const done = results.length ? ` (${importSummary(results)} antes del error)` : "";
      setLastUpload({ text: `${text}${done}`, ok: false });
      toast(text, "bad");
    } finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = "";
      reload();
    }
  }

  return (
    <div className="space-y-4">
      {/* Upload bar ─────────────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-2">
        {may ? (
          <>
            <input
              ref={fileInput}
              type="file"
              accept=".csv,text/csv"
              multiple
              className="sr-only"
              tabIndex={-1}
              id="pagos-csv-input"
              aria-label="Archivo CSV del Registro de Ventas"
              onChange={(e) => void upload(e.target.files)}
            />
            <label
              htmlFor="pagos-csv-input"
              className={`inline-flex h-9 cursor-pointer items-center rounded-md px-3.5 text-[13px] font-semibold transition-colors select-none ${
                uploading
                  ? "bg-canvas-sunken text-ink-muted cursor-not-allowed"
                  : "bg-accent text-on-solid hover:opacity-90 focus-within:outline focus-within:outline-2 focus-within:outline-brand-600"
              }`}
              aria-disabled={uploading}
            >
              {uploading ? "Subiendo…" : "Subir CSV del SII"}
            </label>
            {chosenFile && !uploading ? (
              <span className="max-w-[200px] truncate text-[12.5px] text-ink-muted" title={chosenFile}>{chosenFile}</span>
            ) : null}
          </>
        ) : null}
        <button
          type="button"
          className={`${LINK_ACTION} ml-auto text-xs text-ink-muted`}
          aria-expanded={help}
          aria-controls="pagos-help"
          onClick={() => setHelp((h) => !h)}
        >
          ¿Cómo descargo el archivo?
        </button>
      </div>
      {help ? (
        <div id="pagos-help" className="rounded-lg border border-line bg-canvas-raised px-4 py-3">
          <HowToDownload />
        </div>
      ) : null}
      <p role="status" aria-live="polite" className={lastUpload ? `text-[12.5px] ${lastUpload.ok ? "text-good" : "text-bad"}` : "sr-only"}>
        {lastUpload ? (
          <>
            <span aria-hidden="true">{lastUpload.ok ? "✓ " : "✕ "}</span>
            {lastUpload.text}
          </>
        ) : null}
      </p>
      <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={6} />}>
        {(data) => <Register data={data} filter={filter} setFilter={setFilter} cases={cases} may={may} reload={reload} navigate={navigate} />}
      </ResourceGate>
    </div>
  );
}

function HowToDownload() {
  return (
    <ol className="list-decimal space-y-1 pl-5 text-left text-[12.5px] leading-5 text-ink-muted">
      <li>Entra a sii.cl → Servicios online → Factura electrónica → Registro de Compras y Ventas.</li>
      <li>Elige el mes y pulsa «Consultar».</li>
      <li>
        Abre la pestaña <strong className="font-semibold text-ink">VENTA</strong> (no COMPRA) y pulsa «Descargar Detalles».
      </li>
      <li>Sube aquí el archivo que empieza con RCV_VENTA. Puedes subir varios meses a la vez; repetir un mes no duplica nada.</li>
    </ol>
  );
}

function Register({
  data,
  filter,
  setFilter,
  cases,
  may,
  reload,
  navigate,
}: {
  data: InvoiceList;
  filter: Filter;
  setFilter: (f: Filter) => void;
  cases: OpportunityCardData[];
  may: boolean;
  reload: () => void;
  navigate: Navigate;
}) {
  const { summary } = data;
  const [openId, setOpenId] = useState<string | null>(null);
  if (data.items.length === 0) {
    return (
      <EmptyState title="Todavía no hay facturas">
        Sube el Registro de Ventas del SII de cada mes.
        <span className="mx-auto mt-2 block max-w-md">
          <HowToDownload />
        </span>
      </EmptyState>
    );
  }
  const withoutCase = (i: Invoice) => collectable(i) && i.status !== "credited" && i.opportunity === null;
  const pick: Record<Filter, (i: Invoice) => boolean> = {
    por_cobrar: (i) => i.status === "unpaid",
    sin_caso: withoutCase,
    pagadas: (i) => i.status === "paid",
    todas: () => true,
  };
  const shown = data.items.filter(pick[filter]);
  if (filter === "por_cobrar") shown.sort((a, b) => b.days_open - a.days_open || a.folio - b.folio);
  const count = (f: Filter) => data.items.filter(pick[f]).length;
  const sinCasoCount = count("sin_caso");
  const opened = openId ? data.items.find((i) => i.invoice_id === openId) ?? null : null;
  const cardOf = (i: Invoice) => (i.opportunity ? cases.find((c) => c.opportunity_id === i.opportunity!.id) ?? null : null);

  return (
    <>
      <PorFacturar cases={casesToInvoice(cases, data.items)} navigate={navigate} />

      {/* Summary header ──────────────────────────────────────────────── */}
      <section aria-label="Resumen de lo que falta por cobrar" className="rounded-lg border border-line bg-canvas-raised px-4 py-3.5">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
          <p className="text-2xl font-bold tracking-tight text-ink">
            Por cobrar{" "}
            <span className="tabular-nums">{clp(summary.por_cobrar)}</span>
          </p>
          <p className="text-sm text-ink-muted">
            {summary.unpaid_count} {summary.unpaid_count === 1 ? "factura sin pagar" : "facturas sin pagar"}
          </p>
        </div>
        <AgeBar byAge={summary.by_age} total={summary.por_cobrar} />
        {summary.avg_days_overall > 0 ? (
          <p className="mt-2 text-[12.5px] text-ink-muted">
            Promedio de cobro{" "}
            <strong className="font-semibold text-ink">{summary.avg_days_overall} días</strong>
            {" · "}
            <span className={summary.avg_days_overall <= 30 ? "text-good font-medium" : "text-warn font-medium"}>
              ley 30
            </span>
          </p>
        ) : null}
        {sinCasoCount > 0 ? (
          <button type="button" className={`${LINK_ACTION} mt-2 text-warn`} onClick={() => setFilter("sin_caso")}>
            <span aria-hidden="true">! </span>
            {sinCasoCount} {sinCasoCount === 1 ? "factura sin caso: asígnala" : "facturas sin caso: asígnalas"}
          </button>
        ) : null}
      </section>

      {/* Filter tabs ─────────────────────────────────────────────────── */}
      <div role="group" aria-label="Filtrar facturas" className="inline-flex gap-0.5 rounded-lg bg-canvas-sunken p-0.5">
        {(
          [
            { f: "por_cobrar" as Filter, label: "Por cobrar" },
            { f: "sin_caso" as Filter, label: "Sin caso", muted: sinCasoCount === 0 },
            { f: "pagadas" as Filter, label: "Pagadas" },
            { f: "todas" as Filter, label: "Todas" },
          ] as Array<{ f: Filter; label: string; muted?: boolean }>
        ).map(({ f, label, muted }) => {
          const c = count(f);
          return (
            <button
              key={f}
              type="button"
              aria-pressed={filter === f}
              onClick={() => setFilter(f)}
              className={[
                "inline-flex h-8 min-h-[44px] items-center gap-1.5 rounded-md px-3 text-[13px] font-medium transition-colors",
                filter === f
                  ? "bg-canvas-raised text-ink shadow-sm"
                  : muted
                    ? "text-ink-faint hover:text-ink-muted"
                    : "text-ink-muted hover:text-ink",
              ].join(" ")}
            >
              {label}
              <span
                className={`inline-flex h-5 min-w-[20px] items-center justify-center rounded-full px-1.5 text-[11px] font-semibold ${
                  filter === f ? "bg-brand-100 text-brand-700" : "bg-canvas-sunken text-ink-faint"
                }`}
              >
                {c}
              </span>
            </button>
          );
        })}
      </div>

      {/* Invoice list ────────────────────────────────────────────────── */}
      <Panel bodyClassName="divide-y divide-line">
        {shown.length === 0 ? (
          <p className="px-4 py-5 text-[13px] text-ink-muted">
            {filter === "por_cobrar"
              ? "Nada por cobrar: todas las facturas están pagadas."
              : filter === "sin_caso"
                ? "Todas las facturas tienen su caso."
                : "Ninguna factura en esta vista."}
          </p>
        ) : (
          <ul aria-label="Facturas">
            {shown.map((i) => (
              <InvoiceRow
                key={i.invoice_id}
                i={i}
                today={data.today}
                showStatus={filter !== "por_cobrar"}
                cases={cases}
                card={cardOf(i)}
                may={may}
                reload={reload}
                navigate={navigate}
                onOpen={() => setOpenId(i.invoice_id)}
              />
            ))}
          </ul>
        )}
      </Panel>
      {opened ? (
        <InvoiceSheet invoice={opened} card={cardOf(opened)} today={data.today} may={may} onClose={() => setOpenId(null)} onChanged={reload} navigate={navigate} />
      ) : null}
    </>
  );
}

function InvoiceRow({
  i,
  today,
  showStatus,
  cases,
  card,
  may,
  reload,
  navigate,
  onOpen,
}: {
  i: Invoice;
  today: string;
  /** Off inside «Por cobrar», where every row would say the same. */
  showStatus: boolean;
  cases: OpportunityCardData[];
  card: OpportunityCardData | null;
  may: boolean;
  reload: () => void;
  navigate: Navigate;
  onOpen: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [showFactorizar, setShowFactorizar] = useState(false);
  const [picking, setPicking] = useState(false);
  const caseButton = useRef<HTMLButtonElement>(null);
  const docLabel = DOC_LABEL[i.doc_type] ?? `Documento ${i.doc_type}`;
  const label = `${docLabel} ${i.folio}`;
  const name = i.organization?.name ?? i.client_name;
  const avgDays = i.avg_days_client ?? 30;
  const hasPayHistory = (i.avg_days_client ?? 0) > 0;
  const status = STATUS[i.status];

  // Primary action label changes when overdue
  const isOverdue = i.status === "unpaid" && i.days_open > avgDays;

  async function run(action: () => Promise<unknown>, done: string) {
    setBusy(true);
    try {
      await action();
      toast(done);
    } catch (err) {
      toast(refusalText(err), "bad");
    } finally {
      setBusy(false);
      setPicking(false);
      reload();
    }
  }

  async function payAll() {
    await run(
      () => markInvoicePaid({ invoice_id: i.invoice_id, invoice_version: i.version, paid_on: today }),
      `${label} pagada hoy`,
    );
  }

  const ref = { invoice_id: i.invoice_id, invoice_version: i.version };
  const closePicker = () => { setPicking(false); caseButton.current?.focus(); };

  // Case display: «Caso 01219-26» link + «Cambiar» quiet text
  let caseLine: ReactNode = null;
  if (i.opportunity) {
    const shortRef = i.opportunity.quote_number
      ? `Caso ${i.opportunity.quote_number}`
      : i.opportunity.title ?? "Ver caso";
    caseLine = (
      <span className="inline-flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
        <a
          href={crmHash("oportunidades", i.opportunity.id)}
          className={`${LINK_ACTION} text-accent`}
          onClick={(e) => {
            if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
            e.preventDefault();
            navigate("oportunidades", i.opportunity!.id);
          }}
        >
          {shortRef}
        </a>
        {may && collectable(i) ? (
          <button ref={caseButton} type="button" className={`${LINK_ACTION} text-ink-muted`} disabled={busy} onClick={() => setPicking(true)}>
            Cambiar
          </button>
        ) : null}
      </span>
    );
  } else if (collectable(i) && i.status !== "credited") {
    caseLine = may ? (
      <button ref={caseButton} type="button" className={`${LINK_ACTION} font-medium text-warn`} disabled={busy} onClick={() => setPicking(true)}>
        <span aria-hidden="true" className="mr-1">!</span>Asignar caso…
      </button>
    ) : (
      <span className="text-[12px] text-ink-faint">Sin caso</span>
    );
  }

  // Partial payment progress bar data
  const totalBilled = (i as unknown as { billed?: number }).billed ?? i.total_amount;
  const paidAmount = (i as unknown as { paid_amount?: number }).paid_amount ?? 0;
  const displayAmount = i.status === "paid" ? totalBilled : i.balance ?? i.total_amount;
  const oc = purchaseOrders(card).map((d) => d.document_number).filter(Boolean).join(", ");

  // Build «•••» overflow menu items
  const menuItems: OverflowItem[] = [];
  if (may && i.status === "unpaid") {
    menuItems.push({ label: "Pagada entera", onClick: () => void payAll(), disabled: busy });
    menuItems.push({ label: showFactorizar ? "Ocultar factoring" : "Factorizar", onClick: () => setShowFactorizar((v) => !v) });
  }
  menuItems.push({ label: "Abrir factura", onClick: onOpen });
  if (may && i.status === "paid") {
    menuItems.push({
      label: "Deshacer pago",
      onClick: () => void run(() => markInvoiceUnpaid(ref), `${label} vuelve a «Por cobrar»`),
      tone: "bad",
    });
  }

  return (
    <li data-testid="invoice-row" aria-label={`${label}, ${name}`}>
      <div className="flex items-start gap-3 px-4 py-3">
        {/* Left: invoice details */}
        <div className="min-w-0 flex-1 space-y-1">
          {/* Client name + payment-history chip */}
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-[15px] font-semibold leading-snug text-ink">{name}</span>
            {collectable(i) && <PagaChip avgDays={avgDays} hasHistory={hasPayHistory} />}
          </div>
          {/* Invoice doc · date · aging */}
          <p className="text-sm text-ink-muted">
            <button type="button" className={`${LINK_ACTION} text-sm font-semibold text-accent`} onClick={onOpen} aria-label={`Ver la ${label}`}>
              {label}
            </button>
            {" · "}{i.issued_on.slice(5).replace("-", " ")}
            {i.status === "unpaid" ? <> · <AgingLine invoice={i} /></> : null}
            {showStatus ? (
              <span className={`ml-1 text-[12px] font-medium ${i.status === "paid" ? "text-good" : "text-ink-muted"}`}>
                · {status.label}{i.status === "paid" && i.paid_on ? ` el ${i.paid_on.slice(5).replace("-", "/")}` : ""}
              </span>
            ) : null}
          </p>
          {/* RUT + OC */}
          <p className="text-[12.5px] text-ink-faint">
            RUT {i.client_rut}
            {oc ? <> · OC {oc}</> : null}
            {!i.organization ? " · no está en Organizaciones" : null}
          </p>
          {/* Case link */}
          {caseLine ? <div className="text-[12.5px]">{caseLine}</div> : null}
          {/* Case picker */}
          {picking ? (
            <CasePicker
              cases={cases}
              organizationId={i.organization?.id ?? null}
              current={i.opportunity?.id ?? null}
              onPick={(id) => void run(() => setInvoiceCase({ ...ref, opportunity_id: id }), id ? "Caso asignado" : "Caso quitado")}
              onClose={closePicker}
            />
          ) : null}
          {/* Partial payment progress bar */}
          {i.status === "unpaid" && paidAmount > 0 && totalBilled > 0 ? (
            <div className="space-y-0.5">
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-line">
                <div className="h-full rounded-full bg-good transition-all" style={{ width: `${Math.min(100, (paidAmount / totalBilled) * 100)}%` }} />
              </div>
              <p className="text-[11.5px] tabular-nums text-ink-muted">
                abonado {clp(paidAmount)} de {clp(totalBilled)}
              </p>
            </div>
          ) : null}
          {/* Notes (credit/debit notes attached to this invoice) */}
          {i.notes.length > 0 ? (
            <ul aria-label="Notas de esta factura" className="space-y-0.5">
              {i.notes.map((n) => (
                <li key={n.invoice_id} className="text-[11.5px] tabular-nums text-ink-muted">
                  {DOC_LABEL[n.doc_type]} {n.folio} ({n.issued_on.slice(5).replace("-", " ")}): {n.doc_type === 61 ? "−" : "+"}
                  {clp(n.total_amount)}
                </li>
              ))}
            </ul>
          ) : null}
          {i.reference ? (
            <p className="text-[11.5px] text-ink-muted">
              Ajusta {DOC_LABEL[i.reference.doc_type] ?? "el documento"} {i.reference.folio}, que no está en la lista.
            </p>
          ) : null}
          {/* Factorizar panel (opened from the •••  menu) */}
          {showFactorizar ? (
            <FactorizarPanel
              amount={i.balance ?? i.total_amount}
              days={Math.max(1, avgDays - i.days_open)}
              onClose={() => setShowFactorizar(false)}
            />
          ) : null}
        </div>

        {/* Right: amount + actions */}
        <div className="flex shrink-0 flex-col items-end gap-2">
          <p className="text-lg font-bold tabular-nums text-ink">{clp(displayAmount)}</p>
          {may && i.status === "unpaid" ? (
            <div className="flex items-center gap-1.5">
              <PayForm
                invoice={i}
                today={today}
                onDone={reload}
                triggerLabel={isOverdue ? "Recordar pago…" : "Registrar pago…"}
              />
              <OverflowMenu items={menuItems} />
            </div>
          ) : null}
          {(may && i.status === "paid") || (!may && i.status === "unpaid") ? (
            <OverflowMenu items={menuItems} />
          ) : null}
        </div>
      </div>
    </li>
  );
}

const caseLabel = (c: OpportunityCardData): string => {
  const quote = c.latest_revision?.quote_number ?? c.quote_numbers[0] ?? null;
  return [quote, c.organization?.name, c.title].filter(Boolean).join(" · ");
};

function CasePicker({
  cases,
  organizationId,
  current,
  onPick,
  onClose,
}: {
  cases: OpportunityCardData[];
  organizationId: string | null;
  current: string | null;
  onPick: (opportunityId: string | null) => void;
  onClose: () => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [query, setQuery] = useState("");
  useEffect(() => {
    box.current?.querySelector("input")?.focus();
    const outside = (e: MouseEvent) => box.current && !box.current.contains(e.target as Node) && onClose();
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("mousedown", outside);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", outside);
      document.removeEventListener("keydown", esc);
    };
  }, [onClose]);
  const q = query.trim().toLowerCase();
  const own = (c: OpportunityCardData) => organizationId !== null && c.organization?.organization_id === organizationId;
  const matches = (q
    ? cases.filter((c) =>
        [c.title, c.organization?.name ?? "", ...c.quote_numbers, c.latest_revision?.quote_number ?? ""].some((s) => s.toLowerCase().includes(q)),
      )
    : cases.filter(own)
  )
    .slice()
    .sort((a, b) => Number(own(b)) - Number(own(a)))
    .slice(0, 8);
  return (
    <div ref={box} role="dialog" aria-label="Asignar caso" className="relative z-20 w-full max-w-sm space-y-2 rounded-lg border border-line bg-canvas-raised p-2.5 shadow-lg">
      <SearchInput value={query} onChange={setQuery} placeholder="Número, institución o título…" label="Buscar caso" />
      {!q && matches.length > 0 ? <p className="px-1 text-[11.5px] text-ink-faint">Casos de este cliente:</p> : null}
      {matches.length === 0 ? (
        <p className="px-1 text-[12px] text-ink-muted">
          {q ? "Ningún caso coincide." : "Este cliente no tiene casos abiertos o ganados: busca por número o nombre."}
        </p>
      ) : (
        <ul className="max-h-60 space-y-px overflow-y-auto">
          {matches.map((c) => (
            <li key={c.opportunity_id}>
              <button
                type="button"
                className="block min-h-8 w-full break-words rounded-md px-2 py-1.5 text-left text-[12.5px] text-ink hover:bg-canvas-sunken focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-600 disabled:text-ink-faint"
                disabled={c.opportunity_id === current}
                onClick={() => onPick(c.opportunity_id)}
              >
                {caseLabel(c)}
                {c.opportunity_id === current ? " (actual)" : ""}
              </button>
            </li>
          ))}
        </ul>
      )}
      {current ? (
        <Button variant="quiet" className="w-full" onClick={() => onPick(null)}>
          Quitar el caso
        </Button>
      ) : null}
    </div>
  );
}
