/**
 * How «Rentabilidad» and «Economía de la cotización» name what the API computed
 * (`v2/quotes/economics.py`). Words only: every amount is the API's string, formatted by
 * `formatMoney` / `formatPct`; nothing is added up here.
 */
import type { ImportCostKind, QuoteEconomics } from "./quoteApi";

/** The import-cost kinds in the order they are shown, with their Spanish names. */
export const IMPORT_COST_KINDS: { kind: ImportCostKind; label: string }[] = [
  { kind: "freight", label: "Flete" },
  { kind: "agent", label: "Agente de aduana" },
  { kind: "customs", label: "Derechos y aduana" },
  { kind: "transport", label: "Transporte local" },
  { kind: "other", label: "Otros gastos" },
];

/** The margin's place on the target band (Preferencias), in words and a colour; the words carry it on their own. */
export const MARGIN_STATE_TEXT: Record<string, { label: string; cls: string }> = {
  no_cost: { label: "Sin margen", cls: "text-ink-muted" },
  loss: { label: "Pérdida", cls: "text-bad" },
  below_target: { label: "Bajo el objetivo", cls: "text-warn" },
  on_target: { label: "En rango", cls: "text-good" },
  above_target: { label: "Sobre el objetivo", cls: "text-info" },
};

export function marginStateText(state: string | null | undefined): { label: string; cls: string } {
  return MARGIN_STATE_TEXT[state ?? ""] ?? { label: state ?? "", cls: "text-ink" };
}

/** «Completa», «Falta el costo de 1 línea», «Sin costo». */
export function coverageText(c: QuoteEconomics["cost_coverage"]): string {
  if (c.state === "full") return "Completa";
  if (c.state === "none") return "Sin costo";
  const n = c.lines_without_cost;
  return `Falta el costo de ${n} ${n === 1 ? "línea" : "líneas"}`;
}

/** Whether an amount string is zero ("0", "0.00"): a kind with nothing is not listed. */
export function isZeroAmount(amount: string | null | undefined): boolean {
  return amount == null || /^-?0*(\.0*)?$/.test(amount.trim());
}
