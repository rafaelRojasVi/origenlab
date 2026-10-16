import { describe, expect, it } from "vitest";
import { OperatorApiError } from "../api/operatorClient";
import { refusalMessage } from "./mailRules";

const err = (status: number, body: unknown) => new OperatorApiError(JSON.stringify(body), status);

describe("refusalMessage", () => {
  it("says the command's own message from the API's envelope", () => {
    const e = err(409, {
      error: { code: "conflict", message: "conflict", details: { code: "already_undone", message: "Ya se deshizo." }, request_id: "r1" },
    });
    expect(refusalMessage(e, "No se pudo.")).toBe("Ya se deshizo.");
  });

  it("still reads an older detail text", () => {
    expect(refusalMessage(err(400, { detail: "Texto antiguo." }), "No se pudo.")).toBe("Texto antiguo.");
  });

  it("falls back to the fixed sentence for a 403 with no message", () => {
    expect(refusalMessage(err(403, {}), "No se pudo.")).toBe("Sólo un perfil de administración puede aplicar o deshacer.");
  });
});
