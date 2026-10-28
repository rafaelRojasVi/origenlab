import { useId, useState, type ReactNode } from "react";
import { documentRefusalText, fetchCaseDocumentUrl } from "../caseDocuments";
import { crmHash, type CrmSection } from "../crmRoute";
import type { CaseDocument, OpportunityCardData, RevisionCard } from "../crmTypes";
import { formatMoney } from "../money";
import { currentRevision, quotesNewestFirst } from "../pages/CaseQuotes";
import { Button, Drawer, buttonClass, fmtDate, isSafeExternalHref, toast, type Tone } from "../ui";
import { markInvoicePaid, type Invoice, type InvoiceStatus } from "./invoiceApi";

/**
 * «Ver factura»: one factura, read the way the owner asks about it — who owes, how much, since when —
 * in large type first, with «Marcar pagada» right there; then the papers behind it as large cards
 * (the client's purchase order, the quotation, the case), then the amounts and what was sold.
 *
 * The SII «Registro de Ventas» carries only the header of each document (client, folio, date and
 * amounts); the items and the OC reference live in the factura's own PDF/XML. So «Lo que se vendió»
 * is the quotation's lines as the quote reader read them off its PDF, and says so.
 *
 * Sizes follow the readability floor: 16 px for anything read, 14 px for secondary lines, never the
 * faint tone for something the reader needs; every action is at least 44 px tall.
 */
type Navigate = (section: CrmSection, id?: string | null) => void;

export const DOC_TITLE: Record<number, string> = {
  33: "Factura electrónica",
  34: "Factura exenta electrónica",
  56: "Nota de débito electrónica",
  61: "Nota de crédito electrónica",
};
const SHORT: Record<number, string> = { 33: "Factura", 34: "Factura exenta", 56: "Nota de débito", 61: "Nota de crédito" };
const STATUS: Record<InvoiceStatus, { label: string; tone: Tone }> = {
  unpaid: { label: "Por cobrar", tone: "warn" },
  paid: { label: "Pagada", tone: "good" },
  credited: { label: "Anulada por nota de crédito", tone: "neutral" },
  nota: { label: "Nota", tone: "neutral" },
};
const STAGE: Record<string, string> = {
  lead: "Nuevo", qualifying: "Calificando", qualified: "Calificado", quoting: "Cotizando",
  negotiating: "Negociando", won: "Ganado", lost: "Perdido", abandoned: "Abandonado",
};

/** The status, large: the first thing the sheet says. */
const PILL: Partial<Record<Tone, string>> = {
  warn: "bg-warn-bg text-warn",
  good: "bg-good-bg text-good",
  neutral: "bg-canvas-sunken text-ink-muted",
};

const clp = (n: number | string | null | undefined) => (n == null ? "—" : formatMoney(String(n), "CLP") ?? "—");
const BIG_ACTION = "min-h-11 px-4 text-[15px]";

/** A revision's neto: the Cotizador's own, or the one read off the PDF. */
function revisionNet(r: RevisionCard): number | null {
  const n = Number(r.contents?.net_total ?? NaN);
  return r.contents?.currency === "CLP" && Number.isFinite(n) ? Math.round(n) : null;
}

/** The quotation this factura bills: the one whose neto is the factura's, else the case's vigente one. */
export function billedRevision(card: OpportunityCardData, invoiceNet: number): { revision: RevisionCard; sameNet: boolean } | null {
  const all = card.quotes.flatMap((q) => q.revisions);
  const same = all.find((r) => r.status !== "void" && revisionNet(r) !== null && Math.abs((revisionNet(r) ?? 0) - invoiceNet) <= 1);
  if (same) return { revision: same, sameNet: true };
  const current = quotesNewestFirst(card).map(currentRevision).find(Boolean) ?? null;
  return current ? { revision: current, sameNet: false } : null;
}

export function purchaseOrders(card: OpportunityCardData | null): CaseDocument[] {
  return (card?.documents ?? []).filter((d) => d.kind === "purchase_order");
}

function Heading({ children }: { children: ReactNode }) {
  return <h3 className="text-[15px] font-semibold text-ink">{children}</h3>;
}

/** One paper behind the factura: what it is, its number, and the one thing to do with it. */
function PaperCard({ kind, title, detail, action }: { kind: string; title: ReactNode; detail?: ReactNode; action?: ReactNode }) {
  return (
    <li className="flex flex-wrap items-center gap-3 rounded-lg border border-line bg-canvas px-4 py-3">
      <div className="min-w-0 flex-1 basis-56">
        <p className="text-[13px] font-medium text-ink-muted">{kind}</p>
        <p className="break-words text-base font-semibold text-ink">{title}</p>
        {detail ? <p className="mt-0.5 text-sm text-ink-muted">{detail}</p> : null}
      </div>
      {action}
    </li>
  );
}

function OpenOcButton({ opportunityId, doc }: { opportunityId: string; doc: CaseDocument }) {
  const [opening, setOpening] = useState(false);
  async function open() {
    setOpening(true);
    // The tab opens on the click itself (a later window.open would be blocked as a pop-up).
    const tab = window.open("about:blank", "_blank");
    try {
      const url = await fetchCaseDocumentUrl(opportunityId, doc.document_id);
      if (tab) {
        tab.opener = null;
        tab.location.href = url;
      } else window.location.assign(url);
    } catch (err) {
      tab?.close();
      toast(documentRefusalText(err), "bad");
    } finally {
      setOpening(false);
    }
  }
  return (
    <Button className={BIG_ACTION} busy={opening} busyLabel="Abriendo…" onClick={() => void open()}
      aria-label={`Ver la orden de compra ${doc.document_number ?? doc.original_filename}`}>
      Ver OC
    </Button>
  );
}

function PayForm({ invoice: i, today, onDone }: { invoice: Invoice; today: string; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [day, setDay] = useState(today);
  const [busy, setBusy] = useState(false);
  const id = useId();
  const valid = /^\d{4}-\d{2}-\d{2}$/.test(day) && day <= today;
  if (!open) {
    return (
      <Button variant="primary" className={BIG_ACTION} onClick={() => setOpen(true)}>
        Marcar pagada…
      </Button>
    );
  }
  async function save() {
    setBusy(true);
    try {
      await markInvoicePaid({ invoice_id: i.invoice_id, invoice_version: i.version, paid_on: day });
      toast(`${SHORT[i.doc_type] ?? "Factura"} ${i.folio} pagada`);
      onDone();
    } catch {
      toast("No se pudo guardar el pago. Se recargó la lista: vuelve a intentarlo.", "bad");
      onDone();
    } finally {
      setBusy(false);
    }
  }
  return (
    <form
      className="flex w-full flex-wrap items-end gap-3 rounded-lg border border-line bg-canvas p-3"
      aria-label="Registrar el pago"
      onSubmit={(e) => {
        e.preventDefault();
        if (valid) void save();
      }}
      onKeyDown={(e) => e.key === "Escape" && setOpen(false)}
    >
      <div className="flex flex-col gap-1">
        <label htmlFor={id} className="text-sm font-medium text-ink">¿Qué día pagó?</label>
        <input
          id={id}
          type="date"
          autoFocus
          value={day}
          max={today}
          onChange={(e) => setDay(e.target.value)}
          aria-invalid={!valid || undefined}
          className="h-11 rounded-md border border-line bg-canvas-raised px-3 text-base tabular-nums text-ink focus:border-brand-600 focus:outline-none focus:ring-1 focus:ring-brand-600"
        />
      </div>
      <div className="flex gap-2">
        <Button variant="quiet" className={BIG_ACTION} disabled={busy} onClick={() => setOpen(false)}>Cancelar</Button>
        <Button type="submit" variant="primary" className={BIG_ACTION} busy={busy} busyLabel="Guardando…" disabled={!valid}>
          Guardar pago
        </Button>
      </div>
      {!valid ? <p className="w-full text-sm text-bad">Elige un día de hoy o antes.</p> : null}
    </form>
  );
}

export function InvoiceSheet({
  invoice: i,
  card,
  today,
  may,
  onClose,
  onChanged,
  navigate,
}: {
  invoice: Invoice;
  /** The case's card from the pipeline, when it is loaded. */
  card: OpportunityCardData | null;
  today: string;
  /** The operator may mark it paid. */
  may: boolean;
  onClose: () => void;
  /** After a save: reload the list. */
  onChanged: () => void;
  navigate: Navigate;
}) {
  const docTitle = DOC_TITLE[i.doc_type] ?? `Documento ${i.doc_type}`;
  const status = STATUS[i.status];
  const net = i.net_amount + i.exempt_amount;
  const billed = card ? billedRevision(card, net) : null;
  const lines = (billed?.revision.contents?.lines ?? []).filter((l) => !l.optional && l.kind !== "heading");
  const ocs = purchaseOrders(card);
  const pdf = billed?.revision.drive?.file_url ?? null;
  const owed = i.status === "unpaid" ? i.balance ?? i.total_amount : null;
  const goTo = (section: CrmSection, id: string) => (e: React.MouseEvent) => {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
    e.preventDefault();
    onClose();
    navigate(section, id);
  };

  return (
    <Drawer open onClose={onClose} title={`${docTitle} Nº ${i.folio}`} subtitle={`Emitida el ${fmtDate(i.issued_on)}`}>
      {/* The answer first: who, how much, since when — and the one thing to do about it. */}
      <section aria-label="Resumen" className="space-y-3" data-testid="invoice-summary">
        <div className="flex flex-wrap items-center gap-2">
          <span className={`inline-flex min-h-8 items-center rounded-full px-3 text-[15px] font-semibold ${PILL[status.tone] ?? PILL.neutral}`}>
            {status.label}
            {i.status === "paid" && i.paid_on ? ` el ${fmtDate(i.paid_on)}` : ""}
          </span>
          {i.status === "unpaid" ? (
            <span className={`text-[15px] ${i.days_open > 60 ? "font-semibold text-warn" : "text-ink-muted"}`}>
              hace {i.days_open} {i.days_open === 1 ? "día" : "días"}
            </span>
          ) : null}
        </div>
        <div>
          <p className="break-words text-xl font-semibold leading-snug text-ink">{i.organization?.name ?? i.client_name}</p>
          <p className="text-[15px] tabular-nums text-ink-muted">
            {i.organization && i.organization.name !== i.client_name ? `${i.client_name} · ` : ""}RUT {i.client_rut}
          </p>
        </div>
        <div>
          <p className="text-sm text-ink-muted">{owed != null ? "Te debe" : "Total facturado"}</p>
          <p className="text-[40px] font-bold leading-none tracking-tight tabular-nums text-ink">{clp(owed ?? i.total_amount)}</p>
        </div>
        {may && i.status === "unpaid" ? <PayForm invoice={i} today={today} onDone={onChanged} /> : null}
      </section>

      <section aria-label="Documentos" className="space-y-2">
        <Heading>Documentos</Heading>
        {i.opportunity ? (
          <ul className="space-y-2">
            {ocs.map((d) => (
              <PaperCard
                key={d.document_id}
                kind="Orden de compra del cliente"
                title={d.document_number ? `Nº ${d.document_number}` : d.original_filename}
                detail={`Adjunta el ${fmtDate(d.attached_at)}`}
                action={<OpenOcButton opportunityId={i.opportunity!.id} doc={d} />}
              />
            ))}
            {ocs.length === 0 ? (
              <PaperCard kind="Orden de compra del cliente" title="Sin OC adjunta" detail="Se adjunta desde el caso, al marcarlo ganado." />
            ) : null}
            {billed ? (
              <PaperCard
                kind="Cotización"
                title={billed.revision.quote_number}
                detail={billed.sameNet ? "Mismo neto que la factura" : "El neto de la factura es distinto: puede ser un abono o un ajuste"}
                action={
                  pdf && isSafeExternalHref(pdf) ? (
                    <a href={pdf} target="_blank" rel="noopener noreferrer" className={`${buttonClass()} ${BIG_ACTION}`}>
                      Ver PDF
                    </a>
                  ) : null
                }
              />
            ) : null}
            <PaperCard
              kind="Caso"
              title={i.opportunity.title}
              detail={`${STAGE[i.opportunity.stage] ?? i.opportunity.stage} · ${
                i.case_link === "machine"
                  ? billed?.sameNet
                    ? "asignado solo: el neto calza con la cotización"
                    : "asignado solo: es el único caso ganado del cliente"
                  : "asignado por una persona"
              }`}
              action={
                <a href={crmHash("oportunidades", i.opportunity.id)} onClick={goTo("oportunidades", i.opportunity.id)} className={`${buttonClass()} ${BIG_ACTION}`}>
                  Abrir caso
                </a>
              }
            />
          </ul>
        ) : (
          <p className="text-base text-ink-muted">Sin caso todavía: asígnalo desde la lista con «Asignar caso…» y aquí aparecerán la OC y la cotización.</p>
        )}
      </section>

      <section aria-label="Montos" className="space-y-2">
        <Heading>Montos</Heading>
        <dl className="rounded-lg border border-line px-4 py-2 text-base">
          {i.exempt_amount > 0 ? <AmountRow label="Exento" value={clp(i.exempt_amount)} /> : null}
          <AmountRow label="Neto" value={clp(i.net_amount)} />
          <AmountRow label="IVA (19 %)" value={clp(i.vat_amount)} />
          <AmountRow label="Total" value={clp(i.total_amount)} strong />
          {i.notes.map((n) => (
            <AmountRow
              key={n.invoice_id}
              label={`${SHORT[n.doc_type] ?? "Nota"} ${n.folio} (${fmtDate(n.issued_on)})`}
              value={`${n.doc_type === 61 ? "−" : "+"}${clp(n.total_amount)}`}
            />
          ))}
        </dl>
      </section>

      <section aria-label="Lo que se vendió" className="space-y-2">
        <Heading>Lo que se vendió</Heading>
        {lines.length > 0 && billed ? (
          <>
            <ul className="divide-y divide-line rounded-lg border border-line">
              {lines.map((l) => (
                <li key={l.line_no} className="flex items-baseline justify-between gap-4 px-4 py-2.5">
                  <span className="min-w-0 break-words text-base text-ink">
                    {l.qty ? <span className="tabular-nums text-ink-muted">{Number(l.qty).toLocaleString("es-CL")} × </span> : null}
                    {[l.brand, l.model, l.description].filter(Boolean).join(" · ") || l.item || "—"}
                  </span>
                  <span className="shrink-0 text-base tabular-nums text-ink">{l.line_total ? clp(l.line_total) : ""}</span>
                </li>
              ))}
            </ul>
            <p className="text-sm text-ink-muted">Según la cotización {billed.revision.quote_number}. El SII no envía los productos de la factura.</p>
          </>
        ) : (
          <p className="text-base text-ink-muted">
            El SII no envía los productos de la factura
            {i.opportunity ? ", y la cotización del caso aún no se ha leído." : "; asigna el caso para ver lo cotizado."}
          </p>
        )}
      </section>

      {i.reference ? (
        <section aria-label="Documento que ajusta" className="space-y-2">
          <Heading>Documento que ajusta</Heading>
          <p className="text-base text-ink">{DOC_TITLE[i.reference.doc_type] ?? "Documento"} Nº {i.reference.folio}</p>
        </section>
      ) : null}
    </Drawer>
  );
}

function AmountRow({ label, value, strong = false }: { label: string; value: string; strong?: boolean }) {
  return (
    <div className={`flex items-baseline justify-between gap-4 py-1.5 ${strong ? "mt-1 border-t border-line pt-2.5 text-lg font-semibold" : ""}`}>
      <dt className={strong ? "text-ink" : "text-ink-muted"}>{label}</dt>
      <dd className="tabular-nums text-ink">{value}</dd>
    </div>
  );
}
