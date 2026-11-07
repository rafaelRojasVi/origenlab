import { describe, expect, it } from "vitest";
import schemaJson from "../../../../web/src/content/modelos.schema.json";
import { BRANDS, FAMILIES, FAQ_KEYS, KNOWN_KEYS, LIMITS, REQUIRED_FIELDS, SPEC_KEYS, emptySheet, normalizeErrors, toPayload, translateMessage } from "./modelSheet";

const schema = schemaJson as unknown as {
  required: string[];
  properties: Record<string, {
    enum?: string[]; maxLength?: number; pattern?: string;
    items?: { maxLength?: number; pattern?: string; required?: string[]; properties?: Record<string, unknown>; additionalProperties?: boolean };
  }>;
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

  it("knows exactly the schema's properties, so a new one is not silently dropped on publish", () => {
    expect([...KNOWN_KEYS].sort()).toEqual(Object.keys(schema.properties).sort());
  });

  it("pins the item shapes, item lengths and the related-slug pattern", () => {
    expect(schema.properties.specs.items?.required).toEqual([...SPEC_KEYS]);
    expect(Object.keys(schema.properties.specs.items?.properties ?? {})).toEqual([...SPEC_KEYS]);
    expect(schema.properties.faqs.items?.required).toEqual([...FAQ_KEYS]);
    expect(Object.keys(schema.properties.faqs.items?.properties ?? {})).toEqual([...FAQ_KEYS]);
    expect(schema.properties.applications.items?.maxLength).toBe(LIMITS.application);
    expect(schema.properties.accessories.items?.maxLength).toBe(LIMITS.accessory);
    expect(schema.properties.related.items?.pattern).toBe("^[a-z0-9-]+$");
    expect(LIMITS.application).toBe(80);
    expect(LIMITS.accessory).toBe(120);
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
    expect(translateMessage("[...] is too long, it has at most 50 items")).toMatch(/máximo 50/);
    expect(translateMessage("something unforeseen")).toMatch(/esquema/);
  });
});
