/**
 * The case panel's money and quotations (owner request 2026-10-11): what was quoted, for how
 * much, and where its email and files are, without opening the PDF.
 *
 * - `CaseLinks`: Gmail, the case's Drive folder and the vigente PDF, first thing under the title.
 * - `CaseMoneySummary`: the neto of the vigente revisions, large; IVA and total small (CLP only).
 * - `QuoteList`: one block per quotation, its vigente revision's equipment lines and totals, older
 *   revisions folded.
 *
 * Amounts come from the API as decimal strings (`QuoteContents`) and are only formatted here.
 * A quotation whose PDF was never read says so instead of showing a number.
 */
import { useState } from "react";
import type { CaseMoney, OpportunityCardData, QuoteCard, QuoteContents, QuoteLine, RevisionCard } from "../crmTypes";
import { inSharedMailbox } from "../gmailLinks";
import { Badge, LOCAL_DRIVE_TITLE, fmtDate, isSafeExternalHref } from "../ui";

/* ── money ───────────────────────────────────────────────────────────────── */

const CURRENCY_PREFIX: Record<string, string> = { CLP: "$", USD: "US$", EUR: "€" };

/** «$ 1.190.000» for CLP (no decimals), «US$ 1.299,75» otherwise; es-CL grouping. */
export function formatMoney(amount: string | null | undefined, currency: string | null | undefined): string | null {
  if (amount == null || amount === "") return null;
  const n = Number(amount);
  if (!Number.isFinite(n)) return null;
  const clp = currency === "CLP";
  const text = n.toLocaleString("es-CL", {
    minimumFractionDigits: clp ? 0 : 2,
    maximumFractionDigits: clp ? 0 : 2,
  });
  return `${CURRENCY_PREFIX[currency ?? ""] ?? currency ?? ""} ${text}`.trim();
}

/** «1», «2,5»: a quantity without trailing zeros. */
function formatQty(qty: string | null): string | null {
  if (qty == null) return null;
  const n = Number(qty);
  return Number.isFinite(n) ? n.toLocaleString("es-CL", { maximumFractionDigits: 3 }) : null;
}

/** The revision a quotation stands on: the vigente one, else the newest. */
export function currentRevision(q: QuoteCard): RevisionCard | null {
  const revs = [...q.revisions].sort((a, b) => b.revision_no - a.revision_no);
  return revs.find((r) => r.is_active) ?? revs[0] ?? null;
}

/* ── links ───────────────────────────────────────────────────────────────── */

function LinkButton({ href, children, label, title }: { href: string | null; children: string; label: string; title?: string }) {
  const base =
    "inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs font-medium transition-colors duration-150";
  if (!href || !isSafeExternalHref(href)) {
    return (
      <span className={`${base} cursor-not-allowed border-dashed border-line-strong text-ink-faint`} title={title ?? `${children}: no disponible`}>
        {children}
      </span>
    );
  }
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      aria-label={label}
      title={title}
      className={`${base} border-line bg-canvas-raised text-ink hover:border-brand-600/50 hover:bg-brand-50 hover:text-brand-900 active:translate-y-px`}
    >
      {children}
      <span aria-hidden="true" className="text-[10px] text-ink-faint">↗</span>
    </a>
  );
}

/** The case's newest Gmail message: the vigente quotation's email, else the newest captured one. */
export function caseGmailUrl(card: OpportunityCardData): string | null {
  const rev = card.latest_revision;
  const mail = card.last_contact?.inbound && card.last_contact?.outbound
    ? (Date.parse(card.last_contact.inbound.at ?? "") > Date.parse(card.last_contact.outbound.at ?? "")
        ? card.last_contact.inbound
        : card.last_contact.outbound)
    : card.last_contact?.inbound ?? card.last_contact?.outbound ?? null;
  return inSharedMailbox(mail?.url ?? rev?.gmail?.url ?? null);
}

export function CaseLinks({ card }: { card: OpportunityCardData }) {
  const pdf = card.quotes.map(currentRevision).find((r) => r?.drive) ?? null;
  return (
    <nav aria-label="Enlaces del caso" className="flex flex-wrap gap-2" data-testid="case-links">
      <LinkButton href={caseGmailUrl(card)} label="Abrir el último correo del caso en Gmail">
        Gmail
      </LinkButton>
      <LinkButton href={card.drive_folder?.url ?? null} label="Abrir la carpeta del caso en Drive" title={card.drive_folder ? LOCAL_DRIVE_TITLE : undefined}>
        Carpeta Drive
      </LinkButton>
      <LinkButton
        href={pdf?.drive?.file_url ?? null}
        label={pdf ? `Abrir el PDF ${pdf.quote_number} r${pdf.revision_no} en Drive` : "PDF vigente"}
        title={pdf ? LOCAL_DRIVE_TITLE : undefined}
      >
        {pdf ? `PDF ${pdf.quote_number}` : "PDF"}
      </LinkButton>
    </nav>
  );
}

/* ── the money summary ───────────────────────────────────────────────────── */

export function CaseMoneySummary({ card }: { card: OpportunityCardData }) {
  const money: CaseMoney[] = card.money ?? [];
  const read = money.filter((m) => m.currency !== "unread");
  const unread = Number(money.find((m) => m.currency === "unread")?.net_total ?? 0);
  if (card.quotes.length === 0) return null;
  // IVA and total per currency come from the vigente revisions' own contents (CLP only).
  const clpGross = card.quotes
    .map(currentRevision)
    .filter((r): r is RevisionCard => !!r && !!r.is_active && r.contents?.currency === "CLP")
    .reduce(
      (acc, r) => ({
        tax: acc.tax + Number(r.contents?.tax_total ?? 0),
        gross: acc.gross + Number(r.contents?.gross_total ?? 0),
        ok: acc.ok && r.contents?.gross_total != null,
      }),
      { tax: 0, gross: 0, ok: true },
    );
  return (
    <section aria-label="Monto cotizado" className="rounded-lg border border-line bg-canvas-raised px-4 py-3" data-testid="case-money">
      <p className="text-[11px] font-medium text-ink-muted">
        Cotizado, neto
      </p>
      {read.length === 0 ? (
        <p className="mt-1 text-sm text-ink-muted">Las cotizaciones de este caso aún no se han leído.</p>
      ) : (
        read.map((m) => (
          <div key={m.currency} className="mt-0.5">
            <p className="text-[28px] font-semibold leading-tight tracking-tight text-ink tabular-nums">
              {formatMoney(m.net_total, m.currency)}
            </p>
            {m.currency === "CLP" && clpGross.ok && clpGross.gross > 0 ? (
              <p className="mt-0.5 text-xs text-ink-muted tabular-nums">
                IVA {formatMoney(String(clpGross.tax), "CLP")}
                <span className="mx-1.5 text-ink-faint">|</span>
                Total {formatMoney(String(clpGross.gross), "CLP")}
              </p>
            ) : null}
          </div>
        ))
      )}
      {unread > 0 && read.length > 0 ? (
        <p className="mt-1.5 text-[11px] text-warn">
          {unread === 1 ? "1 cotización sin leer no está sumada." : `${unread} cotizaciones sin leer no están sumadas.`}
        </p>
      ) : null}
    </section>
  );
}

/* ── one quotation ───────────────────────────────────────────────────────── */

function lineTitle(l: QuoteLine): string {
  const desc = (l.description ?? "").trim();
  const model = (l.model ?? "").trim();
  if (model && desc && !desc.toLowerCase().includes(model.toLowerCase())) return `${model} · ${desc}`;
  return desc || model || "Ítem sin descripción";
}

function LineRow({ l, currency }: { l: QuoteLine; currency: string | null }) {
  const qty = formatQty(l.qty);
  const unit = formatMoney(l.unit_price, currency);
  const total = formatMoney(l.line_total, currency);
  const meta = qty && unit && qty !== "1" ? `${qty} × ${unit}` : null;
  return (
    <li className={`grid grid-cols-[1fr_auto] gap-x-3 py-1.5 ${l.optional ? "opacity-60" : ""}`}>
      <div className="min-w-0">
        <p className="text-[13px] leading-snug text-ink">
          {lineTitle(l)}
          {l.optional ? <span className="ml-1.5 text-[11px] font-medium text-ink-muted">Opcional</span> : null}
        </p>
        {meta ? <p className="mt-0.5 text-[11px] text-ink-muted tabular-nums">{meta}</p> : null}
      </div>
      <p className="text-right text-[13px] font-medium text-ink tabular-nums">{total ?? ""}</p>
    </li>
  );
}

function Contents({ c }: { c: QuoteContents }) {
  const shown = c.lines.slice(0, 8);
  const [all, setAll] = useState(false);
  const lines = all ? c.lines : shown;
  return (
    <div>
      {c.lines.length > 0 ? (
        <ul className="divide-y divide-line/70">
          {lines.map((l) => (
            <LineRow key={l.line_no} l={l} currency={c.currency} />
          ))}
        </ul>
      ) : (
        <p className="py-1.5 text-xs text-ink-muted">El PDF se leyó, pero no tiene líneas registradas.</p>
      )}
      {c.lines.length > shown.length ? (
        <button type="button" className="mt-1 text-xs font-medium text-brand-700 hover:underline" onClick={() => setAll(!all)}>
          {all ? "Mostrar menos" : `Ver ${c.lines.length - shown.length} líneas más`}
        </button>
      ) : null}
      <dl className="mt-2 grid grid-cols-[1fr_auto] gap-x-3 gap-y-0.5 border-t border-line pt-2 text-xs tabular-nums">
        <dt className="text-ink-muted">Neto</dt>
        <dd className="text-right font-semibold text-ink">{formatMoney(c.net_total, c.currency) ?? "Sin dato"}</dd>
        {c.tax_total ? (
          <>
            <dt className="text-ink-muted">IVA 19%</dt>
            <dd className="text-right text-ink-muted">{formatMoney(c.tax_total, c.currency)}</dd>
            <dt className="text-ink-muted">Total</dt>
            <dd className="text-right text-ink">{formatMoney(c.gross_total, c.currency)}</dd>
          </>
        ) : null}
      </dl>
    </div>
  );
}

function RevisionLinks({ rev }: { rev: RevisionCard }) {
  const gmail = rev.gmail ? inSharedMailbox(rev.gmail.url) ?? rev.gmail.url : null;
  return (
    <p className="flex flex-wrap gap-x-3 gap-y-1 text-[11px]">
      {rev.drive && isSafeExternalHref(rev.drive.file_url) ? (
        <a className="font-medium text-brand-700 hover:underline" href={rev.drive.file_url} target="_blank" rel="noopener noreferrer" title={LOCAL_DRIVE_TITLE} aria-label={`Abrir PDF r${rev.revision_no} en Drive`}>
          PDF r{rev.revision_no} ↗
        </a>
      ) : null}
      {gmail && isSafeExternalHref(gmail) ? (
        <a className="font-medium text-brand-700 hover:underline" href={gmail} target="_blank" rel="noopener noreferrer" aria-label={`Abrir correo de r${rev.revision_no} en Gmail`}>
          Correo r{rev.revision_no} ↗
        </a>
      ) : null}
    </p>
  );
}

function revisionStatus(rev: RevisionCard): { label: string; tone: "good" | "bad" | "neutral" | "brand" } {
  if (rev.status === "void") return { label: "Anulada", tone: "bad" };
  if (rev.superseded_by_revision_no) return { label: `Reemplazada por r${rev.superseded_by_revision_no}`, tone: "neutral" };
  return rev.status === "sent" ? { label: "Enviada", tone: "good" } : { label: rev.status, tone: "neutral" };
}

function QuoteBlock({ q }: { q: QuoteCard }) {
  const cur = currentRevision(q);
  const older = q.revisions.filter((r) => r.revision_id !== cur?.revision_id).sort((a, b) => b.revision_no - a.revision_no);
  const net = cur?.contents ? formatMoney(cur.contents.net_total, cur.contents.currency) : null;
  const status = cur ? revisionStatus(cur) : null;
  return (
    <article className="rounded-lg border border-line bg-canvas-raised" data-testid="quote-block">
      <header className="flex items-start gap-3 px-3 pt-3">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-1.5">
            <span className="font-mono text-sm font-semibold text-ink">{q.quote_number}</span>
            {cur ? <span className="text-xs text-ink-muted">r{cur.revision_no}, {fmtDate(cur.sent_at)}</span> : null}
            {status ? (
              <Badge tone={status.tone} glyph={false}>
                {status.label}
              </Badge>
            ) : null}
          </p>
        </div>
        {net ? (
          <p className="ml-auto whitespace-nowrap text-right text-sm font-semibold text-ink tabular-nums">
            {net}
          </p>
        ) : null}
      </header>
      <div className="px-3 pb-3 pt-2">
        {cur?.contents ? (
          <Contents c={cur.contents} />
        ) : (
          <p className="rounded-md bg-canvas-sunken/70 px-2.5 py-2 text-xs text-ink-muted">
            PDF aún no leído.
          </p>
        )}
        {cur ? (
          <div className="mt-2.5">
            <RevisionLinks rev={cur} />
          </div>
        ) : null}
        {older.length > 0 ? (
          <details className="mt-2.5 group">
            <summary className="cursor-pointer select-none text-[11px] font-medium text-ink-muted hover:text-ink">
              {older.length === 1 ? "1 revisión anterior" : `${older.length} revisiones anteriores`}
            </summary>
            <ul className="mt-1.5 space-y-2 border-l border-line pl-3">
              {older.map((r) => {
                const st = revisionStatus(r);
                return (
                  <li key={r.revision_id}>
                    <p className="flex flex-wrap items-center gap-1.5 text-[11px] text-ink-muted">
                      <span className="font-semibold text-ink">r{r.revision_no}</span>
                      {fmtDate(r.sent_at)}
                      <Badge tone={st.tone} glyph={false}>
                        {st.label}
                      </Badge>
                      {r.contents?.net_total ? (
                        <span className="tabular-nums">{formatMoney(r.contents.net_total, r.contents.currency)}</span>
                      ) : null}
                    </p>
                    <RevisionLinks rev={r} />
                  </li>
                );
              })}
            </ul>
          </details>
        ) : null}
      </div>
    </article>
  );
}

export function QuoteList({ card }: { card: OpportunityCardData }) {
  if (card.quotes.length === 0) {
    return (
      <p className="rounded-lg border border-dashed border-line-strong px-3 py-3 text-xs text-ink-muted">
        Este caso aún no tiene cotizaciones registradas.
      </p>
    );
  }
  return (
    <div className="space-y-3">
      {card.quotes.map((q) => (
        <QuoteBlock key={q.quote_id} q={q} />
      ))}
    </div>
  );
}
