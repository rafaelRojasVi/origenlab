/**
 * «Qué cambió» between two revisions of one quotation, read from what each PDF says
 * (`RevisionCard.contents`). Lines are matched by `model ?? item ?? description`, ignoring case
 * (a line with none of them by its `line_no`, shown as «Línea N»);
 * amounts are decimal strings, compared as numbers and only formatted here.
 */
import type { QuoteLine, RevisionCard } from "./crmTypes";
import { formatMoney } from "./money";

const UNREAD = "PDF sin leer: no se puede comparar";
const SAME = "Sin cambios en líneas ni neto";

function present(s: string | null | undefined): string | null {
  const t = (s ?? "").trim();
  return t ? t : null;
}

function lineName(l: QuoteLine): string {
  return present(l.model) ?? present(l.item) ?? present(l.description) ?? `Línea ${l.line_no}`;
}

/** The matching key: the name, ignoring case; an unnamed line by its number. */
function lineKey(l: QuoteLine): string {
  const name = present(l.model) ?? present(l.item) ?? present(l.description);
  return name ? `n:${name.toLowerCase()}` : `#${l.line_no}`;
}

function differs(a: string | null, b: string | null): boolean {
  if (a == null || b == null) return (a == null) !== (b == null);
  const x = Number(a);
  const y = Number(b);
  return Number.isFinite(x) && Number.isFinite(y) ? x !== y : a.trim() !== b.trim();
}

function qtyText(q: string | null): string {
  if (q == null) return "—";
  const n = Number(q);
  return Number.isFinite(n) ? n.toLocaleString("es-CL", { maximumFractionDigits: 3 }) : q;
}

function money(amount: string | null, currency: string | null): string {
  return formatMoney(amount, currency) ?? "—";
}

/** The lines by name, in order; a name printed twice pairs up in order of appearance. */
function byName(lines: QuoteLine[]): Map<string, QuoteLine[]> {
  const out = new Map<string, QuoteLine[]>();
  for (const l of lines) {
    const key = lineKey(l);
    out.set(key, [...(out.get(key) ?? []), l]);
  }
  return out;
}

export function revisionChanges(prev: RevisionCard, next: RevisionCard): string[] {
  const a = prev.contents;
  const b = next.contents;
  if (!a || !b) return [UNREAD];

  const neto: string[] = [];
  const added: string[] = [];
  const removed: string[] = [];
  const qty: string[] = [];
  const price: string[] = [];

  if (differs(a.net_total, b.net_total)) {
    neto.push(`Neto ${money(a.net_total, a.currency)} → ${money(b.net_total, b.currency)}`);
  }

  const before = byName(a.lines);
  const after = byName(b.lines);
  for (const [key, nextLines] of after) {
    const prevLines = before.get(key) ?? [];
    nextLines.forEach((l, i) => {
      const old = prevLines[i];
      const name = lineName(l);
      if (!old) {
        added.push(`+ ${name}`);
        return;
      }
      if (differs(old.qty, l.qty)) qty.push(`${name}: cantidad ${qtyText(old.qty)} → ${qtyText(l.qty)}`);
      if (differs(old.unit_price, l.unit_price)) {
        price.push(`${name}: precio ${money(old.unit_price, a.currency)} → ${money(l.unit_price, b.currency)}`);
      }
    });
  }
  for (const [key, prevLines] of before) {
    const kept = after.get(key)?.length ?? 0;
    prevLines.slice(kept).forEach((l) => removed.push(`− ${lineName(l)}`));
  }

  const out = [...neto, ...added, ...removed, ...qty, ...price];
  return out.length ? out : [SAME];
}
