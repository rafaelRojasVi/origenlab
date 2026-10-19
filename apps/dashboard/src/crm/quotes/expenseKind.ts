/**
 * A manual import expense's «Tipo» (Flete, Agente de aduana, Derechos y aduana, Transporte local,
 * Otro), chosen in «Gastos de importación» and stored in the expense's label as «Tipo — Detalle»
 * (or the type alone). The API's `quoting.expenses.expense_category` reads that type first, so
 * «Economía de la cotización» splits the expense by what the operator chose, never by a guess.
 *
 * An older label typed freely («DHL Envio») has no type: the editor shows the one the API's guesser
 * reads in it (`guessExpenseKind`, a port checked against the same vectors, `expenseKindVectors.json`)
 * and keeps the label as typed until the operator changes the row.
 */
import type { ImportCostKind } from "./quoteApi";

export const EXPENSE_KINDS: { kind: ImportCostKind; label: string }[] = [
  { kind: "freight", label: "Flete" },
  { kind: "agent", label: "Agente de aduana" },
  { kind: "customs", label: "Derechos y aduana" },
  { kind: "transport", label: "Transporte local" },
  { kind: "other", label: "Otro" },
];

/** Between the type and its detail (`quoting.expenses.EXPENSE_TYPE_SEPARATOR`). */
export const EXPENSE_TYPE_SEPARATOR = " — ";

const KIND_LABEL = new Map(EXPENSE_KINDS.map((k) => [k.kind, k.label]));
const LABEL_KIND = new Map(EXPENSE_KINDS.map((k) => [k.label.toLowerCase(), k.kind]));

export function expenseKindLabel(kind: ImportCostKind): string {
  return KIND_LABEL.get(kind) ?? "Otro";
}

/** The longest label the API takes (`DraftExpenseLine.label`). */
export const MAX_EXPENSE_LABEL = 120;

/**
 * The label stored for a chosen type and an optional detail. A detail too long to fit after the
 * type (an old free label of 101–120 characters) is cut with «…», so the row still saves.
 */
export function encodeExpenseLabel(kind: ImportCostKind, detail: string): string {
  const d = detail.trim();
  const type = expenseKindLabel(kind);
  if (!d) return type;
  const head = `${type}${EXPENSE_TYPE_SEPARATOR}`;
  const room = MAX_EXPENSE_LABEL - head.length;
  return d.length <= room ? `${head}${d}` : `${head}${d.slice(0, room - 1).trimEnd()}…`;
}

// `quoting.expenses._LABEL_CATEGORY`, in order: a word the label starts with decides, else a word in it.
const LABEL_KIND_WORDS: [string, ImportCostKind][] = [
  ["flete", "freight"], ["freight", "freight"], ["courier", "freight"], ["dhl", "freight"],
  ["envío", "freight"], ["envio", "freight"], ["fedex", "freight"], ["ups", "freight"],
  ["agente", "agent"], ["despacho", "agent"], ["desaduanaje", "agent"],
  ["derecho", "customs"], ["aduana", "customs"], ["arancel", "customs"],
  ["transporte", "transport"], ["traslado", "transport"],
];
const WHOLE_WORD = new Set(["ups", "dhl"]);
// Python's `\b` is Unicode-aware; here a word character is any letter or digit (or `_`).
const W = "[\\p{L}\\p{N}_]";
const wordRe = (word: string, anchored: boolean) =>
  new RegExp(`${anchored ? "^" : `(?<!${W})`}${word}${WHOLE_WORD.has(word) ? `(?!${W})` : ""}`, "u");
const START = LABEL_KIND_WORDS.map(([w, k]) => [wordRe(w, true), k] as const);
const ANYWHERE = LABEL_KIND_WORDS.map(([w, k]) => [wordRe(w, false), k] as const);

/** The type the API reads in a free label (no parameter, no typed prefix). */
export function guessExpenseKind(label: string): ImportCostKind {
  const text = label.trim().toLowerCase();
  const typed = typedKind(text);
  if (typed) return typed;
  for (const [re, kind] of START) if (re.test(text)) return kind;
  for (const [re, kind] of ANYWHERE) if (re.test(text)) return kind;
  return "other";
}

function typedKind(lower: string): ImportCostKind | null {
  const at = lower.indexOf(EXPENSE_TYPE_SEPARATOR);
  const head = (at >= 0 ? lower.slice(0, at) : lower).trim();
  return LABEL_KIND.get(head) ?? null;
}

/** A stored label as the editor's row: its type, its detail, and whether the type was chosen. */
export function parseExpenseLabel(label: string): { kind: ImportCostKind | null; detail: string; typed: boolean } {
  const text = label.trim();
  if (!text) return { kind: null, detail: "", typed: false };
  const typed = typedKind(text.toLowerCase());
  if (typed) {
    const at = text.indexOf(EXPENSE_TYPE_SEPARATOR);
    return { kind: typed, detail: at >= 0 ? text.slice(at + EXPENSE_TYPE_SEPARATOR.length).trim() : "", typed: true };
  }
  return { kind: guessExpenseKind(text), detail: text, typed: false };
}
