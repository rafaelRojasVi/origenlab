/**
 * Exchange rates on the Resumen: the Banco Central's dólar observado, euro and UF in pesos, as
 * `GET /v2/workspace/fx` serves them. Display only; a quote records its own rate.
 */

export interface FxRate {
  code: "USD" | "EUR" | "UF";
  label: string;
  clp: number;
  /** The day the figure is for (Chile), `YYYY-MM-DD`. */
  as_of: string;
}

export interface FxResponse {
  source: string;
  source_label: string;
  source_url: string;
  rates: FxRate[];
  fetched_at: string;
  /** The source failed on the last refresh; these are the previous figures. */
  stale: boolean;
}

const CURRENCY_PREFIX = /^(usd|eur|uf|clp|us\$|\$)\s*/i;

/**
 * An amount typed the Chilean way ("1.250", "1.250,5") or plainly ("1250.5"). A dot followed by
 * groups of three digits is a thousands separator; a comma is the decimal mark. Anything
 * ambiguous or negative is refused (null) rather than guessed.
 */
export function parseAmount(text: string): number | null {
  let s = text.trim().replace(/\s+/g, "");
  s = s.replace(CURRENCY_PREFIX, "").replace(/^\$/, "");
  if (!/^[\d.,]+$/.test(s)) return null;
  if ((s.match(/,/g) ?? []).length > 1) return null;
  let normalized: string;
  if (s.includes(",")) {
    const [whole, decimals] = s.split(",");
    if (!/^(\d{1,3}(\.\d{3})*|\d+)$/.test(whole) || !/^\d+$/.test(decimals)) return null;
    normalized = `${whole.replace(/\./g, "")}.${decimals}`;
  } else if (/^\d{1,3}(\.\d{3})+$/.test(s)) {
    normalized = s.replace(/\./g, "");
  } else if (/^\d+(\.\d+)?$/.test(s)) {
    normalized = s;
  } else {
    return null;
  }
  const value = Number(normalized);
  return Number.isFinite(value) ? value : null;
}

export function toClp(amount: number, rate: number): number {
  return Math.round(amount * rate);
}

const PESOS = new Intl.NumberFormat("es-CL", { maximumFractionDigits: 0 });
const RATE = new Intl.NumberFormat("es-CL", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function fmtClp(value: number): string {
  return `$${PESOS.format(value)}`;
}

export function fmtRate(value: number): string {
  return `$${RATE.format(value)}`;
}
