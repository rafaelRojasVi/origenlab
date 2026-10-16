/**
 * Money as the CRM prints it. Amounts come from the API as decimal strings and are only formatted
 * here, never calculated with.
 */

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
