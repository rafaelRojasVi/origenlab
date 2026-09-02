/**
 * Error taxonomy for customer-quote commands (CRM-Q2B).
 *
 * Backend command errors carry a stable machine-readable reason-code prefix
 * in their detail string (e.g. "duplicate_document_number: ..."), matching
 * the pre-existing quote_numbering_not_configured/quote_numbering_policy_
 * mismatch convention. This maps a known reason code to a specific,
 * actionable Spanish message; an unrecognized code or a raw non-JSON body
 * falls back to a safe status-based message -- the operator never sees raw
 * exception text, JSON blobs, or provider/database internals.
 */

import { OperatorApiError } from "./operatorClient";

const REASON_CODE_RE = /^([a-z][a-z0-9_]*):\s*/;

function extractReasonCode(error: OperatorApiError): string | null {
  let detail: unknown = error.message;

  try {
    const parsed = JSON.parse(error.message) as { detail?: unknown };
    detail = parsed.detail;
  } catch {
    // Not JSON -- use the raw message as-is for reason-code matching.
  }

  if (typeof detail !== "string") {
    return null;
  }

  const match = REASON_CODE_RE.exec(detail);
  return match ? match[1] : null;
}

const REASON_MESSAGES: Record<string, string> = {
  duplicate_document_number: "Ese número de documento ya está en uso por otra cotización.",
  duplicate_quote_number: "Ese número de cotización ya está en uso por otra cotización.",
  duplicate_identifier: "El número de documento o de cotización ya está en uso.",
  drive_folder_already_incorporated: "Esta carpeta de Drive ya fue incorporada a otra cotización.",
  sales_opportunity_not_found:
    "No encontramos la oportunidad seleccionada. Puede que haya cambiado; recarga e intenta de nuevo.",
  customer_quote_not_found:
    "No encontramos esta cotización. Puede que haya cambiado; recarga e intenta de nuevo.",
  customer_quote_version_conflict: "Esta cotización cambió en otra sesión. Recarga e intenta de nuevo.",
  customer_quote_illegal_transition:
    "Esta acción ya no es válida para el estado actual de la cotización. Recarga e intenta de nuevo.",
  quote_numbering_not_configured: "La numeración de cotizaciones aún no está activada. Contacta al equipo técnico.",
  quote_numbering_policy_mismatch: "Hay un conflicto de configuración de numeración. Contacta al equipo técnico.",
};

function fallbackByStatus(status: number): string {
  if (status === 404) return "No encontramos el recurso solicitado. Recarga e intenta de nuevo.";
  if (status === 409) return "Hubo un conflicto de concurrencia. Recarga e intenta de nuevo.";
  if (status === 422) return "Los datos ingresados no son válidos. Revísalos e intenta de nuevo.";
  if (status === 401 || status === 403) return "Tu sesión no está autorizada para esta acción. Vuelve a iniciar sesión.";
  if (status === 503) return "El servicio de escritura no está disponible temporalmente. Intenta de nuevo en unos minutos.";
  if (status >= 500) return "Ocurrió un error inesperado en el servidor. Intenta de nuevo.";
  return "No pudimos completar la acción. Intenta de nuevo.";
}

/**
 * Human-readable, safe (no leaked internals) description of a customer-
 * quote command failure. `context` is currently informational only (kept
 * for future context-specific copy) -- the mapping is driven entirely by
 * the backend's reason code / HTTP status.
 */
export function describeCustomerQuoteCommandError(
  error: unknown,
  _context: "adopt" | "close" | "workflow",
): string {
  if (error instanceof OperatorApiError) {
    const reasonCode = extractReasonCode(error);

    if (reasonCode && REASON_MESSAGES[reasonCode]) {
      return REASON_MESSAGES[reasonCode];
    }

    return fallbackByStatus(error.status);
  }

  // A browser fetch() failure (offline, DNS, CORS) always throws a
  // TypeError -- never a crafted message, so this is the one case worth
  // overriding with a friendlier connectivity message.
  if (error instanceof TypeError) {
    return "No pudimos conectar con el servidor. Revisa tu conexión e intenta de nuevo.";
  }

  // Any other Error was thrown intentionally by this codebase with an
  // already-safe, already-specific message (e.g. "No pudimos confirmar la
  // oportunidad creada. Reintenta.") -- pass it through rather than
  // discarding it behind a generic fallback.
  if (error instanceof Error && error.message) {
    return error.message;
  }

  return "No pudimos completar la acción. Intenta de nuevo.";
}
