import { afterEach, describe, expect, it, vi } from "vitest";
import { formatWhatsappNumber, parseWhatsappNumber, whatsappQuoteUrl } from "./company";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe("parseWhatsappNumber", () => {
  it("keeps E.164 digits and tolerates the usual punctuation", () => {
    expect(parseWhatsappNumber("56912345678")).toBe("56912345678");
    expect(parseWhatsappNumber(" +56 9 1234 5678 ")).toBe("56912345678");
    expect(parseWhatsappNumber("+56-9-1234-5678")).toBe("56912345678");
  });
  it("is null for empty, missing or malformed values", () => {
    for (const bad of [undefined, null, "", "   ", "abc", "1234567", "5691234567890123", "56 9 12x4 5678"]) {
      expect(parseWhatsappNumber(bad)).toBeNull();
    }
  });
});

describe("WHATSAPP_NUMBER", () => {
  it("is read from VITE_ORIGENLAB_WHATSAPP, null when empty", async () => {
    vi.stubEnv("VITE_ORIGENLAB_WHATSAPP", "+56 9 1234 5678");
    expect((await import("./company")).WHATSAPP_NUMBER).toBe("56912345678");
    vi.resetModules();
    vi.stubEnv("VITE_ORIGENLAB_WHATSAPP", "");
    expect((await import("./company")).WHATSAPP_NUMBER).toBeNull();
  });
});

describe("whatsappQuoteUrl", () => {
  it("opens wa.me with the prefilled text, encoded", () => {
    const url = whatsappQuoteUrl("56912345678", "CN01272");
    expect(url.startsWith("https://wa.me/56912345678?text=")).toBe(true);
    const text = new URL(url).searchParams.get("text");
    expect(text).toBe("Hola OrigenLab, consulto por la cotización CN01272.");
    expect(url).toContain("cotizaci%C3%B3n%20CN01272.");
    expect(url).not.toContain(" ");
  });
});

describe("formatWhatsappNumber", () => {
  it("groups a Chilean mobile as +56 9 XXXX XXXX and leaves others as +digits", () => {
    expect(formatWhatsappNumber("56912345678")).toBe("+56 9 1234 5678");
    expect(formatWhatsappNumber("14155550100")).toBe("+14155550100");
  });
});
