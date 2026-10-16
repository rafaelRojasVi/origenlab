/**
 * How the Cotizador prints the API's decimal strings and reads what the operator types. Display
 * and input only: no price is calculated here. A ratio (`margin_pct`, `gross_margin`) is shown ×100
 * as a percentage; a typed percentage becomes the API's ratio by moving the decimal point in the
 * string, so «45,5» is exactly "0.455".
 */
/** «28,6 %» from "0.2857": es-CL, one decimal. Null when there is nothing to show. */
export function formatPct(ratio: string | null | undefined): string | null {
  if (ratio == null || ratio === "") return null;
  const n = Number(ratio);
  if (!Number.isFinite(n)) return null;
  return `${(n * 100).toLocaleString("es-CL", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} %`;
}

const PLAIN = /^(\d+)(?:\.(\d*))?$/;

/** Moves the decimal point of a plain non-negative decimal string `shift` places (left when negative). */
function shiftPoint(text: string, shift: number): string | null {
  const m = PLAIN.exec(text);
  if (!m) return null;
  const digits = m[1] + (m[2] ?? "");
  let point = m[1].length + shift;
  let padded = digits;
  if (point <= 0) {
    padded = "0".repeat(1 - point) + digits;
    point = 1;
  } else if (point > padded.length) {
    padded = padded + "0".repeat(point - padded.length);
  }
  const int = padded.slice(0, point).replace(/^0+(?=\d)/, "");
  const frac = padded.slice(point).replace(/0+$/, "");
  return frac ? `${int}.${frac}` : int;
}

/** The recargo input's text for a stored ratio: "0.455" → «45,5». Empty when there is none. */
export function decimalToPercentText(ratio: string | null | undefined): string {
  if (ratio == null || ratio === "") return "";
  const shifted = shiftPoint(ratio.trim(), 2);
  return shifted === null ? ratio : shifted.replace(".", ",");
}

/**
 * What the operator typed as a percentage, as the API's ratio: «45,5» → "0.455". Empty is null
 * (the server's default for the line's type). Anything that is not a number goes through as typed,
 * so the editor's own check names the problem instead of the browser guessing.
 */
export function percentTextToDecimal(text: string): string | null {
  const t = text.trim().replace(",", ".");
  if (!t) return null;
  return shiftPoint(t, -2) ?? text.trim();
}

const MONEY_PREFIX = /^(clp|usd|eur|us\$|€|\$)/i;

/**
 * A typed amount («1.500.000», «$ 1.500.000», «€ 6.900,50», «1299.75») as a decimal string, read
 * as text (no float): a comma is the decimal mark and dots before it group thousands; with no
 * comma, dots in groups of three are thousands, and a single other dot is the decimal mark.
 * Null when empty; anything else goes through as typed, for the editor's check to name.
 */
export function amountTextToDecimal(text: string): string | null {
  const raw = text.trim();
  if (!raw) return null;
  const s = raw.replace(/\s+/g, "").replace(MONEY_PREFIX, "");
  let out: string | null = null;
  if (/^(\d{1,3}(\.\d{3})+|\d+),\d+$/.test(s)) out = s.replace(/\./g, "").replace(",", ".");
  else if (/^\d{1,3}(\.\d{3})+$/.test(s)) out = s.replace(/\./g, "");
  else if (/^\d+(\.\d+)?$/.test(s)) out = s;
  return out === null ? raw : out.replace(/^0+(?=\d)/, "");
}

/** A typed price in the quote's currency: see `amountTextToDecimal`. */
export const priceTextToDecimal = amountTextToDecimal;

/**
 * A typed quantity: a decimal comma becomes the API's point. «1.000» (a dot and exactly three
 * digits) may be a thousand written the Chilean way or one with three decimals; it is not guessed —
 * `problem` asks. Empty stays empty (the editor asks for it).
 */
export function readQuantity(text: string): { value: string; problem: string | null } {
  const t = text.trim();
  if (/^\d{1,3}(\.\d{3})+$/.test(t)) {
    return { value: t, problem: `Usa coma para decimales (1,5) — ¿quisiste decir ${t.replace(/\./g, "")}?` };
  }
  return { value: t.replace(",", "."), problem: null };
}
