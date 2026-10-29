import { crmHash, type CrmSection } from "../crmRoute";
import type { OpportunityCardData } from "../crmTypes";
import { formatMoney } from "../money";
import { currentRevision, quotesNewestFirst } from "../pages/CaseQuotes";
import { fmtDate } from "../ui";
import type { Invoice } from "./invoiceApi";
import { OpenOcButton, purchaseOrders, revisionNet } from "./InvoiceSheet";

/**
 * «Por facturar» (owner request 2026-10-28: «every OC needs a factura — remind me to issue them»):
 * the won cases that hold a client purchase order and no factura yet, each with its OC one click
 * away and a link straight to the SII's «Emitir factura electrónica». A case leaves the list by
 * itself once its factura is uploaded and attached.
 *
 * Only OCs attached since the first month on record count: before it, a factura may exist that
 * was never uploaded, and the list would nag about cases already billed. Uploading earlier months
 * widens the window by itself.
 */
type Navigate = (section: CrmSection, id?: string | null) => void;

/** The SII's own «Emitir factura electrónica» (free invoicing system): asks for the login first. */
export const SII_EMIT_URL = "https://www1.sii.cl/cgi-bin/Portal001/mipeLaunchPage.cgi?OPCION=33&TIPO=4";

const clp = (n: number) => formatMoney(String(n), "CLP") ?? "—";

export function casesToInvoice(cases: OpportunityCardData[], invoices: Invoice[]): OpportunityCardData[] {
  const firstIssued = invoices.reduce<string | null>((min, i) => (min === null || i.issued_on < min ? i.issued_on : min), null);
  if (firstIssued === null) return [];
  const since = `${firstIssued.slice(0, 7)}-01`;
  const billed = new Set(invoices.map((i) => i.opportunity?.id).filter(Boolean));
  return cases.filter(
    (c) =>
      c.stage === "won" &&
      !billed.has(c.opportunity_id) &&
      purchaseOrders(c).some((d) => d.attached_at.slice(0, 10) >= since),
  );
}

function quotedNet(card: OpportunityCardData): string | null {
  const rev = quotesNewestFirst(card).map(currentRevision).find(Boolean) ?? null;
  const net = rev ? revisionNet(rev) : null;
  return net != null ? `${clp(net)} neto · ${rev!.quote_number}` : rev ? rev.quote_number : null;
}

export function PorFacturar({ cases, navigate }: { cases: OpportunityCardData[]; navigate: Navigate }) {
  if (cases.length === 0) return null;
  return (
    <section aria-labelledby="por-facturar-title" className="rounded-lg border-2 border-warn bg-warn-bg px-4 py-3" data-testid="por-facturar">
      <h2 id="por-facturar-title" className="text-base font-semibold text-ink">
        Por facturar: {cases.length} {cases.length === 1 ? "caso ganado con OC y sin factura" : "casos ganados con OC y sin factura"}
      </h2>
      <p className="mt-0.5 text-sm text-ink-muted">Emite la factura en el SII y sube el CSV del mes: el caso sale solo de esta lista.</p>
      <ul className="mt-3 space-y-2">
        {cases.map((c) => {
          const ocs = purchaseOrders(c);
          const quoted = quotedNet(c);
          return (
            <li key={c.opportunity_id} className="flex flex-wrap items-center gap-3 rounded-lg border border-line bg-canvas-raised px-4 py-3">
              <div className="min-w-0 flex-1 basis-64">
                <p className="break-words text-base font-semibold text-ink">{c.organization?.name ?? "Sin cliente"}</p>
                <a
                  href={crmHash("oportunidades", c.opportunity_id)}
                  onClick={(e) => {
                    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
                    e.preventDefault();
                    navigate("oportunidades", c.opportunity_id);
                  }}
                  className="break-words text-[15px] text-accent underline-offset-2 hover:underline"
                >
                  {c.title}
                </a>
                <p className="text-sm text-ink-muted">
                  {ocs.map((d) => `OC ${d.document_number ?? d.original_filename} (${fmtDate(d.attached_at)})`).join(" · ")}
                  {quoted ? ` · ${quoted}` : ""}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                {ocs[0] ? <OpenOcButton opportunityId={c.opportunity_id} doc={ocs[0]} /> : null}
                <a
                  href={SII_EMIT_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex min-h-11 items-center rounded-md bg-accent px-4 text-[15px] font-medium text-on-solid hover:opacity-90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600"
                >
                  Emitir en el SII <span aria-hidden="true" className="ml-1">↗</span>
                  <span className="sr-only"> (se abre en otra pestaña)</span>
                </a>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
