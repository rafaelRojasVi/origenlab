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

describe("Button", () => {
  it("is disabled, says what it is doing and marks itself busy while its write runs", async () => {
    const { Button } = await import("./ui");
    const { rerender } = render(<Button variant="primary">Guardar</Button>);
    const button = screen.getByRole("button", { name: "Guardar" });
    expect(button).toBeEnabled();
    expect(button).not.toHaveAttribute("aria-busy");
    rerender(
      <Button variant="primary" busy busyLabel="Guardando…">
        Guardar
      </Button>,
    );
    const busy = screen.getByRole("button", { name: "Guardando…" });
    expect(busy).toBeDisabled();
    expect(busy).toHaveAttribute("aria-busy", "true");
    expect(busy).toHaveAttribute("type", "button");
  });
});

describe("toast", () => {
  it("shows a short line in the Toaster and lets it be dismissed", async () => {
    const { Toaster, toast } = await import("./ui");
    const { act, fireEvent } = await import("@testing-library/react");
    render(<Toaster />);
    act(() => toast("Nota agregada."));
    expect(screen.getByRole("status")).toHaveTextContent("Nota agregada.");
    fireEvent.click(screen.getByRole("button", { name: "Cerrar aviso" }));
    expect(screen.queryByRole("status")).toBeNull();
    act(() => toast("No se pudo guardar.", "bad"));
    expect(screen.getByRole("alert")).toHaveTextContent("No se pudo guardar.");
  });
});
