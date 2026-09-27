import type { AuthSessionState } from "../api/authClient";

/**
 * Which roles read contact addresses as recorded. The API enforces this (`contact_redaction.py`
 * masks every address in a `/v2` answer for any other role and marks the answer with the
 * `X-OrigenLab-Redaction` header); the dashboard only explains what the operator is looking at.
 */
export const ROLES_THAT_SEE_CONTACT_ADDRESSES: ReadonlySet<string> = new Set(["sales", "admin"]);

export const REDACTION_HEADER = "X-OrigenLab-Redaction";
export const REDACTED_CONTACT_ADDRESSES = "contact-addresses";

/** True when the signed-in operator's role gets masked contact addresses from the API. */
export function contactAddressesRedacted(session: AuthSessionState): boolean {
  return session.kind === "signed_in" && !ROLES_THAT_SEE_CONTACT_ADDRESSES.has(session.operator.role);
}

/** Whether a value is the API's masked form of an address (`***@dominio` or `***`). */
export function isMaskedAddress(value: string | null | undefined): boolean {
  if (!value) return false;
  return value === "***" || /(^|[\s<])\*\*\*@/.test(value);
}

export const REDACTION_NOTICE = "Direcciones de contacto ocultas para tu rol";
