/** Split `Name <addr>` into its parts. The display name is what the sender's mail client wrote,
 *  never a person recorded in the CRM. */
export function splitAddress(raw: string): { display: string | null; email: string } {
  const m = raw.match(/^\s*(.*?)\s*<([^>]+)>\s*$/);
  if (m) return { display: m[1].replace(/^"|"$/g, "").trim() || null, email: m[2].trim() };
  return { display: null, email: raw.trim() };
}
