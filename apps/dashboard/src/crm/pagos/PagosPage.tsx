import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useMayRunCaseCommands } from "../caseCommands";
import { fetchPipeline } from "../crmApi";
import { crmHash, type CrmSection } from "../crmRoute";
import type { OpportunityCardData } from "../crmTypes";
import { formatMoney } from "../money";
import { Badge, Button, EmptyState, buttonClass, PageHeader, Panel, ResourceGate, SearchInput, Segmented, Skeleton, StatLine, fmtDate, toast, type Tone } from "../ui";
import { useResource } from "../useResource";
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

/**
 * «Pagos»: the facturas Tatiana issued on the SII portal, the case each belongs to, and what is still
 * owed. SII has no API for the sales register, so the month's «Registro de Ventas» CSV is uploaded
 * here («Subir CSV del SII»); uploading a month again adds only the new folios. The upload guesses each
 * factura's case (the client by RUT or name; the quote with its neto, else the client's only won
 * case) and tries again on later uploads; the rest say «Asignar caso…». SII never knows whether a client paid: «Pagada» (with the day) is a person's act.
 *
 * A row reads client → amount owed → how old, the three things a collector looks for; «Por cobrar»
 * lists the oldest first. Every amount is the API's (notas de crédito are netted into their factura's
 * saldo there). Age is flagged in words as well as colour. At phone width a row stacks its cells;
 * nothing scrolls sideways.
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
const AGE_LABEL: Record<AgeBucket, string> = { "0-30": "0–30 días", "31-60": "31–60 días", "61-90": "61–90 días", "90+": "Más de 90 días" };

/** A text-sized action (case link, «Cambiar»): at least 24 px tall, with a visible focus ring. */
const LINK_ACTION =
  "inline-flex min-h-6 items-center rounded px-1 -mx-1 text-[12.5px] underline-offset-2 hover:underline " +
  "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-brand-600 disabled:opacity-50";

const clp = (n: number) => formatMoney(String(n), "CLP") ?? "—";
const collectable = (i: Invoice) => i.doc_type === 33 || i.doc_type === 34;

function refusalText(err: unknown): string {
  const r = invoiceRefusal(err);
  if (!r) return "No se pudo guardar. Revisa la conexión y vuelve a intentar.";
  switch (r.code) {
    case "sii_file_invalid":
      return r.message;
    case "sii_file_too_large":
      return "El archivo pesa más de 2 MB: no parece el Registro de Ventas de un mes.";
    case "invoice_version_conflict":
      return "Alguien cambió esta factura recién. Se recargó la lista: vuelve a intentarlo.";
    case "invoice_already_paid":
      return "Esta factura ya estaba marcada como pagada.";
    case "invoice_not_paid":
      return "Esta factura no estaba marcada como pagada.";
    case "invoice_not_collectable":
      return "Una nota de crédito o débito no se cobra: ajusta la factura que menciona.";
    case "paid_on_in_future":
      return "La fecha de pago no puede ser futura.";
    case "case_not_found":
      return "Ese caso ya no existe.";
    default:
      return r.message || "No se pudo guardar.";
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

export function PagosPage({ navigate }: { navigate: Navigate }) {
  const [state, reload] = useResource(fetchInvoices);
  const [pipeline] = useResource(fetchPipeline);
  const may = useMayRunCaseCommands();
  const [filter, setFilter] = useState<Filter>("por_cobrar");
  const [uploading, setUploading] = useState(false);
  const [lastUpload, setLastUpload] = useState<{ text: string; ok: boolean } | null>(null);
  const [help, setHelp] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const cases = pipeline.kind === "ready" ? pipeline.data.items : [];

  async function upload(files: FileList | null) {
    const list = files ? Array.from(files) : [];
    if (list.length === 0) return;
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
      <PageHeader
        title="Pagos"
        subtitle="Las facturas del Registro de Ventas del SII, su caso y lo que falta por cobrar."
        actions={
          <>
            <Button variant="quiet" aria-expanded={help} aria-controls="pagos-help" onClick={() => setHelp((h) => !h)}>
              ¿Cómo descargo el archivo?
            </Button>
            {may ? (
              <>
                <input
                  ref={fileInput}
                  type="file"
                  accept=".csv,text/csv"
                  multiple
                  className="sr-only"
                  tabIndex={-1}
                  aria-label="Archivo CSV del Registro de Ventas"
                  onChange={(e) => void upload(e.target.files)}
                />
                <Button variant="primary" busy={uploading} busyLabel="Subiendo…" onClick={() => fileInput.current?.click()}>
                  Subir CSV del SII
                </Button>
              </>
            ) : null}
          </>
        }
      />
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
  // What is owed reads oldest first; every other view keeps the newest first.
  if (filter === "por_cobrar") shown.sort((a, b) => b.days_open - a.days_open || a.folio - b.folio);
  const count = (f: Filter) => data.items.filter(pick[f]).length;
  const late = (summary.by_age["61-90"] ?? 0) + (summary.by_age["90+"] ?? 0);

  return (
    <>
      <section aria-label="Resumen de lo que falta por cobrar" className="rounded-lg border border-line bg-canvas-raised px-4 py-3">
        <p className="text-xs text-ink-muted">Por cobrar</p>
        <p className="text-2xl font-semibold tabular-nums text-ink">{clp(summary.por_cobrar)}</p>
        <p className="mb-2 text-xs text-ink-muted">
          {summary.unpaid_count} {summary.unpaid_count === 1 ? "factura sin pagar" : "facturas sin pagar"}
          {late > 0 ? <span className="text-warn"> · {clp(late)} con más de 60 días</span> : null}
          {summary.last_issued_on ? ` · factura más reciente: ${fmtDate(summary.last_issued_on)}` : ""}
        </p>
        <StatLine
          items={(Object.keys(AGE_LABEL) as AgeBucket[]).map((b) => ({
            label: AGE_LABEL[b],
            value: clp(summary.by_age[b] ?? 0),
            tone: b === "90+" && summary.by_age[b] > 0 ? ("bad" as const) : b === "61-90" && summary.by_age[b] > 0 ? ("warn" as const) : undefined,
          }))}
        />
        {summary.without_case > 0 ? (
          <button type="button" className={`${LINK_ACTION} mt-2 text-warn`} onClick={() => setFilter("sin_caso")}>
            <span aria-hidden="true" className="mr-1">!</span>
            {summary.without_case} {summary.without_case === 1 ? "factura sin caso: asígnala" : "facturas sin caso: asígnalas"}
          </button>
        ) : null}
      </section>
      <Segmented<Filter>
        label="Mostrar"
        value={filter}
        onChange={setFilter}
        options={[
          { value: "por_cobrar", label: "Por cobrar", count: count("por_cobrar") },
          { value: "sin_caso", label: "Sin caso", count: count("sin_caso") },
          { value: "pagadas", label: "Pagadas", count: count("pagadas") },
          { value: "todas", label: "Todas", count: count("todas") },
        ]}
      />
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
              <InvoiceRow key={i.invoice_id} i={i} today={data.today} showStatus={filter !== "por_cobrar"} cases={cases} may={may} reload={reload} navigate={navigate} />
            ))}
          </ul>
        )}
      </Panel>
    </>
  );
}

/** How old an unpaid factura is, in words; over 60 days it is flagged, over 90 it is urgent. */
function Age({ days }: { days: number }) {
  const text = `hace ${days} ${days === 1 ? "día" : "días"}`;
  if (days > 90) return <span className="font-medium text-bad"><span aria-hidden="true">! </span>{text}</span>;
  if (days > 60) return <span className="font-medium text-warn"><span aria-hidden="true">! </span>{text}</span>;
  return <span>{text}</span>;
}

function InvoiceRow({
  i,
  today,
  showStatus,
  cases,
  may,
  reload,
  navigate,
}: {
  i: Invoice;
  today: string;
  /** Off inside «Por cobrar», where every row would say the same. */
  showStatus: boolean;
  cases: OpportunityCardData[];
  may: boolean;
  reload: () => void;
  navigate: Navigate;
}) {
  const [busy, setBusy] = useState(false);
  const [paying, setPaying] = useState(false);
  const [paidOn, setPaidOn] = useState(today);
  const [picking, setPicking] = useState(false);
  const payButton = useRef<HTMLButtonElement>(null);
  const caseButton = useRef<HTMLButtonElement>(null);
  const dateId = useId();
  const status = STATUS[i.status];
  const label = `${DOC_LABEL[i.doc_type] ?? `Documento ${i.doc_type}`} ${i.folio}`;
  const validDay = /^\d{4}-\d{2}-\d{2}$/.test(paidOn) && paidOn <= today;

  async function run(action: () => Promise<unknown>, done: string) {
    setBusy(true);
    try {
      await action();
      toast(done);
    } catch (err) {
      toast(refusalText(err), "bad");
    } finally {
      setBusy(false);
      setPaying(false);
      setPicking(false);
      reload();
    }
  }
  const ref = { invoice_id: i.invoice_id, invoice_version: i.version };
  const closePicker = () => {
    setPicking(false);
    caseButton.current?.focus();
  };
  const cancelPaying = () => {
    setPaying(false);
    payButton.current?.focus();
  };

  let caseLine: ReactNode = null;
  if (i.opportunity) {
    caseLine = (
      <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="text-[12px] text-ink-faint">Caso:</span>
        <a
          href={crmHash("oportunidades", i.opportunity.id)}
          className={`${LINK_ACTION} min-w-0 break-words text-left text-accent`}
          onClick={(e) => {
            if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return; // a new tab keeps the browser's own way
            e.preventDefault();
            navigate("oportunidades", i.opportunity!.id);
          }}
        >
          {[i.opportunity.quote_number, i.opportunity.title].filter(Boolean).join(" · ")}
        </a>
        {i.case_link === "machine" ? (
          <Badge tone="info" glyph={false} title="Lo asignó el sistema: el neto calza con la cotización del caso, o es el único caso ganado del cliente">
            automático
          </Badge>
        ) : null}
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

  return (
    <li data-testid="invoice-row" aria-label={`${label}, ${i.organization?.name ?? i.client_name}`} className="flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-start sm:gap-6">
      <div className="min-w-0 flex-1 space-y-1">
        <p className="break-words text-[13.5px] font-semibold text-ink">{i.organization?.name ?? i.client_name}</p>
        <p className="text-[12px] text-ink-muted">
          <span className="text-ink">{label}</span> · {fmtDate(i.issued_on)}
          {i.status === "unpaid" ? <> · <Age days={i.days_open} /></> : null}
        </p>
        <p className="text-[11.5px] text-ink-faint">
          RUT {i.client_rut}
          {i.organization ? "" : " · no está en Organizaciones"}
        </p>
        {caseLine ? <div>{caseLine}</div> : null}
        {picking ? (
          <CasePicker
            cases={cases}
            organizationId={i.organization?.id ?? null}
            current={i.opportunity?.id ?? null}
            onPick={(id) => void run(() => setInvoiceCase({ ...ref, opportunity_id: id }), id ? "Caso asignado" : "Caso quitado")}
            onClose={closePicker}
          />
        ) : null}
        {i.notes.length > 0 ? (
          <ul aria-label="Notas de esta factura" className="space-y-0.5">
            {i.notes.map((n) => (
              <li key={n.invoice_id} className="text-[11.5px] tabular-nums text-ink-muted">
                {DOC_LABEL[n.doc_type]} {n.folio} ({fmtDate(n.issued_on)}): {n.doc_type === 61 ? "−" : "+"}
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
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 sm:w-60 sm:shrink-0 sm:flex-col sm:flex-nowrap sm:items-end sm:justify-start">
        <div className="sm:text-right">
          <p className="text-[15px] font-semibold tabular-nums text-ink">{clp(i.balance ?? i.total_amount)}</p>
          {i.balance != null && i.balance !== i.total_amount ? (
            <p className="text-[11px] tabular-nums text-ink-faint">de {clp(i.total_amount)} facturados</p>
          ) : null}
        </div>
        {showStatus ? (
          <Badge tone={status.tone}>
            {status.label}
            {i.status === "paid" && i.paid_on ? ` el ${fmtDate(i.paid_on)}` : ""}
          </Badge>
        ) : null}
        {may && i.status === "unpaid" && !paying ? (
          <button ref={payButton} type="button" className={buttonClass()} disabled={busy} onClick={() => setPaying(true)}>
            Marcar pagada…
          </button>
        ) : null}
        {paying ? (
          <form
            className="flex w-full flex-col gap-2 rounded-md border border-line bg-canvas p-2.5 sm:items-end"
            aria-label={`Pago de la ${label}`}
            onSubmit={(e) => {
              e.preventDefault();
              if (validDay) void run(() => markInvoicePaid({ ...ref, paid_on: paidOn }), `${label} pagada`);
            }}
            onKeyDown={(e) => e.key === "Escape" && cancelPaying()}
          >
            <label htmlFor={dateId} className="text-[12px] font-medium text-ink">
              ¿Qué día pagó?
            </label>
            <input
              id={dateId}
              name="paid_on"
              type="date"
              autoFocus
              value={paidOn}
              max={today}
              onChange={(e) => setPaidOn(e.target.value)}
              aria-invalid={!validDay || undefined}
              className="h-8 w-full rounded-md border border-line bg-canvas-raised px-2 text-[13px] tabular-nums text-ink focus:border-brand-600 focus:outline-none focus:ring-1 focus:ring-brand-600 sm:w-44"
            />
            {!validDay ? <p className="text-[11.5px] text-bad">Elige un día de hoy o antes.</p> : null}
            <div className="flex gap-2">
              <Button variant="quiet" disabled={busy} onClick={cancelPaying}>
                Cancelar
              </Button>
              <Button type="submit" variant="primary" busy={busy} busyLabel="Guardando…" disabled={!validDay}>
                Guardar pago
              </Button>
            </div>
          </form>
        ) : null}
        {may && i.status === "paid" ? (
          <Button variant="quiet" busy={busy} onClick={() => void run(() => markInvoiceUnpaid(ref), `${label} vuelve a «Por cobrar»`)}>
            Deshacer pago
          </Button>
        ) : null}
      </div>
    </li>
  );
}

const caseLabel = (c: OpportunityCardData): string => {
  const quote = c.latest_revision?.quote_number ?? c.quote_numbers[0] ?? null;
  return [quote, c.organization?.name, c.title].filter(Boolean).join(" · ");
};

/** The client's own cases first; search by quote number, institution or title. Focus starts in the search. */
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
