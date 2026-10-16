import { describe, expect, it } from "vitest";
import { QuoteRefusal } from "./quoteApi";
import { looksSpanish, quoteRefusalLines, quoteRefusalText } from "./quoteRefusal";

/** Each code with the API's own (English, or terse) text: the Spanish said for it. */
const ENGLISH: [string, string, string][] = [
  ["labdelivery_refused", "input of Labdelivery origin is refused", "Labdelivery"],
  ["case_closed", "this case is closed; a closed case gets no new quote", "Este caso está cerrado; no recibe cotizaciones nuevas."],
  ["not_a_draft", "only a draft revision is edited", "ya no es un borrador"],
  ["recipient_unknown", "no such person to send the quote to", "ya no está en el CRM"],
  ["recipient_not_on_case", "the recipient is not a current participant of this case", "ya no participa en el caso"],
  ["contact_point_inactive", "this email address is deactivated in the CRM", "desactivada"],
  ["archived_subject", "that person is archived", "archivada"],
  ["recipient_changed_concurrently", "the case's people changed while saving; save again", "mientras se guardaba"],
  ["recipient_own_address", "own address", "Esa dirección es de OrigenLab, no del cliente."],
  ["superseded", "superseded", "Ya fue reemplazada por una revisión posterior."],
  ["already_sent", "sent", "Ya se envió: crea una nueva revisión."],
  ["draft_open", "this quote already has an open draft; edit or void it first", "Ya hay un borrador abierto"],
  ["nothing_to_revise", "this quote has no approved or sent revision to revise", "No hay una revisión aprobada"],
  ["product_not_found", "no such product", "ya no está en el catálogo"],
  ["cost_observation_not_found", "line 2 names a cost observation that does not exist", "ya no existe"],
  ["reference_not_found", "a line names a supplier or cost observation that does not exist", "proveedor o un costo"],
  ["amount_too_large", "too large", "Un monto es demasiado grande."],
  ["fx_unavailable", "no rate", "No hay tipo de cambio para hoy"],
  ["quote_numbering_not_configured", "not configured", "Falta configurar la numeración de cotizaciones."],
  ["quote_numbering_exhausted", "exhausted", "se agotó"],
  ["quote_number_taken", "the number 00042-26 was just taken; try again", "se acaba de usar"],
  ["validation_error", "Request validation failed", "no aceptó algunos datos"],
  ["fx_missing:EUR", "the draft cannot be priced (fx_missing:EUR)", "Falta el tipo de cambio de EUR."],
  ["quantity_invalid:3", "the draft cannot be priced (quantity_invalid:3)", "La cantidad de la línea 3 no es válida."],
  ["margin_pct_invalid:2", "the draft cannot be priced (margin_pct_invalid:2)", "El recargo o margen de la línea 2 no es válido."],
  ["dhl_parameter_missing:dhl_fuel_pct", "cannot be priced", "«dhl_fuel_pct»"],
  ["stale_version", "the draft was modified since you loaded it", "Otra persona cambió esta cotización"],
  ["role_may_not_view_costs", "sales or admin only", "Sólo ventas y administración pueden cotizar."],
  ["revision_not_found", "no such quote revision", "Esta revisión ya no existe."],
  ["bad_cursor", "that cursor is not one this list gave", "La lista cambió; se volvió a cargar desde el inicio."],
  ["bad_status", "no such quote status", "Ese estado de cotizaciones no existe."],
  ["command_in_progress", "a command with this Idempotency-Key is still running", "todavía se está procesando"],
];

describe("quoteRefusalText", () => {
  it.each(ENGLISH)("says %s in Spanish, never the API's English", (code, message, spanish) => {
    const text = quoteRefusalText(new QuoteRefusal(409, code, message));
    expect(text).toContain(spanish);
    expect(text).not.toContain(message);
    expect(looksSpanish(text)).toBe(true);
  });

  it("keeps the API's Spanish when it says more than the code", () => {
    expect(quoteRefusalText(new QuoteRefusal(409, "superseded", "Ya fue reemplazada por la revisión 2."))).toBe("Ya fue reemplazada por la revisión 2.");
    expect(quoteRefusalText(new QuoteRefusal(409, "fx_unavailable", "No hay tipo de cambio USD para hoy; ingrésalo con «Cambiar»."))).toContain("USD");
  });

  it("keeps approval's joined reasons, and splits them as lines", () => {
    const e = new QuoteRefusal(409, "no_recipient", "Elige a quién va dirigida. · Completa «Forma de pago»: aún tiene un texto entre corchetes.");
    expect(quoteRefusalLines(e, "x")).toEqual(["Elige a quién va dirigida.", "Completa «Forma de pago»: aún tiene un texto entre corchetes."]);
  });

  it("says an unknown code's Spanish text, and the fallback for an English one", () => {
    expect(quoteRefusalText(new QuoteRefusal(422, "algo_nuevo", "Falta configurar algo."), "F")).toBe("Falta configurar algo.");
    expect(quoteRefusalText(new QuoteRefusal(422, "something_new", "something broke"), "F")).toBe("F");
  });

  it("says the fallback for a body that is not the API's envelope", () => {
    expect(quoteRefusalText(new QuoteRefusal(502, null, "<html>Bad gateway</html>"), "F")).toBe("F");
    expect(quoteRefusalText(new Error("boom"), "F")).toBe("F");
    expect(quoteRefusalText(null, "F")).toBe("F");
  });

  it("reads the workspace's computed_error shape too", () => {
    expect(quoteRefusalText({ code: "fx_missing:USD", message: "the draft cannot be priced (fx_missing:USD)" })).toBe("Falta el tipo de cambio de USD.");
  });
});
