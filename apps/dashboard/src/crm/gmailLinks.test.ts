import { describe, expect, it } from "vitest";
import { composeInSharedMailbox, inSharedMailbox } from "./gmailLinks";

describe("Gmail links", () => {
  it("opens a message or a search as the shared mailbox, whatever account the browser lists first", () => {
    expect(inSharedMailbox("https://mail.google.com/mail/u/0/#all/abc123")).toBe(
      "https://mail.google.com/mail/?authuser=contacto%40origenlab.cl#all/abc123",
    );
    expect(inSharedMailbox("https://mail.google.com/mail/u/2/#search/rfc822msgid%3Ax")).toBe(
      "https://mail.google.com/mail/?authuser=contacto%40origenlab.cl#search/rfc822msgid%3Ax",
    );
    expect(inSharedMailbox("https://drive.google.com/file/d/x")).toBe("https://drive.google.com/file/d/x");
    expect(inSharedMailbox(null)).toBeNull();
  });

  it("writes a new email from the shared mailbox, addressed when the address is known", () => {
    const url = new URL(composeInSharedMailbox("ana@ejemplo.cl", "Seguimiento cotización N° 01239-26"));
    expect(url.searchParams.get("authuser")).toBe("contacto@origenlab.cl");
    expect(url.searchParams.get("view")).toBe("cm");
    expect(url.searchParams.get("to")).toBe("ana@ejemplo.cl");
    expect(url.searchParams.get("su")).toBe("Seguimiento cotización N° 01239-26");
    expect(new URL(composeInSharedMailbox(null, "x")).searchParams.has("to")).toBe(false);
  });
});
