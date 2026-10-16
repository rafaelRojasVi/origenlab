/** The refusal envelope of `set-cost-parameter`: the command's own code wins over the HTTP layer's. */
import { describe, expect, it } from "vitest";
import { QuoteRefusal } from "./quoteApi";
import { preferenceRefusalText, refusalEnvelope } from "./preferencesApi";

describe("refusalEnvelope", () => {
  it("reads the command's code from error.details, falling back to error.code and FastAPI's detail", () => {
    expect(
      refusalEnvelope({ error: { code: "conflict", message: "outer", details: { code: "parameter_changed", message: "inner" }, request_id: "r" } }),
    ).toMatchObject({ code: "parameter_changed", message: "inner" });
    expect(refusalEnvelope({ error: { code: "forbidden", message: "outer", details: {} } })).toMatchObject({ code: "forbidden", message: "outer" });
    expect(refusalEnvelope({ detail: { code: "role_may_not_set_numbering", message: "m" } })).toMatchObject({ code: "role_may_not_set_numbering" });
    expect(refusalEnvelope("<html>")).toEqual({ code: null, message: null, details: null });
  });
});

describe("preferenceRefusalText", () => {
  it("says the preferences' own codes in Spanish and leaves the rest to the quote texts", () => {
    expect(preferenceRefusalText(new QuoteRefusal(409, "parameter_changed", "x"))).toBe("Otra persona lo cambió; recarga.");
    expect(preferenceRefusalText(new QuoteRefusal(403, "role_may_not_set_numbering", "x"))).toBe("Sólo administración fija el inicio de la numeración.");
    expect(preferenceRefusalText(new QuoteRefusal(409, "command_in_progress", "x"))).toMatch(/todavía se está procesando/);
    expect(preferenceRefusalText(new QuoteRefusal(502, null, "Bad gateway"))).toBe("No se pudo guardar. Intenta de nuevo.");
  });
});
