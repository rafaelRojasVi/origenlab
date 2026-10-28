import { useEffect, useRef, useState } from "react";
import { useMayRunCaseCommands } from "../caseCommands";
import { fetchPipeline } from "../crmApi";
import type { CrmSection } from "../crmRoute";
import type { OpportunityCardData } from "../crmTypes";
import { formatMoney } from "../money";
import { Badge, Button, EmptyState, PageHeader, Panel, ResourceGate, SearchInput, Segmented, Skeleton, StatLine, fmtDate, toast, type Tone } from "../ui";
import { useResource } from "../useResource";
import {
  fetchInvoices,
  importInvoices,
  invoiceRefusal,
  markInvoicePaid,
  markInvoiceUnpaid,
  setInvoiceCase,
  type AgeBucket,
  type Invoice,
  type InvoiceList,
  type InvoiceStatus,
} from "./invoiceApi";

/**
 * «Pagos»: the facturas Tatiana issued on the SII portal, the case each belongs to, and what is still
 * owed. SII has no API for the sales register, so the month's «Registro de Ventas» CSV is uploaded
 * here («Subir CSV del SII»); uploading a month again adds only the new folios. The upload attaches a
 * factura to its case when exactly one case of the client has a quote of that total; the rest say
 * «Asignar caso…». SII never knows whether a client paid: «Pagada» (with the day) is a person's act.
 *
 * Every amount is the API's; notas de crédito are netted into their factura's saldo by the API.
 * At phone width a row stacks its cells; nothing scrolls sideways.
 */
type Navigate = (section: CrmSection, id?: string | null) => void;
type Filter = "por_cobrar" | "pagadas" | "todas";

const DOC_LABEL: Record<number, string> = { 33: "Factura", 34: "Factura exenta", 56: "Nota de débito", 61: "Nota de crédito" };
const STATUS: Record<InvoiceStatus, { label: string; tone: Tone }> = {
  unpaid: { label: "Por cobrar", tone: "warn" },
  paid: { label: "Pagada", tone: "good" },
  credited: { label: "Anulada por nota de crédito", tone: "neutral" },
  nota: { label: "Nota", tone: "neutral" },
};
const AGE_LABEL: Record<AgeBucket, string> = { "0-30": "0–30 días", "31-60": "31–60 días", "61-90": "61–90 días", "90+": "Más de 90 días" };

const clp = (n: number) => formatMoney(String(n), "CLP") ?? "—";

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

export function PagosPage({ navigate }: { navigate: Navigate }) {
  const [state, reload] = useResource(fetchInvoices);
  const [pipeline] = useResource(fetchPipeline);
  const may = useMayRunCaseCommands();
  const [filter, setFilter] = useState<Filter>("por_cobrar");
  const [uploading, setUploading] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const cases = pipeline.kind === "ready" ? pipeline.data.items : [];

  async function upload(files: FileList | null) {
    const list = files ? Array.from(files) : [];
    if (list.length === 0) return;
    setUploading(true);
    try {
      let imported = 0;
      let already = 0;
      let attached = 0;
      for (const file of list) {
        const r = await importInvoices(file);
        imported += r.imported;
        already += r.already_on_record;
        attached += r.attached_to_case;
      }
      const parts = [`${imported} ${imported === 1 ? "documento nuevo" : "documentos nuevos"}`];
      if (already) parts.push(`${already} ya ${already === 1 ? "estaba" : "estaban"}`);
      if (attached) parts.push(`${attached} ${attached === 1 ? "asignado" : "asignados"} a su caso`);
      toast(parts.join(" · "));
      reload();
    } catch (err) {
      toast(refusalText(err), "bad");
      reload();
    } finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="Pagos"
        subtitle="Las facturas del Registro de Ventas del SII, su caso y lo que falta por cobrar."
        actions={
          may ? (
            <>
              <input
                ref={fileInput}
                type="file"
                accept=".csv,text/csv"
                multiple
                className="sr-only"
                aria-label="Archivo CSV del Registro de Ventas"
                onChange={(e) => void upload(e.target.files)}
              />
              <Button variant="primary" busy={uploading} busyLabel="Subiendo…" onClick={() => fileInput.current?.click()}>
                Subir CSV del SII
              </Button>
            </>
          ) : null
        }
      />
      <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={6} />}>
        {(data) => <Register data={data} filter={filter} setFilter={setFilter} cases={cases} may={may} reload={reload} navigate={navigate} />}
      </ResourceGate>
    </div>
  );
}

function HowToDownload() {
  return (
    <ol className="mx-auto mt-2 max-w-md list-decimal space-y-0.5 pl-5 text-left text-xs text-ink-muted">
      <li>Entra a sii.cl → Servicios online → Factura electrónica → Registro de Compras y Ventas.</li>
      <li>Elige el mes y pulsa «Consultar».</li>
      <li>Abre la pestaña «VENTA» y pulsa «Descargar Detalles».</li>
      <li>Sube aquí el archivo RCV_VENTA_….csv. Puedes subir varios meses a la vez.</li>
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
        <HowToDownload />
      </EmptyState>
    );
  }
  const shown = data.items.filter((i) =>
    filter === "por_cobrar" ? i.status === "unpaid" : filter === "pagadas" ? i.status === "paid" : true,
  );
  const counts = {
    por_cobrar: data.items.filter((i) => i.status === "unpaid").length,
    pagadas: data.items.filter((i) => i.status === "paid").length,
    todas: data.items.length,
  };
  return (
    <>
      <div className="rounded-lg border border-line bg-canvas-raised px-3 py-3">
        <p className="text-xs text-ink-muted">Por cobrar</p>
        <p className="text-2xl font-semibold tabular-nums text-ink">{clp(summary.por_cobrar)}</p>
        <p className="mb-2 text-xs text-ink-muted">
          {summary.unpaid_count} {summary.unpaid_count === 1 ? "factura" : "facturas"} sin pagar
          {summary.last_issued_on ? ` · última factura subida: ${fmtDate(summary.last_issued_on)}` : ""}
        </p>
        <StatLine
          items={[
            ...(Object.keys(AGE_LABEL) as AgeBucket[]).map((b) => ({
              label: AGE_LABEL[b],
              value: clp(summary.by_age[b] ?? 0),
              tone: b === "90+" && summary.by_age[b] > 0 ? ("bad" as const) : b === "61-90" && summary.by_age[b] > 0 ? ("warn" as const) : undefined,
            })),
            { label: "Sin caso", value: String(summary.without_case), tone: summary.without_case > 0 ? ("warn" as const) : undefined },
          ]}
        />
      </div>
      <Segmented<Filter>
        label="Mostrar"
        value={filter}
        onChange={setFilter}
        options={[
          { value: "por_cobrar", label: "Por cobrar", count: counts.por_cobrar },
          { value: "pagadas", label: "Pagadas", count: counts.pagadas },
          { value: "todas", label: "Todas", count: counts.todas },
        ]}
      />
      <Panel bodyClassName="divide-y divide-line">
        {shown.length === 0 ? (
          <p className="px-3 py-4 text-[13px] text-ink-muted">
            {filter === "por_cobrar" ? "Nada por cobrar: todas las facturas están pagadas." : "Ninguna factura en esta vista."}
          </p>
        ) : (
          shown.map((i) => <InvoiceRow key={i.invoice_id} i={i} today={data.today} cases={cases} may={may} reload={reload} navigate={navigate} />)
        )}
      </Panel>
    </>
  );
}

function InvoiceRow({
  i,
  today,
  cases,
  may,
  reload,
  navigate,
}: {
  i: Invoice;
  today: string;
  cases: OpportunityCardData[];
  may: boolean;
  reload: () => void;
  navigate: Navigate;
}) {
  const [busy, setBusy] = useState(false);
  const [paying, setPaying] = useState(false);
  const [paidOn, setPaidOn] = useState(today);
  const [picking, setPicking] = useState(false);
  const status = STATUS[i.status];
  const collectable = i.doc_type === 33 || i.doc_type === 34;

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

  return (
    <div className="flex flex-col gap-2 px-3 py-2.5 sm:flex-row sm:items-start sm:gap-4">
      <div className="min-w-0 sm:w-40 sm:shrink-0">
        <p className="text-[13px] font-semibold text-ink">
          {DOC_LABEL[i.doc_type] ?? `Documento ${i.doc_type}`} {i.folio}
        </p>
        <p className="text-[11.5px] text-ink-muted">
          {fmtDate(i.issued_on)}
          {i.status === "unpaid" ? ` · hace ${i.days_open} ${i.days_open === 1 ? "día" : "días"}` : ""}
        </p>
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-[13px] text-ink">{i.organization?.name ?? i.client_name}</p>
        <p className="text-[11.5px] text-ink-muted">
          RUT {i.client_rut}
          {i.organization ? "" : " · sin organización en el CRM"}
        </p>
        <div className="mt-1">
          {i.opportunity ? (
            <span className="inline-flex flex-wrap items-center gap-1.5 text-[12px]">
              <button type="button" className="text-accent underline-offset-2 hover:underline" onClick={() => navigate("oportunidades", i.opportunity!.id)}>
                {[i.opportunity.quote_number, i.opportunity.title].filter(Boolean).join(" · ")}
              </button>
              {i.case_link === "machine" ? <Badge tone="info" glyph={false} title="La asignó el sistema: coincide el total de la cotización">automático</Badge> : null}
              {may && collectable ? (
                <button type="button" className="text-ink-faint hover:text-ink" disabled={busy} onClick={() => setPicking(true)}>
                  Cambiar
                </button>
              ) : null}
            </span>
          ) : collectable && may ? (
            <button type="button" className="text-[12px] text-warn underline-offset-2 hover:underline" onClick={() => setPicking(true)}>
              Asignar caso…
            </button>
          ) : collectable ? (
            <span className="text-[12px] text-ink-faint">Sin caso</span>
          ) : null}
          {picking ? (
            <CasePicker
              cases={cases}
              organizationId={i.organization?.id ?? null}
              current={i.opportunity?.id ?? null}
              onPick={(id) => void run(() => setInvoiceCase({ ...ref, opportunity_id: id }), id ? "Caso asignado" : "Caso quitado")}
              onClose={() => setPicking(false)}
            />
          ) : null}
        </div>
        {i.notes.length > 0 ? (
          <ul className="mt-1 space-y-0.5">
            {i.notes.map((n) => (
              <li key={n.invoice_id} className="text-[11.5px] text-ink-muted">
                {DOC_LABEL[n.doc_type]} {n.folio} ({fmtDate(n.issued_on)}): {n.doc_type === 61 ? "−" : "+"}
                {clp(n.total_amount)}
              </li>
            ))}
          </ul>
        ) : null}
        {i.reference ? (
          <p className="mt-1 text-[11.5px] text-ink-muted">
            Ajusta {DOC_LABEL[i.reference.doc_type] ?? "el documento"} {i.reference.folio}, que no está en la lista.
          </p>
        ) : null}
      </div>
      <div className="flex flex-col items-start gap-1 sm:w-56 sm:shrink-0 sm:items-end">
        <p className="text-[13px] font-semibold tabular-nums text-ink">{clp(i.balance ?? i.total_amount)}</p>
        {i.balance != null && i.balance !== i.total_amount ? (
          <p className="text-[11px] tabular-nums text-ink-faint">total {clp(i.total_amount)}</p>
        ) : null}
        <Badge tone={status.tone}>
          {status.label}
          {i.status === "paid" && i.paid_on ? ` el ${fmtDate(i.paid_on)}` : ""}
        </Badge>
        {may && i.status === "unpaid" && !paying ? (
          <Button disabled={busy} onClick={() => setPaying(true)}>
            Pagada…
          </Button>
        ) : null}
        {paying ? (
          <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label={`Pago de la factura ${i.folio}`}>
            <input
              type="date"
              aria-label="Día del pago"
              value={paidOn}
              max={today}
              onChange={(e) => setPaidOn(e.target.value)}
              className="h-8 rounded-md border border-line bg-canvas px-2 text-[12.5px] tabular-nums text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
            />
            <Button
              variant="primary"
              busy={busy}
              disabled={!/^\d{4}-\d{2}-\d{2}$/.test(paidOn) || paidOn > today}
              onClick={() => void run(() => markInvoicePaid({ ...ref, paid_on: paidOn }), `Factura ${i.folio} pagada`)}
            >
              Guardar
            </Button>
            <Button variant="quiet" disabled={busy} onClick={() => setPaying(false)}>
              Cancelar
            </Button>
          </div>
        ) : null}
        {may && i.status === "paid" ? (
          <Button variant="quiet" busy={busy} onClick={() => void run(() => markInvoiceUnpaid(ref), `Factura ${i.folio} vuelve a «Por cobrar»`)}>
            Deshacer pago
          </Button>
        ) : null}
      </div>
    </div>
  );
}

const caseLabel = (c: OpportunityCardData): string => {
  const quote = c.latest_revision?.quote_number ?? c.quote_numbers[0] ?? null;
  return [quote, c.organization?.name, c.title].filter(Boolean).join(" · ");
};

/** The client's own cases first; search by quote number, institution or title. */
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
    <div ref={box} role="dialog" aria-label="Asignar caso" className="relative z-20 mt-1 w-full max-w-sm space-y-2 rounded-lg border border-line bg-canvas-raised p-2.5 shadow-lg">
      <SearchInput value={query} onChange={setQuery} placeholder="Número, institución o título…" label="Buscar caso" />
      {matches.length === 0 ? (
        <p className="px-1 text-[12px] text-ink-muted">{q ? "Ningún caso coincide." : "Este cliente no tiene casos abiertos o ganados: busca por número o nombre."}</p>
      ) : (
        <ul className="max-h-60 overflow-y-auto">
          {matches.map((c) => (
            <li key={c.opportunity_id}>
              <button
                type="button"
                className="block w-full truncate rounded-md px-2 py-1.5 text-left text-[12.5px] text-ink hover:bg-canvas-sunken disabled:text-ink-faint"
                disabled={c.opportunity_id === current}
                onClick={() => onPick(c.opportunity_id)}
              >
                {caseLabel(c)}
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
