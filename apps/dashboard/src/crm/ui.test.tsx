import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ExternalLink, isSafeExternalHref } from "./ui";

describe("ExternalLink", () => {
  it("renders an https link with noopener noreferrer in a new tab", () => {
    render(<ExternalLink href="https://origenlab.cl/marcas/ika/">Ficha</ExternalLink>);
    const a = screen.getByRole("link", { name: /Ficha/ });
    expect(a).toHaveAttribute("href", "https://origenlab.cl/marcas/ika/");
    expect(a).toHaveAttribute("target", "_blank");
    expect(a).toHaveAttribute("rel", "noopener noreferrer");
  });

  it.each([
    "javascript:alert(1)",
    " javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "blob:https://origenlab.cl/1234",
    "vbscript:msgbox(1)",
    "file:///etc/hostname",
    "/relative/path",
    "//origenlab.cl/protocol-relative",
    "",
  ])("never renders %j as a link", (href) => {
    render(<ExternalLink href={href}>Abrir</ExternalLink>);
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByTestId("external-link-refused")).toHaveTextContent("Abrir");
    expect(isSafeExternalHref(href)).toBe(false);
  });
});
