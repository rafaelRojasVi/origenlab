/**
 * Command refusals: one parser for every envelope the API and the proxy answer with, and one
 * table of the Spanish sentences the operator reads.
 *
 * The API a deployment runs registers `apps/api/src/origenlab_api/errors.py`, which wraps a
 * refusal as
 *
 *     {"error": {"code": "conflict", "message": "…", "details": {"code": "stale_version", …}}}
 *
 * — the generic code on top, the specific one in `details`. A bare FastAPI app (the API's own
 * unit tests, older mocks) answers `{"detail": {"code", "message"}}`, and the Worker answers
 * `{"error": {"code": "path_not_allowed"}}`. `parseRefusal` reads all of them and keeps the
 * most specific code. Nothing here ever puts a raw response body on screen.
 */
import { OperatorApiError } from "../api/operatorClient";

export interface Refusal {
  /** HTTP status; 0 when the request never got an answer (network failure). */
  status: number;
  /** The most specific machine code: `details.code` when the envelope has one. */
  code: string;
  /** The API's own sentence — English for most commands, so the UI shows `refusalText` instead. */
  message: string;
  /** The object that carried the specific code (e.g. `next_allowed_at`, `constraint`). */
  details: Record<string, unknown>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

/** Any refusal body — parsed JSON or the raw text of the response — into one shape. */
export function parseRefusal(status: number, body: unknown): Refusal {
  const generic: Refusal = { status, code: `http_${status}`, message: "", details: {} };
  let parsed = body;
  if (typeof body === "string") {
    const raw = body.trim();
    if (!raw) return generic;
    try {
      parsed = JSON.parse(raw);
    } catch {
      // Not JSON (an HTML error page, a plain-text proxy answer): only a known code is kept.
      return raw.includes("path_not_allowed") ? { ...generic, code: "path_not_allowed" } : generic;
    }
  }
  if (!isRecord(parsed)) return generic;

  const error = parsed.error;
  if (isRecord(error)) {
    const details = isRecord(error.details) ? error.details : {};
    return {
      status,
      code: text(details.code) ?? text(error.code) ?? generic.code,
      message: text(error.message) ?? text(details.message) ?? "",
      details,
    };
  }

  const detail = parsed.detail;
  if (isRecord(detail)) {
    return { status, code: text(detail.code) ?? generic.code, message: text(detail.message) ?? "", details: detail };
  }
  if (typeof detail === "string") return { ...generic, message: detail };
  if (Array.isArray(detail)) return { ...generic, code: "validation_error", details: { validation_errors: detail } };
  return generic;
}

/** A failed command call as a refusal; a request that never got an answer is `network_error`. */
export function refusalFromError(err: unknown): Refusal {
  if (err instanceof OperatorApiError) return parseRefusal(err.status, err.message);
  return { status: 0, code: "network_error", message: err instanceof Error ? err.message : String(err), details: {} };
}

const STALE = "Otro operador modificó este registro mientras lo editabas. Carga la versión actual; tus cambios siguen en el formulario.";
const NOT_ENABLED = "Esta acción no está habilitada en este entorno.";

/** Every refusal code the operator can meet, in Spanish. Unlisted codes get `fallback`. */
export const REFUSAL_MESSAGES: Readonly<Record<string, string>> = {
  // Concurrency and idempotency: the same sentence wherever they happen.
  stale_version: STALE,
  case_version_conflict: STALE,
  record_busy: "Otro operador está guardando este registro. Reintenta en unos segundos.",
  service_busy: "El sistema está ocupado. Reintenta en unos segundos.",
  duplicate: "Ya existe un registro con esos datos.",
  idempotency_key_reused:
    "Este envío ya se había registrado con otros datos. Recarga para ver lo que quedó guardado antes de intentarlo otra vez.",
  idempotency_conflict: "Este envío todavía se está procesando. Espera unos segundos y recarga.",
  command_in_progress: "Este envío todavía se está procesando. Espera unos segundos y recarga.",
  command_already_failed: "Un intento anterior de este envío falló. Vuelve a intentarlo.",
  // The request never reached a decision.
  network_error: "No se pudo contactar al servidor. Revisa la conexión y reintenta; un mismo envío no se registra dos veces.",
  validation_error: "Revisa los datos: hay un campo inválido o incompleto.",
  http_400: "Revisa los datos: hay un campo inválido o incompleto.",
  http_422: "Revisa los datos: hay un campo inválido o incompleto.",
  unauthorized: "Tu sesión terminó. Vuelve a iniciar sesión.",
  http_401: "Tu sesión terminó. Vuelve a iniciar sesión.",
  forbidden: "Tu perfil no puede hacer esta acción.",
  http_403: "Tu perfil no puede hacer esta acción.",
  role_may_not_decide: "Tu perfil no puede hacer esta acción.",
  role_may_not_archive: "Sólo un perfil de administración puede archivar o fusionar.",
  path_not_allowed: NOT_ENABLED,
  not_found: NOT_ENABLED,
  http_404: NOT_ENABLED,
  method_not_allowed: NOT_ENABLED,
  http_405: NOT_ENABLED,
  internal_error: "El servidor no pudo completar la acción. Reintenta en unos segundos.",
  backend_unavailable: "El sistema no está disponible en este momento. Reintenta en unos segundos.",
  // What a CRM record refuses.
  archived_subject: "El registro está archivado.",
  merged_person: "Esa persona fue fusionada con otra; ábrela desde la persona que quedó.",
  organization_merged: "Esa institución fue fusionada con otra; ábrela desde la que quedó.",
  person_not_found: "Esa persona ya no existe en el CRM.",
  organization_not_found: "Esa institución ya no existe en el CRM.",
  contact_point_taken: "Esa dirección ya es de otra persona del CRM.",
  shared_mailbox: "Esa dirección es un buzón compartido, no una persona.",
  contact_point_inactive: "Esa dirección fue desactivada en el CRM.",
  already_exists: "Ese dato ya está registrado y activo.",
  identifier_taken: "Ese identificador ya está registrado en otra institución.",
  identifier_already_present: "Ese identificador ya está en esta institución.",
  domain_already_present: "Ese dominio ya está en esta institución.",
  exclusive_domain_taken: "Otra institución tiene ese dominio como exclusivo.",
  invalid_domain: "El dominio no es válido.",
  not_a_phone_number: "El teléfono no es válido: escríbelo con código de país (+56…).",
  preview_changed: "La vista previa cambió antes de confirmar; recarga y revisa.",
};

/** A version moved under the operator: the record must be read again before saving. */
export function isStaleRefusal(r: Refusal | null | undefined): boolean {
  return r?.code === "stale_version" || r?.code === "case_version_conflict";
}

/** The command is not mounted here (switch off, or the Worker does not forward it). */
export function isNotEnabledRefusal(r: Refusal | null | undefined): boolean {
  return !!r && ["path_not_allowed", "not_found", "http_404", "method_not_allowed", "http_405"].includes(r.code);
}

/**
 * The sentence the operator reads for a failed command. `fallback` leads the message for a code
 * the table does not know, which keeps the code for support; `overrides` lets one screen say a
 * known code in its own words. Never a raw body, never the API's English.
 */
export function refusalText(
  refusalOrError: Refusal | unknown,
  options: { fallback?: string; overrides?: Readonly<Record<string, string>> } = {},
): string {
  const r = isRefusal(refusalOrError) ? refusalOrError : refusalFromError(refusalOrError);
  const known = options.overrides?.[r.code] ?? REFUSAL_MESSAGES[r.code];
  if (known) return known;
  if (/^http_5\d\d$/.test(r.code)) return REFUSAL_MESSAGES.internal_error;
  return `${options.fallback ?? "No se pudo completar la acción"} (código ${r.code}).`;
}

function isRefusal(value: unknown): value is Refusal {
  return isRecord(value) && typeof value.code === "string" && typeof value.status === "number" && isRecord(value.details);
}
