/**
 * «Vista previa PDF»: the client's quote as one A4 document, over the whole page, that the browser
 * prints or saves as a PDF («Imprimir / Guardar PDF» → `window.print()`). It is rendered into
 * `document.body` (a portal), so the print stylesheet (`quotePrint.css`) hides everything else —
 * the menu, the module's tabs, the Cotizador — and only the document prints; its header row
 * repeats on each page and lines do not split.
 *
 * It renders a `PrintableQuote` only (see `printableQuote.ts`): no cost, margin, supplier or import
 * expense ever reaches this DOM. The product's model comes from the catalog index when it is known
 * (best effort; without it the line prints its description). A draft is marked «BORRADOR» on every
 * page, so an unapproved quote is never taken for the final one. While it is open the window's
 * title names the quote and its institution, which becomes the saved file's name.
 */
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { loadCatalogIndex, type CatalogIndex } from "../catalog/catalogIndex";
import { formatMoney } from "../money";
import { isWithoutIva, type PrintableQuote } from "./printableQuote";
import "./quotePrint.css";

/** Who signs and how OrigenLab is reached, printed on every quote. */
export const ISSUER = {
  signer: "Tatiana Vivanco",
  title: "Bioquímico · OrigenLab",
  contactLines: [
    "Tatiana Vivanco - OrigenLab",
    "Casa Matriz: Oettinger 51, D206, Valdivia",
    "contacto@origenlab.cl · +56 9 6256 7816",
    "www.origenlab.cl · Horario: 09:00-18:00",
  ],
} as const;

const LOGO = "/logo/origenlab-mark-light.svg";

/** «15 de octubre de 2026» from `YYYY-MM-DD`. */
function longDay(day: string | null): string | null {
  const m = day?.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return null;
  return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])).toLocaleDateString("es-CL", { day: "numeric", month: "long", year: "numeric" });
}

/** A stored quantity reads «2», not «2.000000»: trailing zeros dropped, decimal comma; no arithmetic. */
function qty(v: string | null): string {
  if (v == null || v === "") return "—";
  return v.replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "").replace(".", ",");
}

/** «19 %» from "0.19". */
function taxLabel(rate: string | null): string {
  const n = rate == null ? NaN : Number(rate);
  if (!Number.isFinite(n)) return "IVA 19 %";
  return `IVA ${(Math.round(n * 10000) / 100).toLocaleString("es-CL")} %`;
}

export function printTitle(q: PrintableQuote): string {
  // A persona natural has no institution: the person is the client.
  const name = q.institution?.name ?? q.recipient?.name;
  const who = name ? ` — ${name}` : "";
  return `Cotización ${q.label}${who}${q.draft ? " (borrador)" : ""}`;
}

export function QuotePrintView({
  quote,
  onClose,
  loadIndex = loadCatalogIndex,
}: {
  quote: PrintableQuote;
  onClose: () => void;
  loadIndex?: () => Promise<CatalogIndex>;
}) {
  const [models, setModels] = useState<Map<string, string>>(() => new Map());
  const printRef = useRef<HTMLButtonElement>(null);
  const title = printTitle(quote);
  const noIva = isWithoutIva(quote.taxRate);
  const ivaColumns = !!quote.tender && !noIva;

  useEffect(() => {
    const before = document.title;
    document.title = title;
    return () => {
      document.title = before;
    };
  }, [title]);

  useEffect(() => {
    let live = true;
    loadIndex().then(
      (index) => {
        if (!live) return;
        setModels(new Map(index.items.filter((i) => i.model_number).map((i) => [i.id, i.model_number])));
      },
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [loadIndex]);

  useEffect(() => {
    printRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const money = (v: string | null) => formatMoney(v, quote.currency) ?? "—";
  const date = longDay(quote.date);
  const validUntil = longDay(quote.validUntil);

  return createPortal(
    <div className="qp-root" role="dialog" aria-modal="true" aria-label={`Vista previa PDF · ${quote.label}`}>
      <div className="qp-toolbar">
        <button type="button" className="qp-btn" onClick={onClose}>
          Volver
        </button>
        <span className="qp-toolbar-note" role={quote.notReady ? "status" : undefined}>
          {quote.notReady ?? (quote.draft ? "Borrador: se imprime marcado «BORRADOR»." : "Lista para enviar al cliente.")}
          {/* Chrome prints its own date, title and address only when the dialog's margins are changed or
              «Encabezados y pies de página» is ticked; the page asks for none, but the dialog remembers. */}
          <span className="qp-toolbar-hint">
            Al imprimir: Márgenes «Predeterminado» y sin marcar «Encabezados y pies de página».
          </span>
        </span>
        <button
          ref={printRef}
          type="button"
          className="qp-btn qp-btn-primary"
          disabled={!!quote.notReady}
          onClick={() => window.print()}
        >
          Imprimir / Guardar PDF
        </button>
      </div>

      <article className="qp-sheet" aria-label={title}>
        {quote.draft ? (
          <div className="qp-watermark" aria-hidden="true">
            BORRADOR
          </div>
        ) : null}

        <header className="qp-head">
          <div className="qp-brand">
            <img src={LOGO} alt="" width={44} height={44} />
            <div>
              <div className="qp-brand-name">OrigenLab</div>
              <div className="qp-muted">Equipamiento científico para laboratorios</div>
              {quote.tender ? (
                <>
                  <div className="qp-muted">Oferente: {quote.tender.bidder.legal_name} · RUT {quote.tender.bidder.rut}</div>
                  {quote.tender.code ? <div className="qp-muted">Licitación {quote.tender.code}</div> : null}
                </>
              ) : null}
            </div>
          </div>
          <div className="qp-number">
            <span className="qp-eyebrow">Cotización</span>
            <span className="qp-label">{quote.label}</span>
            {date ? <span className="qp-muted">{date}</span> : null}
            {validUntil ? <span className="qp-muted">Válida hasta el {validUntil}</span> : null}
            {quote.draft ? <span className="qp-draft-tag">BORRADOR · sin aprobar</span> : null}
          </div>
        </header>

        {quote.institution || quote.recipient ? (
          <section className="qp-client" aria-label="Cliente">
            <div>
              <span className="qp-eyebrow-muted">Cliente</span>
              <span className="qp-strong">{quote.institution?.name ?? quote.recipient?.name ?? "—"}</span>
              {quote.institution?.rut ? <span className="qp-muted">RUT {quote.institution.rut}</span> : null}
              {quote.client.address ? <span className="qp-muted">{quote.client.address}</span> : null}
              {quote.client.city ? <span className="qp-muted">{quote.client.city}</span> : null}
            </div>
            {quote.recipient ? (
              <div>
                <span className="qp-eyebrow-muted">Atención</span>
                {/* A contact known only by address has it as name too: print it once. */}
                {quote.recipient.name && quote.recipient.name !== quote.recipient.email ? (
                  <span className="qp-strong">{quote.recipient.name}</span>
                ) : null}
                {quote.client.contactRole || quote.client.department ? (
                  <span className="qp-muted">{[quote.client.contactRole, quote.client.department].filter(Boolean).join(" · ")}</span>
                ) : null}
                {quote.recipient.email ? <span className="qp-muted">{quote.recipient.email}</span> : null}
                {quote.client.phone ? <span className="qp-muted">{quote.client.phone}</span> : null}
              </div>
            ) : null}
          </section>
        ) : null}

        <table className="qp-lines">
          <thead>
            <tr>
              <th scope="col" className="qp-col-no">#</th>
              <th scope="col">Descripción</th>
              <th scope="col" className="qp-num">Cant.</th>
              <th scope="col" className="qp-num">Precio unit.</th>
              {ivaColumns ? (
                <>
                  <th scope="col" className="qp-num">Neto</th>
                  <th scope="col" className="qp-num">{taxLabel(quote.taxRate)}</th>
                </>
              ) : null}
              <th scope="col" className="qp-num">Total</th>
            </tr>
          </thead>
          <tbody>
            {quote.lines.map((l) => {
              const model = l.productId ? models.get(l.productId) : undefined;
              const showModel = model && !l.description.toLowerCase().includes(model.toLowerCase()) ? model : null;
              return (
                <tr key={l.no}>
                  <td className="qp-col-no qp-mono">{String(l.no).padStart(2, "0")}</td>
                  <td>
                    <span className="qp-item">
                      {l.description || "—"}
                      {showModel ? <span className="qp-model"> {showModel}</span> : null}
                    </span>
                    {l.note ? <span className="qp-note">{l.note}</span> : null}
                  </td>
                  <td className="qp-num qp-mono">{qty(l.quantity)}</td>
                  <td className="qp-num qp-mono">{money(l.unitPrice)}</td>
                  {ivaColumns ? (
                    <>
                      <td className="qp-num qp-mono">{money(l.total)}</td>
                      <td className="qp-num qp-mono">{money(l.iva ?? null)}</td>
                      <td className="qp-num qp-mono">{money(l.grossTotal ?? null)}</td>
                    </>
                  ) : (
                    <td className="qp-num qp-mono">{money(l.total)}</td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>

        <dl className="qp-totals" aria-label="Totales">
          {noIva ? null : (
            <>
              <div>
                <dt>Neto</dt>
                <dd className="qp-mono">{money(quote.totals.net)}</dd>
              </div>
              <div>
                <dt>{taxLabel(quote.taxRate)}</dt>
                <dd className="qp-mono">{money(quote.totals.iva)}</dd>
              </div>
            </>
          )}
          <div className="qp-total">
            <dt>Total {quote.currency}{noIva ? " sin IVA" : ""}</dt>
            <dd className="qp-mono">{money(quote.totals.total)}</dd>
          </div>
        </dl>

        {quote.terms.length ? (
          <section aria-label="Condiciones" className="qp-avoid">
            <h2 className="qp-h2">Condiciones</h2>
            <dl className="qp-terms">
              {quote.terms.map((t, i) => (
                <div key={`${t.label}-${i}`}>
                  <dt>{t.label}</dt>
                  <dd>{t.value}</dd>
                </div>
              ))}
            </dl>
          </section>
        ) : null}

        {quote.clientNote ? <p className="qp-client-note">{quote.clientNote}</p> : null}

        {/* Signature left, OrigenLab's contact right, in one row, so a quote fits one page
            (owner, 2026-10-17). Public contact data, as apps/web/src/data/contact.ts. */}
        <footer className="qp-foot qp-avoid">
          <div className="qp-sign" aria-label="Firma">
            <span className="qp-sign-greet">Atentamente,</span>
            <span className="qp-sign-line" aria-hidden="true" />
            <span className="qp-sign-name">{ISSUER.signer}</span>
            <span className="qp-sign-title">{ISSUER.title}</span>
            {quote.draft ? <span className="qp-strong qp-draft-note">Borrador sin aprobar: no válido como cotización.</span> : null}
          </div>
          <div className="qp-muted qp-right">
            {ISSUER.contactLines.map((line) => (
              <span key={line}>{line}</span>
            ))}
          </div>
        </footer>
      </article>
    </div>,
    document.body,
  );
}
