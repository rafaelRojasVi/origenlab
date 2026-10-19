/**
 * «Gastos ingresados»: an issued revision's import-expense lines (`line_kind` logistics or fee)
 * exactly as the operator typed them — the label (a «Tipo — Detalle» label split into its detail
 * and its type; an older free label kept as typed), how the amount was set (Perfil / Manual /
 * DHL calculado) and the amount in pesos. Internal: sales and admin only, never printed.
 *
 * The data is the workspace's stored lines (`current.lines`); no new API. A peso amount is the
 * stored one; a foreign one is put in pesos the way the engine prices a frozen revision
 * (`inputs.engine_input_from_rows` + `engine.expense_clp`): the currency's rate is the one the
 * first line in that currency carries, the product rounded half away from zero to the peso. The
 * arithmetic is exact (decimal strings as BigInt), so the total equals «Gastos de importación».
 */
import { parseExpenseLabel, expenseKindLabel } from "./expenseKind";
import type { QuoteStoredLine } from "./quoteApi";

const EXPENSE_KINDS = new Set(["logistics", "fee"]);

export const EXPENSE_METHOD_LABEL: Record<string, string> = {
  profile: "Perfil",
  manual: "Manual",
  dhl_calc: "DHL calculado",
};

export interface EnteredExpense {
  lineNo: number;
  /** The detail of a «Tipo — Detalle» label (the type when there is none); an old label as typed. */
  text: string;
  /** The chosen type of a typed label; null for an old free label. */
  tag: string | null;
  method: string;
  currency: string;
  /** As stored, in `currency`. */
  amount: string;
  /** In whole pesos; null when a foreign amount has no stored rate. */
  amountClp: string | null;
}

function parseDecimal(text: string): { n: bigint; scale: number } | null {
  const m = /^(-?)(\d+)(?:\.(\d+))?$/.exec(text.trim());
  if (!m) return null;
  const frac = m[3] ?? "";
  const n = BigInt(m[2] + frac);
  return { n: m[1] ? -n : n, scale: frac.length };
}

/** a × b to the peso, half away from zero (the engine's `peso`); null for a non-decimal. */
export function roundedPesos(a: string, b: string): string | null {
  const x = parseDecimal(a);
  const y = parseDecimal(b);
  if (!x || !y) return null;
  const product = x.n * y.n;
  const scale = x.scale + y.scale;
  if (scale === 0) return product.toString();
  const unit = 10n ** BigInt(scale);
  const abs = product < 0n ? -product : product;
  let whole = abs / unit;
  if ((abs % unit) * 2n >= unit) whole += 1n;
  return (product < 0n && whole !== 0n ? -whole : whole).toString();
}

export function enteredExpenses(lines: QuoteStoredLine[] | null | undefined): { rows: EnteredExpense[]; totalClp: string | null } {
  const ordered = [...(lines ?? [])].sort((a, b) => a.line_no - b.line_no);
  const rates = new Map<string, string>();
  for (const l of ordered) {
    if (l.cost_currency && l.cost_currency !== "CLP" && l.fx_rate != null && !rates.has(l.cost_currency)) rates.set(l.cost_currency, String(l.fx_rate));
  }
  const rows = ordered
    .filter((l) => EXPENSE_KINDS.has(l.line_kind))
    .map((l): EnteredExpense => {
      const parsed = parseExpenseLabel(l.description ?? "");
      const typed = parsed.typed && parsed.kind;
      const currency = l.cost_currency ?? "CLP";
      const amount = l.unit_cost == null ? "0" : String(l.unit_cost);
      const rate = currency === "CLP" ? "1" : rates.get(currency);
      return {
        lineNo: l.line_no,
        text: typed ? parsed.detail || expenseKindLabel(parsed.kind!) : (l.description ?? "").trim() || "—",
        tag: typed && parsed.detail ? expenseKindLabel(parsed.kind!) : null,
        method: EXPENSE_METHOD_LABEL[l.expense_method ?? "manual"] ?? l.expense_method ?? "Manual",
        currency,
        amount,
        amountClp: rate == null ? null : roundedPesos(amount, rate),
      };
    });
  const totalClp = rows.some((r) => r.amountClp == null)
    ? null
    : rows.reduce((sum, r) => sum + BigInt(r.amountClp!), 0n).toString();
  return { rows, totalClp };
}
