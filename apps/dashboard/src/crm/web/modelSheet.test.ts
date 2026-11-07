import { describe, expect, it } from "vitest";
import schemaJson from "../../../../web/src/content/modelos.schema.json";
import { BRANDS, FAMILIES, LIMITS, REQUIRED_FIELDS, emptySheet, normalizeErrors, toPayload, translateMessage } from "./modelSheet";

const schema = schemaJson as unknown as {
  required: string[];
  properties: Record<string, { enum?: string[]; maxLength?: number; pattern?: string }>;
};

describe("the form follows apps/web's modelos.schema.json", () => {
  it("offers exactly the schema's families and brands", () => {
    expect(FAMILIES.map((f) => f.value)).toEqual(schema.properties.family.enum);
    expect(BRANDS.map((b) => b.value)).toEqual(schema.properties.brand.enum);
  });

  it("requires exactly the schema's required fields", () => {
    expect([...REQUIRED_FIELDS].sort()).toEqual([...schema.required].sort());
  });

  it("caps text at the schema's maximum lengths", () => {
    expect(LIMITS.name).toBe(schema.properties.name.maxLength);
    expect(LIMITS.headline).toBe(schema.properties.headline.maxLength);
    expect(LIMITS.lead).toBe(schema.properties.lead.maxLength);
  });

  it("starts a new sheet with every required field present", () => {
    const sheet = emptySheet("modelo-x", "2026-11-07");
    for (const key of schema.required) expect(sheet, key).toHaveProperty(key);
    expect(sheet.slug).toBe("modelo-x");
  });
});

describe("toPayload", () => {
  it("omits empty optional fields and blank rows, and trims", () => {
    const sheet = { ...emptySheet("modelo-x", "2026-11-07"), name: " Equipo ", applications: ["  ", "Uso"], specs: [{ label: "", value: "", source: "" }], official_pdf_url: " " };
    const payload = toPayload(sheet);
    expect(payload.name).toBe("Equipo");
    expect(payload.applications).toEqual(["Uso"]);
    expect(payload.specs).toEqual([]);
    expect(payload).not.toHaveProperty("official_pdf_url");
  });
});

describe("normalizeErrors", () => {
  it("reads slash, dotted and bracketed paths, and a missing property named in the message", () => {
    expect(normalizeErrors([{ path: "specs/0/source", message: "x" }])[0].path).toBe("specs/0/source");
    expect(normalizeErrors([{ path: "$.specs[0].source", message: "x" }])[0].path).toBe("specs/0/source");
    expect(normalizeErrors([{ path: "", message: "'headline' is a required property" }])[0].path).toBe("headline");
  });
});

describe("translateMessage", () => {
  it("puts the validator's messages in Spanish", () => {
    expect(translateMessage("'abc' is too long")).toMatch(/Demasiado largo/);
    expect(translateMessage("'x' does not match '^https?://'")).toMatch(/http/);
    expect(translateMessage("Additional properties are not allowed ('price' was unexpected)")).toMatch(/no permitido/);
    expect(translateMessage("'sonic' is not one of ['a', 'b']")).toMatch(/Valor no permitido/);
    expect(translateMessage("something unforeseen")).toMatch(/esquema/);
  });
});
