import { useState } from "react";
import { documentRefusalText, fetchCaseDocumentUrl, ocLabel } from "../caseDocuments";
import { crmHash, type CrmSection } from "../crmRoute";
import type { CaseDocument, OpportunityCardData, RevisionCard } from "../crmTypes";
import { formatMoney } from "../money";
import { CaseLinks, currentRevision, quotesNewestFirst } from "../pages/CaseQuotes";
import { Badge, Button, Drawer, fmtDate, fmtDateTime, type Tone } from "../ui";
import type { Invoice, InvoiceStatus } from "./invoiceApi";

/**
 * «Ver factura»: one factura laid out the way a Chilean factura electrónica reads — the folio box,
 * «Señor(es)» with the RUT, the detail, neto + IVA + total — and beside it what the CRM knows that
 * the SII register does not: the client's purchase order (opened from here), the quotation it
 * bills, the case, its Gmail and Drive.
 *
 * The SII «Registro de Ventas» carries only the header of each document (client, folio, date and
 * amounts); the items and the OC reference live in the factura's own PDF/XML. So the detail shown
 * is the quotation's lines as the quote reader read them off its PDF, and says so.
 */
type Navigate = (section: CrmSection, id?: string | null) => void;

export const DOC_TITLE: Record<number, string> = {
  33: "Factura electrónica",
  34: "Factura no afecta o exenta electrónica",
  56: "Nota de débito electrónica",
  61: "Nota de crédito electrónica",
};
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

const clp = (n: number | string | null | undefined) => (n == null ? "—" : formatMoney(String(n), "CLP") ?? "—");

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

function Row({ label, value, strong = false }: { label: string; value: string; strong?: boolean }) {
  return (
    <div className={`flex items-baseline justify-between gap-4 py-1 ${strong ? "border-t border-line pt-2 font-semibold text-ink" : "text-ink-muted"}`}>
      <dt className="text-[12.5px]">{label}</dt>
      <dd className="text-[13px] tabular-nums text-ink">{value}</dd>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section aria-label={title} className="space-y-2">
      <h3 className="text-[11px] font-semibold uppercase tracking-wide text-ink-faint">{title}</h3>
      {children}
    </section>
  );
}

function OpenOc({ opportunityId, doc }: { opportunityId: string; doc: CaseDocument }) {
  const [opening, setOpening] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function open() {
    setOpening(true);
    setError(null);
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
      setError(documentRefusalText(err));
    } finally {
      setOpening(false);
    }
  }
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-line px-3 py-2">
      <div className="min-w-0">
        <p className="text-[13px] font-medium text-ink">Orden de compra {doc.document_number ? `Nº ${doc.document_number}` : ""}</p>
        <p className="break-words text-[11.5px] text-ink-faint">
          {doc.document_number ? doc.original_filename : ocLabel(doc)} · adjunta el {fmtDate(doc.attached_at)}
        </p>
        {error ? <p className="text-[11.5px] text-bad">{error}</p> : null}
      </div>
      <Button busy={opening} busyLabel="Abriendo…" onClick={() => void open()} aria-label={`Ver la ${ocLabel(doc)}`}>
        Ver OC
      </Button>
    </li>
  );
}

export function InvoiceSheet({
  invoice: i,
  card,
  onClose,
  navigate,
}: {
  invoice: Invoice;
  /** The case's card from the pipeline, when it is loaded. */
  card: OpportunityCardData | null;
  onClose: () => void;
  navigate: Navigate;
}) {
  const title = `${DOC_TITLE[i.doc_type] ?? `Documento ${i.doc_type}`} Nº ${i.folio}`;
  const status = STATUS[i.status];
  const net = i.net_amount + i.exempt_amount;
  const billed = card ? billedRevision(card, net) : null;
  const lines = (billed?.revision.contents?.lines ?? []).filter((l) => !l.optional && l.kind !== "heading");
  const ocs = purchaseOrders(card);
  const goTo = (section: CrmSection, id: string) => (e: React.MouseEvent) => {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
    e.preventDefault();
    onClose();
    navigate(section, id);
  };

  return (
    <Drawer open onClose={onClose} title={title} subtitle={i.organization?.name ?? i.client_name}>
      {/* The folio box every Chilean factura prints top right. */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="rounded-md border-2 border-bad px-4 py-2 text-center" data-testid="folio-box">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-bad">{DOC_TITLE[i.doc_type] ?? "Documento"}</p>
          <p className="text-lg font-bold tabular-nums text-bad">Nº {i.folio}</p>
        </div>
        <div className="space-y-1 text-right">
          <Badge tone={status.tone}>
            {status.label}
            {i.status === "paid" && i.paid_on ? ` el ${fmtDate(i.paid_on)}` : ""}
          </Badge>
          <p className="text-[12px] text-ink-muted">Emitida el {fmtDate(i.issued_on)}</p>
          {i.status === "unpaid" ? (
            <p className={`text-[12px] ${i.days_open > 60 ? "font-medium text-warn" : "text-ink-muted"}`}>
              {i.days_open} {i.days_open === 1 ? "día" : "días"} sin pagar
            </p>
          ) : null}
        </div>
      </div>

      <Section title="Señor(es)">
        <div className="rounded-md border border-line px-3 py-2">
          <p className="break-words text-[13.5px] font-semibold text-ink">{i.client_name}</p>
          <p className="text-[12px] tabular-nums text-ink-muted">RUT {i.client_rut}</p>
          {i.organization ? (
            <a href={crmHash("organizaciones", i.organization.id)} onClick={goTo("organizaciones", i.organization.id)} className="text-[12px] text-accent underline-offset-2 hover:underline">
              En el CRM: {i.organization.name}
            </a>
          ) : (
            <p className="text-[12px] text-ink-faint">No está en Organizaciones.</p>
          )}
        </div>
      </Section>

      <Section title="Detalle">
        {lines.length > 0 && billed ? (
          <>
            <ul className="divide-y divide-line rounded-md border border-line">
              {lines.map((l) => (
                <li key={l.line_no} className="flex items-baseline justify-between gap-3 px-3 py-1.5">
                  <span className="min-w-0 break-words text-[12.5px] text-ink">
                    {l.qty ? <span className="tabular-nums text-ink-muted">{Number(l.qty).toLocaleString("es-CL")} × </span> : null}
                    {[l.brand, l.model, l.description].filter(Boolean).join(" · ") || l.item || "—"}
                  </span>
                  <span className="shrink-0 text-[12.5px] tabular-nums text-ink-muted">{l.line_total ? clp(l.line_total) : ""}</span>
                </li>
              ))}
            </ul>
            <p className="text-[11.5px] text-ink-faint">
              Según la cotización {billed.revision.quote_number}
              {billed.sameNet ? ", que tiene el mismo neto que la factura." : ". El neto de la factura es distinto: puede ser un abono o un ajuste."}
            </p>
          </>
        ) : (
          <p className="text-[12.5px] text-ink-muted">
            El Registro de Ventas del SII no trae los productos de la factura.
            {i.opportunity ? " La cotización del caso aún no se ha leído." : " Asigna el caso para ver lo cotizado."}
          </p>
        )}
      </Section>

      <Section title="Montos">
        <dl>
          {i.exempt_amount > 0 ? <Row label="Exento" value={clp(i.exempt_amount)} /> : null}
          <Row label="Neto" value={clp(i.net_amount)} />
          <Row label="IVA 19 %" value={clp(i.vat_amount)} />
          <Row label="Total" value={clp(i.total_amount)} strong />
          {i.notes.map((n) => (
            <Row
              key={n.invoice_id}
              label={`${DOC_TITLE[n.doc_type] ?? "Nota"} Nº ${n.folio} (${fmtDate(n.issued_on)})`}
              value={`${n.doc_type === 61 ? "−" : "+"}${clp(n.total_amount)}`}
            />
          ))}
          {i.balance != null && (i.notes.length > 0 || i.status === "unpaid") ? (
            <Row label={i.status === "paid" ? "Saldo" : "Por cobrar"} value={clp(i.status === "paid" ? 0 : i.balance)} strong />
          ) : null}
        </dl>
      </Section>

      <Section title="Referencias">
        {i.opportunity ? (
          <div className="space-y-2">
            {ocs.length > 0 ? (
              <ul className="space-y-2" aria-label="Órdenes de compra">
                {ocs.map((d) => <OpenOc key={d.document_id} opportunityId={i.opportunity!.id} doc={d} />)}
              </ul>
            ) : (
              <p className="text-[12.5px] text-ink-muted">El caso no tiene orden de compra adjunta. Se adjunta desde el caso, al marcarlo ganado.</p>
            )}
            <div className="rounded-md border border-line px-3 py-2">
              <p className="text-[11.5px] text-ink-faint">Caso</p>
              <a href={crmHash("oportunidades", i.opportunity.id)} onClick={goTo("oportunidades", i.opportunity.id)} className="break-words text-[13px] font-medium text-accent underline-offset-2 hover:underline">
                {[i.opportunity.quote_number, i.opportunity.title].filter(Boolean).join(" · ")}
              </a>
              <p className="text-[11.5px] text-ink-muted">
                {STAGE[i.opportunity.stage] ?? i.opportunity.stage}
                {billed?.revision.sent_at ? ` · cotización ${billed.revision.quote_number} enviada el ${fmtDateTime(billed.revision.sent_at)}` : ""}
              </p>
              <p className="mt-1 text-[11.5px] text-ink-faint">
                {i.case_link === "machine"
                  ? billed?.sameNet
                    ? "Asignado automáticamente: el neto calza con la cotización."
                    : "Asignado automáticamente: es el único caso ganado del cliente."
                  : "Asignado por una persona."}
              </p>
            </div>
            {card ? <CaseLinks card={card} /> : null}
          </div>
        ) : (
          <p className="text-[12.5px] text-ink-muted">Sin caso: no hay orden de compra ni cotización que mostrar. Usa «Asignar caso…» en la lista.</p>
        )}
      </Section>

      {i.reference ? (
        <Section title="Documento que ajusta">
          <p className="text-[12.5px] text-ink-muted">
            {DOC_TITLE[i.reference.doc_type] ?? "Documento"} Nº {i.reference.folio}
          </p>
        </Section>
      ) : null}
    </Drawer>
  );
}
