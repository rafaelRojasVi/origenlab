/**
 * The Cotizador's company preferences: which cost parameter each field of «Preferencias» is, and
 * how its value is shown, edited and read back. Display and input only — every value is the API's
 * (`GET /v2/catalog/parameters`), and nothing here is a float: a percentage moves the decimal point
 * in the string («12,5» ⇄ "0.125"), an amount drops its thousands dots («1.234.567» ⇄ "1234567").
 *
 * The keys and their ranges are the API's (`v2/catalog/keys.py`): percentages are ratios, the
 * `profile_*_clp` amounts are pesos, validity is whole days 1–365, the seed a whole number
 * 1–99999 (administration only), and the other `quote_default_*` keys are printed text up to 1000
 * characters, sent as `value_text`. The column is `numeric(18,6)`: a percentage takes at most four
 * decimals (six on the ratio) and 999 %, an amount at most twelve digits, so nothing is rounded
 * silently or overflows. The margin band's two ends are checked against each other
 * (`bandProblem`) before anything is sent. An unset parameter is not an error: the engine falls back
 * (docs/OPERATIONS.md §14.8), and the field says to what — in its hint, and as its placeholder.
 */
import { formatMoney } from "../money";
import { decimalToPercentText, percentTextToDecimal, amountTextToDecimal } from "./quoteFormat";

export type PreferenceKind = "percent" | "clp" | "days" | "seed" | "text";

export interface PreferenceField {
  key: string;
  label: string;
  kind: PreferenceKind;
  hint?: string;
  /** What the engine uses while the parameter is unset. */
  unset: string;
  /** That fallback as the empty field's placeholder («40 %»); none when nothing falls back. */
  placeholder?: string;
  /** Only administration may change it (`role_may_not_set_numbering` for anyone else). */
  adminOnly?: boolean;
}

export interface PreferenceGroup {
  title: string;
  note?: string;
  fields: PreferenceField[];
}

const MARKUP_HINT = "sobre el costo con gastos";

export const PREFERENCE_GROUPS: PreferenceGroup[] = [
  {
    title: "Recargos por defecto",
    note: "Se escriben en cada línea nueva que tiene costo y no tiene recargo; cambiarlos no recalcula un borrador guardado. Una línea «Otro» usa el recargo de Accesorios.",
    fields: [
      { key: "default_markup_equipment", label: "Equipos", kind: "percent", hint: MARKUP_HINT, unset: "se usa 40 %", placeholder: "40 %" },
      { key: "default_markup_accessory", label: "Accesorios", kind: "percent", hint: MARKUP_HINT, unset: "se usa 45 %", placeholder: "45 %" },
      { key: "default_markup_consumable", label: "Insumos", kind: "percent", hint: MARKUP_HINT, unset: "se usa 45 %", placeholder: "45 %" },
      { key: "default_markup_spare_part", label: "Repuestos", kind: "percent", hint: MARKUP_HINT, unset: "se usa 45 %", placeholder: "45 %" },
      { key: "default_markup_service", label: "Servicios", kind: "percent", hint: MARKUP_HINT, unset: "se usa 45 %", placeholder: "45 %" },
    ],
  },
  {
    title: "Margen objetivo",
    fields: [
      { key: "margin_target_min", label: "Mínimo", kind: "percent", hint: "bajo esto: «Bajo el objetivo»", unset: "se usa 25 %", placeholder: "25 %" },
      { key: "margin_target_max", label: "Máximo", kind: "percent", hint: "sobre esto: «Sobre el objetivo»", unset: "se usa 45 %", placeholder: "45 %" },
      {
        key: "price_deviation_warn_pct",
        label: "Desviación de precio",
        kind: "percent",
        // Stored for the warning a later delivery builds (docs/STATUS.md §2.7.123 «Not built»).
        hint: "se guarda; el aviso todavía no está construido",
        unset: "no hay aviso",
      },
    ],
  },
  {
    title: "Gastos de importación (perfil)",
    note: "Montos fijos en pesos; una cotización por perfil los lleva cuando alguna línea viene del extranjero.",
    fields: [
      { key: "profile_freight_clp", label: "Flete", kind: "clp", unset: "se omite, con un aviso" },
      { key: "profile_agent_clp", label: "Agente de aduana", kind: "clp", unset: "se omite, con un aviso" },
      { key: "profile_customs_clp", label: "Derechos y aduana", kind: "clp", unset: "se omite, con un aviso" },
      { key: "profile_transport_clp", label: "Transporte local", kind: "clp", unset: "se omite, con un aviso" },
    ],
  },
  {
    title: "Condiciones por defecto",
    note: "Se copian en cada cotización nueva. Lo que va entre corchetes es un espacio por completar: no se aprueba una cotización que todavía lo tenga.",
    fields: [
      { key: "quote_default_delivery_terms", label: "Plazo de entrega", kind: "text", unset: "queda vacío" },
      { key: "quote_default_validity_days", label: "Validez (días)", kind: "days", unset: "se usa 30 días", placeholder: "30" },
      { key: "quote_default_payment_terms", label: "Forma de pago", kind: "text", unset: "queda vacío" },
      { key: "quote_default_warranty", label: "Garantía", kind: "text", unset: "queda vacío" },
      { key: "quote_default_delivery_place", label: "Lugar de entrega", kind: "text", unset: "queda vacío" },
      { key: "quote_default_installation", label: "Instalación y capacitación", kind: "text", unset: "queda vacío" },
    ],
  },
  {
    title: "Numeración",
    note: "La próxima cotización lleva el mayor entre este número y el más alto ya usado + 1: un valor menor que ése no tiene efecto, y cambiarlo después sólo puede adelantar el inicio. La serie sigue sola desde ahí.",
    fields: [
      {
        key: "quote_number_seed",
        label: "Inicio de la numeración",
        kind: "seed",
        hint: "por ejemplo 42 para 00042-26",
        unset: "sin configurar: no se pueden crear cotizaciones",
        adminOnly: true,
      },
    ],
  },
];

export const TEXT_MAX = 1000;
const RANGES: Partial<Record<PreferenceKind, [number, number]>> = { days: [1, 365], seed: [1, 99999] };
/** `numeric(18,6)`: six decimals on a ratio are four on a percentage. */
export const PERCENT_DECIMALS = 4;
export const PERCENT_MAX = 999;
/** Twelve integer digits: up to $ 999.999.999.999. */
export const CLP_DIGITS = 12;

const PLAIN = /^\d+(?:\.\d+)?$/;

/** A plain decimal string without leading zeros or trailing fraction zeros ("045.500" → "45.5"); null otherwise. */
export function normalDecimal(value: unknown): string | null {
  if (typeof value !== "string" && typeof value !== "number") return null;
  const t = String(value).trim();
  if (!PLAIN.test(t)) return null;
  const [int, frac = ""] = t.split(".");
  const i = int.replace(/^0+(?=\d)/, "");
  const f = frac.replace(/0+$/, "");
  return f ? `${i}.${f}` : i;
}

/** «1234567» → «1.234.567»: grouping on the digits, no float. */
function groupThousands(digits: string): string {
  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
}

/** The current value as the page shows it; null when there is none. */
export function displayPreference(kind: PreferenceKind, value: unknown): string | null {
  if (value == null || value === "") return null;
  if (kind === "text") return typeof value === "string" ? value : null;
  const n = normalDecimal(value);
  if (n === null) return String(value);
  if (kind === "percent") return `${decimalToPercentText(n)} %`;
  if (kind === "clp") return formatMoney(n, "CLP");
  if (kind === "days") return n === "1" ? "1 día" : `${n} días`;
  return n;
}

/** The input's text for the current value («40», «12,5», «1.234.567», «30»); empty when unset. */
export function editTextOf(kind: PreferenceKind, value: unknown): string {
  if (value == null) return "";
  if (kind === "text") return typeof value === "string" ? value : "";
  const n = normalDecimal(value);
  if (n === null) return String(value);
  if (kind === "percent") return decimalToPercentText(n);
  if (kind === "clp") {
    const [int, frac] = n.split(".");
    return frac ? `${groupThousands(int)},${frac}` : groupThousands(int);
  }
  return n;
}

export type Parsed = { ok: true; value: string } | { ok: false; problem: string };

const PERCENT_TOO_FINE = `Usa como máximo ${PERCENT_DECIMALS} decimales en un porcentaje.`;
const PERCENT_TOO_BIG = `Un porcentaje va de 0 a ${PERCENT_MAX} %.`;
const CLP_TOO_BIG = `El monto es demasiado grande: como máximo ${CLP_DIGITS} dígitos.`;

const PROBLEM: Record<PreferenceKind, string> = {
  percent: "Escribe un porcentaje, por ejemplo 40 o 12,5.",
  clp: "Escribe un monto en pesos, sin decimales; por ejemplo 1.234.567.",
  days: "Escribe un número entero de días, de 1 a 365.",
  seed: "Escribe un número entero, de 1 a 99999.",
  text: `Escribe el texto (hasta ${TEXT_MAX} caracteres).`,
};

/** What the operator typed, as the value the API takes (a decimal string, or the text). */
export function parsePreference(kind: PreferenceKind, text: string): Parsed {
  const bad = { ok: false as const, problem: PROBLEM[kind] };
  if (kind === "text") {
    const t = text.trim();
    return t && t.length <= TEXT_MAX ? { ok: true, value: t } : bad;
  }
  if (kind === "percent") {
    const t = text.replace(/\s+/g, "").replace(/%$/, "");
    const m = /^(\d+)(?:[.,](\d+))?$/.exec(t);
    if (!m) return bad;
    const int = m[1].replace(/^0+(?=\d)/, "");
    const frac = (m[2] ?? "").replace(/0+$/, "");
    if (frac.length > PERCENT_DECIMALS) return { ok: false, problem: PERCENT_TOO_FINE };
    // Integer comparison on at most three digits.
    if (int.length > 3 || Number(int) > PERCENT_MAX || (Number(int) === PERCENT_MAX && frac)) return { ok: false, problem: PERCENT_TOO_BIG };
    const ratio = normalDecimal(percentTextToDecimal(t));
    return ratio === null ? bad : { ok: true, value: ratio };
  }
  if (kind === "clp") {
    const amount = normalDecimal(amountTextToDecimal(text));
    if (amount === null || amount.includes(".")) return bad;
    return amount.length > CLP_DIGITS ? { ok: false, problem: CLP_TOO_BIG } : { ok: true, value: amount };
  }
  const m = /^\s*(\d+)\s*(?:d[ií]as?)?\s*$/i.exec(text);
  const range = RANGES[kind];
  if (!m || !range || (kind === "seed" && /d[ií]a/i.test(text))) return bad;
  const digits = m[1].replace(/^0+(?=\d)/, "");
  // Integer comparison on at most six digits: the ranges' bounds are whole numbers.
  if (digits.length > 6) return bad;
  const n = Number(digits);
  return n < range[0] || n > range[1] ? bad : { ok: true, value: digits };
}

/** True when `parsed` is the current `value` (nothing to save). */
export function sameValue(kind: PreferenceKind, parsed: string, value: unknown): boolean {
  if (value == null) return false;
  if (kind === "text") return typeof value === "string" && value.trim() === parsed;
  const a = normalDecimal(parsed);
  return a !== null && a === normalDecimal(value);
}

/** Compares two plain non-negative decimal strings (-1, 0, 1) on their digits; null if either is not one. */
export function compareDecimal(a: unknown, b: unknown): -1 | 0 | 1 | null {
  const x = normalDecimal(a);
  const y = normalDecimal(b);
  if (x === null || y === null) return null;
  const [xi, xf = ""] = x.split(".");
  const [yi, yf = ""] = y.split(".");
  if (xi.length !== yi.length) return xi.length < yi.length ? -1 : 1;
  if (xi !== yi) return xi < yi ? -1 : 1;
  const n = Math.max(xf.length, yf.length);
  const xp = xf.padEnd(n, "0");
  const yp = yf.padEnd(n, "0");
  return xp === yp ? 0 : xp < yp ? -1 : 1;
}

/** The engine's band while a bound is unset (docs/OPERATIONS.md §14.8). */
export const MARGIN_TARGET_FALLBACK = { margin_target_min: "0.25", margin_target_max: "0.45" } as const;

/**
 * Why `value` for one end of the margin band cannot be saved against the other end's current value
 * (or its fallback): the minimum above the maximum, or the maximum below the minimum. Null when
 * fine, or when `key` is not a band key. Equal ends are allowed.
 */
export function bandProblem(key: string, value: string, current: Record<string, { value: unknown } | undefined>): string | null {
  if (key !== "margin_target_min" && key !== "margin_target_max") return null;
  const other = key === "margin_target_min" ? "margin_target_max" : "margin_target_min";
  const raw = current[other]?.value;
  const otherValue = normalDecimal(raw) ?? MARGIN_TARGET_FALLBACK[other];
  const order = compareDecimal(value, otherValue);
  const shown = displayPreference("percent", otherValue);
  if (key === "margin_target_min" && order === 1) return `El mínimo no puede ser mayor que el máximo (${shown}).`;
  if (key === "margin_target_max" && order === -1) return `El máximo no puede ser menor que el mínimo (${shown}).`;
  return null;
}
