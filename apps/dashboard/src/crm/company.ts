/**
 * OrigenLab's own contact data on the dashboard side, in one place. The WhatsApp number is public
 * (it is printed on every quotation) but the repository is public too, so it is a build-time
 * setting, not source: `VITE_ORIGENLAB_WHATSAPP`, digits with country code (e.g. `56912345678`).
 * Unset or invalid: the quotation prints no WhatsApp line and no QR.
 */

/** Digits only, 8 to 15 of them (E.164 without «+»); `+`, a leading `00`, spaces, dots and dashes are tolerated. */
export function parseWhatsappNumber(raw: string | undefined | null): string | null {
  // «00» is the international prefix written out («0056 9 …»): not part of the number.
  const digits = (raw ?? "").trim().replace(/[\s+().-]/g, "").replace(/^00/, "");
  return /^\d{8,15}$/.test(digits) ? digits : null;
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
