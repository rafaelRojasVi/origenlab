/**
 * What the client's printed quote says («Vista previa PDF»), and nothing else.
 *
 * `printableQuote` copies, from the workspace (and, for a draft, the editor's live state), only what
 * the client may read: the number, dates, institution, recipient, the item lines' description,
 * quantity, unit price and total, the totals (neto, IVA, total) and the printed conditions. Costs,
 * recargos, margins, suppliers, exchange rates, warnings and import-expense ("logistics") lines are
 * never copied, so the print view cannot put them in the DOM. Every amount is the API's string,
 * only formatted later; the browser computes no price.
 *
 * An issued revision (approved, sent) prints what approval froze: its stored item lines and totals,
 * and the client side from its `party_snapshot`. A draft prints the live editor (its draft and the
 * API's last preview) and is marked «BORRADOR».
 */
import { computedLineFor, type DraftEditorState } from "./draftEditor";
import { type ClientLines, clientLines, PRINTED_TERMS, readSnapshot } from "./IssuedView";
import type { QuoteRecipientPick, QuoteWorkspace } from "./quoteApi";
import { quoteLabel } from "./quoteLabel";

export interface PrintLine {
  no: number;
  description: string;
  productId: string | null;
  note: string | null;
  quantity: string | null;
  unitPrice: string | null;
  total: string | null;
  /** A tender offer only: this line's IVA, and its neto plus IVA. */
  iva?: string | null;
  grossTotal?: string | null;
}

export interface PrintableQuote {
  /** «CN00042A». */
  label: string;
  draft: boolean;
  /** The quote's date, `YYYY-MM-DD` (approval's, or today for a draft). */
  date: string;
  validUntil: string | null;
  currency: string;
  institution: { name: string | null; rut: string | null } | null;
  recipient: { name: string | null; email: string | null } | null;
  /** Cargo, unidad, teléfono, dirección, ciudad: what «Datos del cliente» adds. */
  client: ClientLines;
  lines: PrintLine[];
  totals: { net: string | null; iva: string | null; total: string | null };
  taxRate: string | null;
  terms: { label: string; value: string }[];
  clientNote: string | null;
  approvedBy: string | null;
  /** A tender offer with a bidder: «Oferente» and «Licitación» in the header, IVA per line. Null otherwise. */
  tender: { code: string | null; bidder: { legal_name: string; rut: string } } | null;
  /** Why it cannot be printed yet (prices missing); null when it can. */
  notReady: string | null;
}

const ISSUED = new Set(["approved", "sent"]);

/** Whether «Vista previa PDF» applies to this revision: a draft, or an approved or sent one. */
export function isPrintable(workspace: QuoteWorkspace): boolean {
  const cur = workspace.current;
  if (!cur) return false;
  if (ISSUED.has(cur.status)) return true;
  return cur.status === "draft" && cur.origin === "authored" && !!cur.draft;
}

/** A decimal string as digits and scale, or null. */
function parseDecimal(v: string | null) {
  const m = v?.trim().match(/^(-?)(\d+)(?:\.(\d+))?$/);
  return m ? { neg: m[1] === "-", digits: BigInt(m[2] + (m[3] ?? "")), scale: (m[3] ?? "").length } : null;
}

/** The whole-peso part of a decimal string, as a BigInt (null when it is not a number). */
function wholePesos(v: string | null): bigint | null {
  const d = parseDecimal(v);
  return d ? (d.neg ? -1n : 1n) * (d.digits / 10n ** BigInt(d.scale)) : null;
}

/**
 * Adds the tender's IVA and total to each line: `neto × rate` rounded half up to whole pesos, as the
 * worker's offer_payload does, in exact decimal arithmetic. When those do not add up to the
 * revision's stored IVA (and the lines' netos do add up to the stored neto), the difference is
 * spread one peso at a time over the lines with the largest rounding remainders (ties: the earlier
 * line), so the printed lines sum to the totals block. Otherwise the lines stay as computed.
 */
function withIva(lines: PrintLine[], rate: string | null, storedNet: string | null, storedIva: string | null): PrintLine[] {
  const r = parseDecimal(rate);
  const parts = lines.map((l) => {
    const a = parseDecimal(l.total);
    if (!a || !r || a.neg) return null;
    const divisor = 10n ** BigInt(a.scale + r.scale);
    const product = a.digits * r.digits;
    return { floor: product / divisor, rem: product % divisor, half: (product + divisor / 2n) / divisor, divisor };
  });
  if (parts.some((p) => p == null)) return lines.map((l) => ({ ...l, iva: null, grossTotal: null }));
  const ps = parts as NonNullable<(typeof parts)[number]>[];
  let ivas = ps.map((p) => p.half);
  const netSum = lines.reduce((acc, l) => acc + (wholePesos(l.total) ?? 0n), 0n);
  const net = wholePesos(storedNet);
  const target = wholePesos(storedIva);
  if (net != null && target != null && net === netSum && ivas.reduce((a, b) => a + b, 0n) !== target) {
    const extra = target - ps.reduce((a, p) => a + p.floor, 0n);
    if (extra >= 0n && extra <= BigInt(ps.length)) {
      const order = ps
        .map((p, i) => ({ i, frac: (p.rem * 1000000n) / p.divisor }))
        .sort((x, y) => (y.frac === x.frac ? x.i - y.i : y.frac > x.frac ? 1 : -1));
      const up = new Set(order.slice(0, Number(extra)).map((o) => o.i));
      ivas = ps.map((p, i) => p.floor + (up.has(i) ? 1n : 0n));
    }
  }
  return lines.map((l, i) => ({ ...l, iva: String(ivas[i]), grossTotal: String((wholePesos(l.total) ?? 0n) + ivas[i]) }));
}

const text = (v: unknown): string | null => (typeof v === "string" && v.trim() ? v.trim() : null);

/** Today in Santiago, `YYYY-MM-DD`. */
function today(now: Date): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/Santiago", year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
}

/** A timestamp's Santiago day, or a bare date as it is. */
function dayOf(value: string | null | undefined, now: Date): string {
  if (!value) return today(now);
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? today(now) : today(d);
}

/** Whom a draft goes to, as the case knows them. */
function draftRecipient(ws: QuoteWorkspace, pick: QuoteRecipientPick | null | undefined, participantId: string | null | undefined) {
  const { contacts, participants } = ws.case;
  const pid = pick?.participant_id ?? (pick ? null : participantId);
  if (pid) {
    const c = contacts.find((x) => x.participant_id === pid);
    if (c) return { name: c.name, email: c.email };
    const p = participants.find((x) => x.id === pid);
    return p ? { name: p.name, email: p.email } : null;
  }
  if (pick?.person_id) {
    const c = contacts.find((x) => x.person_id === pick.person_id);
    return c ? { name: c.name, email: c.email } : null;
  }
  if (pick?.contact_point_id) {
    const c = contacts.find((x) => x.contact_point_id === pick.contact_point_id);
    return c ? { name: c.name, email: c.email } : null;
  }
  if (pick && (text(pick.email) || text(pick.name))) return { name: text(pick.name), email: text(pick.email) };
  return null;
}

function termsOf(source: Record<string, unknown>, validityDays: number | null) {
  const out: { label: string; value: string }[] = [];
  for (const [key, label] of PRINTED_TERMS) {
    if (key === "client_notes_es") continue;
    const v = text(source[key]);
    if (v) out.push({ label: label === "Instalación y capacitación" ? "Instalación" : label, value: v });
    if (key === "delivery_terms" && validityDays != null) {
      out.push({ label: "Validez", value: `${validityDays} ${validityDays === 1 ? "día" : "días"}` });
    }
  }
  const extra = Array.isArray(source.terms_extra) ? source.terms_extra : [];
  for (const t of extra) {
    const o = t as { label_es?: unknown; text_es?: unknown } | null;
    const label = text(o?.label_es);
    const value = text(o?.text_es);
    if (label && value) out.push({ label, value });
  }
  return out;
}

export function printableQuote(ws: QuoteWorkspace, live: { state: DraftEditorState; stale: boolean } | null, now = new Date()): PrintableQuote | null {
  const cur = ws.current;
  if (!cur || !isPrintable(ws)) return null;
  const label = quoteLabel({ label: cur.label, quote_number: ws.quote.quote_number, revision_no: cur.revision_no });
  const currency = cur.quote_currency ?? "CLP";
  const tender = ws.tender?.bidder ? { code: ws.tender.code, bidder: ws.tender.bidder } : null;
  const caseInstitution = ws.case.institution ? { name: ws.case.institution.name, rut: ws.case.institution.rut } : null;

  if (ISSUED.has(cur.status)) {
    const snap = readSnapshot(cur.party_snapshot);
    // Import expenses ("logistics") are internal: only the item lines are the client's.
    const lines = (cur.lines ?? [])
      .filter((l) => l.line_kind === "item")
      .sort((a, b) => a.line_no - b.line_no)
      .map((l, i) => ({
        no: i + 1,
        description: l.description ?? "",
        productId: l.product_id,
        note: text(l.line_note_es),
        quantity: l.quantity,
        unitPrice: l.unit_price_qc,
        total: l.line_total_qc,
      }));
    return {
      label,
      draft: false,
      date: dayOf(snap?.approvedAt ?? cur.approved_at, now),
      validUntil: snap?.validUntil ?? cur.valid_until,
      currency,
      institution: snap?.institution ?? caseInstitution,
      recipient: snap?.recipient ?? null,
      client: snap?.client ?? clientLines(null),
      lines: tender ? withIva(lines, cur.tax_rate ?? "0.19", cur.subtotal, cur.tax_total) : lines,
      tender,
      totals: { net: cur.subtotal, iva: cur.tax_total, total: cur.grand_total },
      taxRate: cur.tax_rate,
      terms: termsOf(snap?.terms ?? {}, snap?.validityDays ?? cur.validity_days),
      clientNote: text(snap?.terms.client_notes_es),
      approvedBy: snap?.approvedBy ?? null,
      notReady: null,
    };
  }

  // A draft: the live editor, priced by the API's last preview.
  const draft = live?.state.draft ?? cur.draft!;
  const computed = live ? live.state.computed : (cur.computed ?? null);
  const keys = live?.state.computedKeys ?? [];
  const lines = draft.items.map((item, i) => {
    const priced = live ? computedLineFor(computed, keys, item.uid) : (computed?.lines.find((l) => l.line_no === i + 1) ?? null);
    return {
      no: i + 1,
      description: item.description,
      productId: item.product_id ?? null,
      note: text(item.line_note_es),
      quantity: item.quantity,
      unitPrice: priced?.unit_price ?? null,
      total: priced?.line_total ?? null,
    };
  });
  const unpriced = !computed || lines.some((l) => l.unitPrice == null || l.total == null);
  const notReady = live?.stale ? "Actualizando los precios…" : unpriced ? "Faltan precios: completa el borrador para imprimirlo." : null;
  const terms = draft.terms as unknown as Record<string, unknown>;
  const validity = typeof draft.terms.validity_days === "number" ? draft.terms.validity_days : cur.validity_days;
  // «Datos del cliente» typed on the draft print over what the CRM knows, as approval will freeze them.
  const typed = draft.client ?? {};
  const recipient = draftRecipient(ws, draft.recipient, (draft as { recipient_participant_id?: string | null }).recipient_participant_id ?? cur.recipient_participant_id);
  const institution = caseInstitution || text(typed.institution_name) || text(typed.rut)
    ? { name: text(typed.institution_name) ?? caseInstitution?.name ?? null, rut: text(typed.rut) ?? caseInstitution?.rut ?? null }
    : null;
  return {
    label,
    draft: true,
    date: today(now),
    validUntil: null,
    currency,
    institution,
    recipient: recipient && text(typed.contact_name) ? { ...recipient, name: text(typed.contact_name) } : recipient,
    client: clientLines(typed),
    lines: tender ? withIva(lines, cur.tax_rate ?? "0.19", computed?.totals.net ?? null, computed?.totals.iva ?? null) : lines,
    tender,
    totals: computed ? { net: computed.totals.net, iva: computed.totals.iva, total: computed.totals.total } : { net: null, iva: null, total: null },
    taxRate: cur.tax_rate,
    terms: termsOf(terms, validity ?? null),
    clientNote: text(draft.terms.client_notes_es),
    approvedBy: null,
    notReady,
  };
}
