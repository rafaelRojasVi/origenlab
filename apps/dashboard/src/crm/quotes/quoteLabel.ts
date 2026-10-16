/**
 * The name a quote goes by on screen, matching the API's `cn_label`: «CN» + the five-digit serial
 * + the revision letter (revision 1 has none, revision 2 is «A», 3 is «B»…). It is read from the
 * printed label («00042A-26») when the API sent one, else from the quote number and revision.
 * A historical number outside the series' form is shown as it is.
 */
const SERIAL = /^(\d{1,6})([A-Z]?)-\d{2}$/;

export function quoteLabel({
  label,
  quote_number,
  revision_no,
}: {
  label: string | null | undefined;
  quote_number: string;
  revision_no?: number | null;
}): string {
  const printed = label ? SERIAL.exec(label) : null;
  if (printed) return `CN${printed[1].padStart(5, "0")}${printed[2]}`;
  const base = SERIAL.exec(quote_number);
  if (!base) return quote_number;
  const rev = revision_no ?? 1;
  const letter = rev >= 2 && rev <= 27 ? String.fromCharCode(65 + rev - 2) : "";
  return `CN${base[1].padStart(5, "0")}${letter}`;
}
