import { describe, expect, it } from "vitest";
import type { AuthSessionState } from "../api/authClient";
import { contactAddressesRedacted, isMaskedAddress } from "./redaction";

function signedIn(role: string): AuthSessionState {
  return {
    kind: "signed_in",
    method: "google_session",
    operator: { operatorId: "op-1", email: "operador@ejemplo.invalid", displayName: "Operador", role },
  };
}

describe("contactAddressesRedacted", () => {
  it("is true for a viewer and any role the API does not list", () => {
    expect(contactAddressesRedacted(signedIn("viewer"))).toBe(true);
    expect(contactAddressesRedacted(signedIn("owner"))).toBe(true);
  });

  it("is false for sales and admin", () => {
    expect(contactAddressesRedacted(signedIn("sales"))).toBe(false);
    expect(contactAddressesRedacted(signedIn("admin"))).toBe(false);
  });

  it("is false without a confirmed session — there is nothing to explain yet", () => {
    expect(contactAddressesRedacted({ kind: "loading" })).toBe(false);
    expect(contactAddressesRedacted({ kind: "error", message: "x" })).toBe(false);
  });
});

describe("isMaskedAddress", () => {
  it("recognises the API's masked forms", () => {
    expect(isMaskedAddress("***@ejemplo.invalid")).toBe(true);
    expect(isMaskedAddress("Persona <***@ejemplo.invalid>")).toBe(true);
    expect(isMaskedAddress("***")).toBe(true);
  });

  it("does not flag a real address or an empty value", () => {
    expect(isMaskedAddress("persona@ejemplo.invalid")).toBe(false);
    expect(isMaskedAddress("")).toBe(false);
    expect(isMaskedAddress(null)).toBe(false);
  });
});
