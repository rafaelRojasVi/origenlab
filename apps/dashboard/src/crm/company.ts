/**
 * OrigenLab's own contact data on the dashboard side, in one place. The WhatsApp number is public
 * (it is printed on every quotation) but the repository is public too, so it is a build-time
 * setting, not source: `VITE_ORIGENLAB_WHATSAPP`, a Chilean number with country code (e.g. `56912345678`).
 * Unset or invalid: the quotation prints no WhatsApp line and no QR.
 */

/**
 * A Chilean number as digits with the country code: «56» then 8 or 9 digits (E.164 without «+»).
 * `+`, a leading `00`, spaces, dots, dashes and parentheses are tolerated. A national number
 * («912345678») or one with a trunk zero («0912…») is refused: wa.me needs the country code.
 */
export function parseWhatsappNumber(raw: string | undefined | null): string | null {
  // «00» is the international prefix written out («0056 9 …»): not part of the number.
  const digits = (raw ?? "").trim().replace(/[\s+().-]/g, "").replace(/^00/, "");
  return /^56\d{8,9}$/.test(digits) ? digits : null;
}

/**
 * A Chilean mobile as WhatsApp wants it, `569XXXXXXXX`, or null for anything else — a landline
 * (+56 2 …, +56 41 …) has no WhatsApp (QA 2026-11-19). Accepts «+56 9 …», «0056 9 …», «56 9 …»,
 * a bare «9 XXXX XXXX» and the old trunk «09 XXXX XXXX».
 */
export function chileanMobile(phone: string): string | null {
  let digits = phone.replace(/\D/g, "");
  if (digits.startsWith("00")) digits = digits.slice(2);
  if (digits.length === 10 && digits.startsWith("09")) digits = digits.slice(1);
  if (digits.length === 9 && digits.startsWith("9")) digits = `56${digits}`;
  return /^569\d{8}$/.test(digits) ? digits : null;
}

export const WHATSAPP_NUMBER: string | null = parseWhatsappNumber(import.meta.env.VITE_ORIGENLAB_WHATSAPP);

/** `wa.me` link that opens a chat with the message already typed: «Hola OrigenLab, cotización CN…» (short on purpose: it has to fit the QR with room to spare). */
export function whatsappQuoteUrl(number: string, quoteNumber: string): string {
  const text = `Hola OrigenLab, cotización ${quoteNumber}.`;
  return `https://wa.me/${number}?text=${encodeURIComponent(text)}`;
}

/** «+56 9 1234 5678» for a Chilean mobile; any other number as «+» and its digits. */
export function formatWhatsappNumber(number: string): string {
  const m = number.match(/^56(9)(\d{4})(\d{4})$/);
  return m ? `+56 ${m[1]} ${m[2]} ${m[3]}` : `+${number}`;
}
