import { describe, expect, it } from "vitest";

import { buildV2DetailHash, parseV2DetailId } from "./v2DeepLink";

const ID = "96301691-af05-51ea-82e3-05f5fae40837";

describe("v2DeepLink", () => {
  it("round-trips an id through the hash", () => {
    expect(buildV2DetailHash("contactos", ID)).toBe(`#/contactos?id=${ID}`);
    expect(parseV2DetailId(buildV2DetailHash("contactos", ID), "contactos")).toBe(ID);
  });

  it("lower-cases the id, because the proxy allows lower-case hex and nothing else", () => {
    // An upper-case UUID in a pasted link would otherwise reach the Worker and come back
    // 403, which reads to an operator like an outage rather than like a bad link.
    expect(parseV2DetailId(`#/casos?id=${ID.toUpperCase()}`, "casos")).toBe(ID);
    expect(buildV2DetailHash("casos", ID.toUpperCase())).toBe(`#/casos?id=${ID}`);
  });

  it("is not a deep link for another section", () => {
    expect(parseV2DetailId(`#/instituciones?id=${ID}`, "contactos")).toBeNull();
  });

  it("refuses anything that is not the shape the proxy admits", () => {
    expect(parseV2DetailId("#/contactos?id=compras@uni.example", "contactos")).toBeNull();
    expect(parseV2DetailId("#/contactos?id=op-1", "contactos")).toBeNull();
    expect(parseV2DetailId("#/contactos?id=", "contactos")).toBeNull();
    expect(parseV2DetailId("#/contactos", "contactos")).toBeNull();
    expect(parseV2DetailId("", "contactos")).toBeNull();
  });

  it("ignores other query parameters rather than failing on them", () => {
    expect(parseV2DetailId(`#/casos?tab=x&id=${ID}`, "casos")).toBe(ID);
  });
});
