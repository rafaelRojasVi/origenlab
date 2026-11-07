import { describe, expect, it } from "vitest";
import { CASE_ORIGINS, NO_ORIGIN_LABEL, ORIGIN_LABEL, matchesOrigin, originLabel } from "./caseOrigin";

describe("case origin", () => {
  it("names the nine channels, in the order the API lists them (a test in apps/api checks the two files agree)", () => {
    expect(CASE_ORIGINS).toHaveLength(9);
    expect(Object.keys(ORIGIN_LABEL)).toEqual([...CASE_ORIGINS]);
  });

  it("labels every channel in Spanish and never shows the raw key", () => {
    expect(ORIGIN_LABEL).toEqual({
      correo: "Correo",
      web: "Web",
      whatsapp: "WhatsApp",
      campana: "Campaña",
      mercado_publico: "Mercado Público",
      fabricante: "Fabricante",
      recomendacion: "Recomendación",
      llamada: "Llamada",
      ex_labdelivery: "Ex Labdelivery",
    });
    for (const key of CASE_ORIGINS) expect(originLabel(key)).not.toBe(key);
  });

  it("reads a missing origin as «Sin origen» and a value this build does not know as «Otro origen»", () => {
    expect(NO_ORIGIN_LABEL).toBe("Sin origen");
    expect(originLabel(null)).toBe("Sin origen");
    expect(originLabel(undefined)).toBe("Sin origen");
    expect(originLabel("")).toBe("Sin origen");
    expect(originLabel("telepatia")).toBe("Otro origen");
  });

  it("filters cards by origin: all, one channel, or none", () => {
    const web = { origin: "web" };
    const none = { origin: null };
    const older = {}; // an API that predates the field
    expect([web, none, older].filter((c) => matchesOrigin(c, "all"))).toHaveLength(3);
    expect([web, none, older].filter((c) => matchesOrigin(c, "web"))).toEqual([web]);
    expect([web, none, older].filter((c) => matchesOrigin(c, "correo"))).toEqual([]);
    expect([web, none, older].filter((c) => matchesOrigin(c, "none"))).toEqual([none, older]);
  });
});
