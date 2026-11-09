/**
 * «Vista previa PDF» — multi-page A4 preview.
 *
 * The quote is laid out as one or more explicit A4 «pages» (white sheets with shadow, grey
 * background between them) so the operator sees where pages will break before saving the PDF.
 *
 * HOW PAGINATION WORKS
 * ─────────────────────
 * 1. A hidden measurement container renders the full quote content with the same font sizes
 *    and cell paddings that the @print stylesheet uses (12 px base, 7 px cell padding).
 * 2. After every render (and again when fonts finish loading) each logical block is measured
 *    with getBoundingClientRect().height.  The pure `paginate()` function distributes the
 *    blocks across A4 pages given their measured heights and the print content area.
 * 3. The paginated result drives the visible «pages» and the print output: each page div
 *    carries `break-after: page` so Chrome's «Guardar como PDF» produces exactly the same
 *    breaks as the preview — no reliance on the browser's own fragmentation algorithm.
 *
 * While pagination has not yet been computed (before the first measurement) the component
 * falls back to a single-sheet render identical to the previous behaviour, so existing tests
 * that run under jsdom (no layout → all heights zero → measurement always produces one page)
 * continue to pass.
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { loadCatalogIndex, type CatalogIndex } from "../catalog/catalogIndex";
import { fitsQr, QrSvg } from "../../lib/qr";
import { formatMoney } from "../money";
import { formatWhatsappNumber, WHATSAPP_NUMBER, whatsappQuoteUrl } from "../company";
import { isWithoutIva, type PrintableQuote, type PrintLine } from "./printableQuote";
import { paginate, PAGE_USABLE_H, BLOCK_GAP_PX, A4_HEIGHT_PX } from "./quotePaginate";
import type { PagedResult } from "./quotePaginate";
import "./quotePrint.css";

/** Who signs and how OrigenLab is reached, printed on every quote. */
export const ISSUER = {
  signer: "Tatiana Vivanco",
  title: "Bioquímico · OrigenLab",
  contactLines: ["Tatiana Vivanco - OrigenLab", "Casa Matriz: Oettinger 51, D206, Valdivia"],
  email: "contacto@origenlab.cl",
  phone: "+56 9 6256 7816",
  site: "www.origenlab.cl",
  hours: "Horario: 09:00-18:00",
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
  const name = q.institution?.name ?? q.recipient?.name;
  const who = name ? ` — ${name}` : "";
  return `Cotización ${q.label}${who}${q.draft ? " (borrador)" : ""}`;
}

// ─── block IDs ────────────────────────────────────────────────────────────────

const BLOCK_HEADER = "header";
const BLOCK_CLIENT = "client";
const BLOCK_THEAD  = "thead";
const BLOCK_TOTALS = "totals";
const BLOCK_COND   = "conditions";
const BLOCK_NOTE   = "note";
const BLOCK_FOOTER = "footer";
const rowBlockId   = (i: number) => `row-${i}`;

// ─── content renderers shared between measurement pass and paginated view ─────

type Money = (v: string | null) => string;

interface SharedProps {
  quote: PrintableQuote;
  money: Money;
  models: Map<string, string>;
  ivaColumns: boolean;
  noIva: boolean;
  date: string | null;
  validUntil: string | null;
  waUrl: string | null;
  qrFits: boolean;
  whatsapp: string | null;
}

function HeaderBlock({ quote, date, validUntil }: Pick<SharedProps, "quote" | "date" | "validUntil">) {
  return (
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
  );
}

function ClientBlock({ quote }: Pick<SharedProps, "quote">) {
  if (!quote.institution && !quote.recipient) return null;
  return (
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
  );
}

function TheadRow({ quote, ivaColumns }: Pick<SharedProps, "quote" | "ivaColumns">) {
  return (
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
  );
}

function BodyRow({ l, money, models, ivaColumns, blockId }: { l: PrintLine; blockId?: string } & Pick<SharedProps, "money" | "models" | "ivaColumns">) {
  const model = l.productId ? models.get(l.productId) : undefined;
  const showModel = model && !l.description.toLowerCase().includes(model.toLowerCase()) ? model : null;
  return (
    <tr data-block-id={blockId}>
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
}

function TotalsBlock({ quote, money, noIva }: Pick<SharedProps, "quote" | "money" | "noIva">) {
  return (
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
  );
}

function ConditionsBlock({ quote }: Pick<SharedProps, "quote">) {
  if (!quote.terms.length) return null;
  return (
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
  );
}

function NoteBlock({ quote }: Pick<SharedProps, "quote">) {
  if (!quote.clientNote) return null;
  return <p className="qp-client-note">{quote.clientNote}</p>;
}

function FooterBlock({ quote, waUrl, qrFits, whatsapp }: Pick<SharedProps, "quote" | "waUrl" | "qrFits" | "whatsapp">) {
  return (
    <footer className="qp-foot qp-avoid">
      <div className="qp-sign" aria-label="Firma">
        <span className="qp-sign-greet">Atentamente,</span>
        <span className="qp-sign-line" aria-hidden="true" />
        <span className="qp-sign-name">{ISSUER.signer}</span>
        <span className="qp-sign-title">{ISSUER.title}</span>
        {quote.draft ? <span className="qp-strong qp-draft-note">Borrador sin aprobar: no válido como cotización.</span> : null}
      </div>
      {waUrl && qrFits ? (
        <a className="qp-wa qp-link" href={waUrl}>
          <QrSvg text={waUrl} size="22mm" />
          <span>Escanee o toque para escribir por WhatsApp sobre esta cotización</span>
        </a>
      ) : null}
      <div className="qp-muted qp-right">
        {ISSUER.contactLines.map((line) => <span key={line}>{line}</span>)}
        <span>
          <a className="qp-link" href={`mailto:${ISSUER.email}`}>{ISSUER.email}</a>
          {" · "}
          <a className="qp-link" href={`tel:${ISSUER.phone.replace(/\s+/g, "")}`}>{ISSUER.phone}</a>
        </span>
        <span>
          <a className="qp-link" href={`https://${ISSUER.site}`}>{ISSUER.site}</a>
          {` · ${ISSUER.hours}`}
        </span>
        {whatsapp && waUrl ? (
          <a className="qp-link" href={waUrl}>WhatsApp {formatWhatsappNumber(whatsapp)}</a>
        ) : null}
      </div>
    </footer>
  );
}

// ─────────────────────────────────────────────────────────────────────────────

type Zoom = "fit" | "full";

// Gap between pages in the preview (screen only; not in print).
const PAGE_GAP_PX   = 24;
const PAGES_PAD_TOP = 24;
const PAGES_PAD_BOT = 48;

export function QuotePrintView({
  quote,
  onClose,
  loadIndex = loadCatalogIndex,
  whatsapp = WHATSAPP_NUMBER,
}: {
  quote: PrintableQuote;
  onClose: () => void;
  loadIndex?: () => Promise<CatalogIndex>;
  /** OrigenLab's WhatsApp number (digits); defaults to the build setting. Null: no line, no QR. */
  whatsapp?: string | null;
}) {
  const [models,     setModels]     = useState<Map<string, string>>(() => new Map());
  const [pagination, setPagination] = useState<PagedResult | null>(null);
  const [zoom,       setZoom]       = useState<Zoom>("fit");
  const [fitScale,   setFitScale]   = useState(1);

  const printRef   = useRef<HTMLButtonElement>(null);
  const measureRef = useRef<HTMLDivElement>(null);
  const viewRef    = useRef<HTMLDivElement>(null);

  const title      = printTitle(quote);
  const noIva      = isWithoutIva(quote.taxRate);
  const ivaColumns = !!quote.tender && !noIva;

  // Document title
  useEffect(() => {
    const before = document.title;
    document.title = title;
    return () => { document.title = before; };
  }, [title]);

  // Catalog index (model numbers)
  useEffect(() => {
    let live = true;
    loadIndex().then(
      (index) => {
        if (!live) return;
        setModels(new Map(index.items.filter((i) => i.model_number).map((i) => [i.id, i.model_number])));
      },
      () => undefined,
    );
    return () => { live = false; };
  }, [loadIndex]);

  // Keyboard close
  useEffect(() => {
    printRef.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Fit-to-viewport scale
  const computeFit = useCallback(() => {
    const vw  = viewRef.current?.clientWidth ?? window.innerWidth;
    const pgW = Math.round(210 * 96 / 25.4); // A4 width in px ≈ 794
    const s   = Math.min(1, (vw - 32) / pgW); // 16 px gutter each side
    setFitScale(s > 0 ? s : 1);
  }, []);

  useEffect(() => {
    computeFit();
    if (typeof ResizeObserver === "undefined") return;
    const obs = new ResizeObserver(computeFit);
    if (viewRef.current) obs.observe(viewRef.current);
    return () => obs.disconnect();
  }, [computeFit]);

  // ── measurement ───────────────────────────────────────────────────────────

  const measure = useCallback(() => {
    const container = measureRef.current;
    if (!container) return;

    const getH = (sel: string): number => {
      const el = container.querySelector<HTMLElement>(sel);
      return el ? el.getBoundingClientRect().height : 0;
    };

    const theadH = getH(`[data-block-id="${BLOCK_THEAD}"]`);
    const blocks: Parameters<typeof paginate>[0] = [];

    const push = (id: string, kind: "normal" | "row" | "avoid", h: number) => {
      if (h > 0) blocks.push({ id, kind, height: h });
    };

    push(BLOCK_HEADER, "normal", getH(`[data-block-id="${BLOCK_HEADER}"]`));
    push(BLOCK_CLIENT, "normal", getH(`[data-block-id="${BLOCK_CLIENT}"]`));

    container.querySelectorAll<HTMLElement>("[data-block-id^=\"row-\"]").forEach((el) => {
      const id = el.getAttribute("data-block-id")!;
      push(id, "row", el.getBoundingClientRect().height);
    });

    push(BLOCK_TOTALS, "avoid", getH(`[data-block-id="${BLOCK_TOTALS}"]`));
    push(BLOCK_COND,   "avoid", getH(`[data-block-id="${BLOCK_COND}"]`));
    push(BLOCK_NOTE,   "avoid", getH(`[data-block-id="${BLOCK_NOTE}"]`));
    push(BLOCK_FOOTER, "avoid", getH(`[data-block-id="${BLOCK_FOOTER}"]`));

    if (blocks.length === 0) {
      // jsdom or first paint: all heights are 0.  One page with no blocks.
      setPagination({ pages: [[]], needsThead: [false] });
      return;
    }

    setPagination(paginate(blocks, PAGE_USABLE_H, theadH, BLOCK_GAP_PX));
  }, []);

  // Re-measure after each render (quote or model map change).
  useLayoutEffect(() => { measure(); }, [measure, quote, models]);

  // Re-measure once fonts have loaded (they can alter line heights).
  useEffect(() => {
    if (typeof document !== "undefined" && document.fonts?.ready) {
      document.fonts.ready.then(measure);
    }
  }, [measure, quote, models]);

  // ── derived values ─────────────────────────────────────────────────────────

  const waUrl = whatsapp ? whatsappQuoteUrl(whatsapp, quote.label) : null;
  const qrFits = useMemo(() => {
    if (!waUrl) return false;
    const fits = fitsQr(waUrl);
    if (!fits && import.meta.env.DEV) {
      console.warn("QuotePrintView: the WhatsApp link does not fit a QR; printing the number only.");
    }
    return fits;
  }, [waUrl]);

  const money      = (v: string | null) => formatMoney(v, quote.currency) ?? "—";
  const date       = longDay(quote.date);
  const validUntil = longDay(quote.validUntil);
  const scale      = zoom === "fit" ? fitScale : 1;

  // Shared prop bag passed to all content renderers.
  const sp: SharedProps = { quote, money, models, ivaColumns, noIva, date, validUntil, waUrl, qrFits, whatsapp };

  // ── render one block by ID ─────────────────────────────────────────────────

  const renderBlock = (id: string): ReactNode => {
    switch (id) {
      case BLOCK_HEADER: return <HeaderBlock key="h" quote={sp.quote} date={sp.date} validUntil={sp.validUntil} />;
      case BLOCK_CLIENT: return <ClientBlock key="c" quote={sp.quote} />;
      case BLOCK_TOTALS: return <TotalsBlock key="t" quote={sp.quote} money={sp.money} noIva={sp.noIva} />;
      case BLOCK_COND:   return <ConditionsBlock key="co" quote={sp.quote} />;
      case BLOCK_NOTE:   return <NoteBlock key="n" quote={sp.quote} />;
      case BLOCK_FOOTER: return <FooterBlock key="f" quote={sp.quote} waUrl={sp.waUrl} qrFits={sp.qrFits} whatsapp={sp.whatsapp} />;
      default: {
        if (id.startsWith("row-")) {
          const idx = Number(id.slice(4));
          const l   = quote.lines[idx];
          return l ? <BodyRow key={id} l={l} money={sp.money} models={sp.models} ivaColumns={sp.ivaColumns} /> : null;
        }
        return null;
      }
    }
  };

  // ── render one A4 page ─────────────────────────────────────────────────────

  const renderPage = (
    pageBlocks: { id: string; kind: string }[],
    hasThead: boolean,
    pageNum: number,
    total: number,
  ) => {
    const content: ReactNode[] = [];
    let rowBuf: string[] = [];

    const flushTable = () => {
      if (!rowBuf.length) return;
      content.push(
        <table key="tbl" className="qp-lines">
          {hasThead ? (
            <thead>
              <TheadRow quote={sp.quote} ivaColumns={sp.ivaColumns} />
            </thead>
          ) : null}
          <tbody>{rowBuf.map(renderBlock)}</tbody>
        </table>,
      );
      rowBuf = [];
    };

    for (const blk of pageBlocks) {
      if (blk.kind === "row") {
        rowBuf.push(blk.id);
      } else {
        flushTable();
        content.push(renderBlock(blk.id));
      }
    }
    flushTable();

    return (
      <article
        key={pageNum}
        className="qp-page"
        aria-label={pageNum === 1 ? title : undefined}
      >
        {quote.draft ? <div className="qp-watermark" aria-hidden="true">BORRADOR</div> : null}
        {content}
        <div className="qp-page-num" aria-hidden="true">
          Página {pageNum} de {total}
        </div>
      </article>
    );
  };

  // ── measurement container ─────────────────────────────────────────────────
  // Off-screen, invisible; uses print-equivalent styles (.qp-measure in CSS).

  const measurementPass = (
    <div ref={measureRef} className="qp-measure" aria-hidden="true">
      <div data-block-id={BLOCK_HEADER}>
        <HeaderBlock quote={sp.quote} date={sp.date} validUntil={sp.validUntil} />
      </div>
      {(quote.institution || quote.recipient) && (
        <div data-block-id={BLOCK_CLIENT}>
          <ClientBlock quote={sp.quote} />
        </div>
      )}
      <table className="qp-lines">
        <thead data-block-id={BLOCK_THEAD}>
          <TheadRow quote={sp.quote} ivaColumns={sp.ivaColumns} />
        </thead>
        <tbody>
          {quote.lines.map((l, i) => (
            <BodyRow
              key={l.no}
              l={l}
              money={sp.money}
              models={sp.models}
              ivaColumns={sp.ivaColumns}
              blockId={rowBlockId(i)}
            />
          ))}
        </tbody>
      </table>
      <div data-block-id={BLOCK_TOTALS}>
        <TotalsBlock quote={sp.quote} money={sp.money} noIva={sp.noIva} />
      </div>
      {quote.terms.length ? (
        <div data-block-id={BLOCK_COND}>
          <ConditionsBlock quote={sp.quote} />
        </div>
      ) : null}
      {quote.clientNote ? (
        <div data-block-id={BLOCK_NOTE}>
          <NoteBlock quote={sp.quote} />
        </div>
      ) : null}
      <div data-block-id={BLOCK_FOOTER}>
        <FooterBlock quote={sp.quote} waUrl={sp.waUrl} qrFits={sp.qrFits} whatsapp={sp.whatsapp} />
      </div>
    </div>
  );

  // ── paginated view ─────────────────────────────────────────────────────────

  // Fall back to a single-sheet view when:
  //   a) pagination has not been computed yet (first paint), or
  //   b) every measured page is empty — this happens in jsdom where all heights
  //      are 0, so measure() pushes no blocks.  Keeping the full table visible in
  //      the fallback lets existing tests inspect the quote content normally.
  const allPagesEmpty = pagination !== null && pagination.pages.every((p) => p.length === 0);
  const useFallback   = pagination === null || allPagesEmpty;

  const totalPages = useFallback
    ? 1
    : pagination!.pages.filter((p) => p.length > 0).length || 1;

  // Height of the natural (un-scaled) pages region for the scroll-height trick.
  const pagesNaturalH =
    totalPages * A4_HEIGHT_PX +
    Math.max(0, totalPages - 1) * PAGE_GAP_PX +
    PAGES_PAD_TOP +
    PAGES_PAD_BOT;

  let pagesContent: ReactNode;
  if (!useFallback) {
    const activePgs = pagination!.pages.filter((p) => p.length > 0);
    const pgCount   = activePgs.length || 1;
    pagesContent = activePgs.length > 0
      ? activePgs.map((pgBlocks, i) => renderPage(pgBlocks, pagination!.needsThead[i], i + 1, pgCount))
      : renderPage([], false, 1, 1);
  } else {
    // Fallback single-sheet while measurement is pending, or in jsdom (no layout).
    // Keeps existing tests working: the full table content is visible in the DOM.
    pagesContent = (
      <article className="qp-sheet" aria-label={title}>
        {quote.draft ? <div className="qp-watermark" aria-hidden="true">BORRADOR</div> : null}
        <HeaderBlock quote={sp.quote} date={sp.date} validUntil={sp.validUntil} />
        {(quote.institution || quote.recipient) && <ClientBlock quote={sp.quote} />}
        <table className="qp-lines">
          <thead><TheadRow quote={sp.quote} ivaColumns={sp.ivaColumns} /></thead>
          <tbody>
            {quote.lines.map((l) => (
              <BodyRow key={l.no} l={l} money={sp.money} models={sp.models} ivaColumns={sp.ivaColumns} />
            ))}
          </tbody>
        </table>
        <TotalsBlock quote={sp.quote} money={sp.money} noIva={sp.noIva} />
        <ConditionsBlock quote={sp.quote} />
        <NoteBlock quote={sp.quote} />
        <FooterBlock quote={sp.quote} waUrl={sp.waUrl} qrFits={sp.qrFits} whatsapp={sp.whatsapp} />
      </article>
    );
  }

  // ── portal ────────────────────────────────────────────────────────────────
  // The measurement container is a SIBLING of the dialog (not inside it) so
  // testing-library's within(dialog) queries never see it and report duplicates.

  return createPortal(
    <>
      {/* Off-screen measurement pass — outside the dialog so within(dialog) doesn't see it */}
      {measurementPass}

      <div
        className="qp-root"
        role="dialog"
        aria-modal="true"
        aria-label={`Vista previa PDF · ${quote.label}`}
      >
        {/* Toolbar */}
        <div className="qp-toolbar">
          <button type="button" className="qp-btn" onClick={onClose}>
            Volver
          </button>
          <span className="qp-toolbar-note" role={quote.notReady ? "status" : undefined}>
            {quote.notReady ?? (quote.draft ? "Borrador: se imprime marcado «BORRADOR»." : "Lista para enviar al cliente.")}
            <span className="qp-toolbar-hint">
              Al imprimir: Márgenes «Predeterminado» y sin marcar «Encabezados y pies de página».
            </span>
          </span>

          {/* Zoom group */}
          <div className="qp-zoom" role="group" aria-label="Zoom de vista previa">
            <button
              type="button"
              className={`qp-btn qp-btn-zoom${zoom === "fit" ? " qp-btn-zoom-on" : ""}`}
              onClick={() => setZoom("fit")}
              aria-pressed={zoom === "fit"}
            >
              Ajustar
            </button>
            <button
              type="button"
              className={`qp-btn qp-btn-zoom${zoom === "full" ? " qp-btn-zoom-on" : ""}`}
              onClick={() => setZoom("full")}
              aria-pressed={zoom === "full"}
            >
              100 %
            </button>
          </div>

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

        {/* Scrollable viewport */}
        <div ref={viewRef} className="qp-viewport">
          <div
            className="qp-pages"
            style={
              scale !== 1
                ? {
                    transform: `scale(${scale})`,
                    transformOrigin: "top center",
                    // Shrink the viewport's scroll region to match the scaled height so there
                    // is no dead space below the last page.
                    height: `${pagesNaturalH * scale}px`,
                  }
                : undefined
            }
          >
            {pagesContent}
          </div>
        </div>
      </div>
    </>,
    document.body,
  );
}
