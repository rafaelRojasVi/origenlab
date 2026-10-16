/**
 * What the Cotizador says when the quote API refuses something, in Spanish: one place for every
 * screen (the save line, «Para», «Avisos», approve / void / revise, «Cotizar»).
 *
 * The machine `code` decides. The API's own text is said only when it is already Spanish (some
 * refusals carry the specifics the operator needs: the currency without a rate, the revision that
 * replaced this one, the list of reasons approval gave, joined with « · »); an English text is
 * never shown. A body that is not the API's envelope (a proxy page, a network failure) has no code
 * and gets the caller's `fallback`.
 */
import { QuoteRefusal } from "./quoteApi";

/** The API's machine codes, in Spanish. */
const SPANISH: Record<string, string> = {
  // Who and what the quote is for.
  labdelivery_refused: "Hay un texto o una dirección de Labdelivery: no se usa en las cotizaciones de OrigenLab.",
  case_closed: "Este caso está cerrado; no recibe cotizaciones nuevas.",
  case_not_found: "Este caso ya no existe.",
  quote_not_found: "Esta cotización ya no existe.",
  revision_not_found: "Esta revisión ya no existe.",
  not_a_draft: "Esta revisión ya no es un borrador: se aprobó o se anuló.",
  not_authored: "Una cotización histórica no se cambia desde aquí.",
  stale_version: "Otra persona cambió esta cotización. Recarga la página para ver la versión guardada.",
  bad_cursor: "La lista cambió; se volvió a cargar desde el inicio.",
  bad_status: "Ese estado de cotizaciones no existe.",
  role_may_not_view_costs: "Sólo ventas y administración pueden cotizar.",
  // «Nueva cotización»: the client and its case.
  case_choice_needed: "Este cliente tiene varios casos abiertos: elige uno, o abre una Solicitud nueva.",
  case_not_of_client: "Ese caso no es de este cliente; elige otro.",
  organization_name_ambiguous: "Hay varias instituciones con ese nombre: elige una de ellas.",
  organization_not_found: "Esa institución ya no está en el CRM; búscala de nuevo.",
  person_not_found: "Esa persona ya no está en el CRM; búscala de nuevo.",
  contact_point_not_found: "Esa dirección ya no está en el CRM; búscala de nuevo.",
  supplier_exception_required:
    "OrigenLab le compra a esta institución (es proveedora o fabricante): su cotización se crea desde su caso, con la excepción justificada.",
  client_changed_concurrently: "Los datos de este cliente cambiaron mientras se creaba la cotización; vuelve a intentarlo.",
  query_too_short: "Escribe al menos dos letras para la búsqueda.",
  // «Para».
  recipient_unknown: "Esa persona o dirección ya no está en el CRM; elige otra.",
  recipient_not_on_case: "Esa persona ya no participa en el caso; elige otra.",
  contact_point_inactive: "Esa dirección está desactivada en el CRM; elige otra.",
  archived_subject: "Esa persona o institución está archivada; elige otra.",
  recipient_changed_concurrently: "Cambió quién participa en el caso mientras se guardaba; vuelve a intentarlo.",
  recipient_own_address: "Esa dirección es de OrigenLab, no del cliente.",
  // Revisions.
  superseded: "Ya fue reemplazada por una revisión posterior.",
  already_sent: "Ya se envió: crea una nueva revisión.",
  already_void: "Esta revisión ya está anulada.",
  draft_open: "Ya hay un borrador abierto: edítalo o anúlalo primero.",
  nothing_to_revise: "No hay una revisión aprobada o enviada desde la cual empezar otra.",
  revision_letters_exhausted: "Esta cotización no tiene más letras de revisión; crea una cotización nueva.",
  draft_needs_saving: "Guarda el borrador de nuevo: sus precios cambiaron.",
  // Lines and prices.
  product_not_found: "Un producto de la cotización ya no está en el catálogo; quítalo o cámbialo.",
  cost_observation_not_found: "Un costo del catálogo que usa la cotización ya no existe; escribe el costo a mano.",
  reference_not_found: "Una línea nombra un proveedor o un costo que ya no existe; revisa los costos.",
  amount_too_large: "Un monto es demasiado grande.",
  fx_unavailable: "No hay tipo de cambio para hoy; ingrésalo con «Cambiar».",
  dhl_weight_missing: "Falta el peso de algún equipo para calcular el flete DHL.",
  dhl_origin_missing: "Falta el origen de algún equipo para calcular el flete DHL.",
  dhl_origin_mixed: "Los equipos vienen de orígenes distintos: DHL no calcula un solo envío. Usa otro método de gastos.",
  dhl_zone_unknown: "El origen de los equipos no tiene zona DHL configurada.",
  // Numbering.
  quote_numbering_not_configured: "Falta configurar la numeración de cotizaciones.",
  quote_numbering_exhausted: "La numeración de cotizaciones se agotó; configura un nuevo inicio.",
  quote_number_taken: "Ese número de cotización se acaba de usar; vuelve a intentarlo.",
  // The request itself.
  validation_error: "El servidor no aceptó algunos datos de la cotización; revisa los campos y vuelve a intentarlo.",
  command_in_progress: "Esta acción todavía se está procesando; espera un momento y vuelve a intentarlo.",
  idempotency_conflict: "Esta acción todavía se está procesando; espera un momento y vuelve a intentarlo.",
  idempotency_key_reused: "Esta acción ya se usó para otra cosa; vuelve a intentarlo.",
  command_already_failed: "El intento anterior falló; vuelve a intentarlo.",
};

/** The engine's codes that name a line or a currency (`fx_missing:EUR`, `quantity_invalid:3`). */
const WITH_ARGUMENT: Record<string, (arg: string) => string> = {
  fx_missing: (c) => `Falta el tipo de cambio de ${c}.`,
  quantity_invalid: (n) => `La cantidad de la línea ${n} no es válida.`,
  margin_pct_invalid: (n) => `El recargo o margen de la línea ${n} no es válido.`,
  dhl_parameter_missing: (k) => `Falta el parámetro de DHL «${k}» en los parámetros de costos.`,
  expense_method_unknown: () => "El método de gastos no se reconoce; elige otro.",
};

/** Approval's own reasons: the API writes them in Spanish, naming the line or the field. */
const APPROVAL = new Set(["no_items", "unpriced_line", "principal_missing", "fx_missing", "no_institution", "no_recipient", "placeholder_left"]);

/** Refusals whose Spanish server text says more than the code (a currency, a revision number). */
const SPECIFIC = new Set([
  "fx_unavailable",
  "superseded",
  "recipient_own_address",
  "quote_numbering_not_configured",
  "quote_numbering_exhausted",
  "already_sent",
  "draft_needs_saving",
  "amount_too_large",
]);

const SPANISH_MARKS = /[áéíóúñ¿¡«»]/i;
const SPANISH_START = /^(falta|elige|agrega|completa|guarda|ya |esa |ese |este |esta |hay |no hay|un monto|la línea|el caso)/i;

/** A heuristic: diacritics, Spanish quotes or a phrase the API starts its Spanish texts with. */
export function looksSpanish(text: string): boolean {
  const t = text.trim();
  return !!t && (SPANISH_MARKS.test(t) || SPANISH_START.test(t));
}

type Envelope = { code: string; message: string };

function envelopeOf(e: unknown): Envelope | null {
  if (e instanceof QuoteRefusal) return e.code ? { code: e.code, message: e.message ?? "" } : null;
  if (e && typeof e === "object" && "code" in e) {
    const { code, message } = e as { code: unknown; message?: unknown };
    if (typeof code === "string" && code) return { code, message: typeof message === "string" ? message : "" };
  }
  return null;
}

/**
 * The refusal in Spanish (several approval reasons stay joined with « · »; `quoteRefusalLines`
 * splits them). Accepts a `QuoteRefusal` or a `{code, message}` (the workspace's `computed_error`).
 */
export function quoteRefusalText(e: unknown, fallback = "No se pudo completar. Intenta de nuevo."): string {
  const env = envelopeOf(e);
  if (!env) return fallback;
  const { code, message } = env;
  const spanish = looksSpanish(message);
  if ((APPROVAL.has(code) || SPECIFIC.has(code) || message.includes(" · ")) && spanish) return message.trim();
  if (SPANISH[code]) return SPANISH[code];
  const colon = code.indexOf(":");
  if (colon > 0 && WITH_ARGUMENT[code.slice(0, colon)]) return WITH_ARGUMENT[code.slice(0, colon)](code.slice(colon + 1));
  if (spanish) return message.trim();
  return fallback;
}

/** The refusal as a list: approval joins several reasons with « · ». */
export function quoteRefusalLines(e: unknown, fallback: string): string[] {
  const parts = quoteRefusalText(e, fallback)
    .split(" · ")
    .map((t) => t.trim())
    .filter(Boolean);
  return parts.length ? parts : [fallback];
}
