import "@testing-library/jest-dom";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TopBar } from "./CrmApp";

describe("header quick links", () => {
  it("always shows Drive, Gmail and the website, each in a new tab, as the shared account", () => {
    render(<TopBar onMenu={() => undefined} menuOpen={false} />);
    const nav = within(screen.getByRole("navigation", { name: "Accesos directos" }));
    const expected: [string, string][] = [
      ["Drive", "https://drive.google.com/drive/my-drive?authuser=contacto%40origenlab.cl"],
      ["Gmail", "https://mail.google.com/mail/?authuser=contacto%40origenlab.cl"],
      ["Sitio web", "https://origenlab.cl/"],
    ];
    const links = nav.getAllByRole("link");
    expect(links).toHaveLength(expected.length);
    for (const [name, href] of expected) {
      expect(nav.getByRole("link", { name })).toHaveAttribute("href", href);
    }
    for (const a of links) {
      expect(a).toHaveAttribute("target", "_blank");
      expect(a).toHaveAttribute("rel", "noopener noreferrer");
    }
  });

  it("never shows the internal «datos locales» chip", () => {
    render(<TopBar onMenu={() => undefined} menuOpen={false} />);
    expect(screen.queryByText(/datos locales/)).not.toBeInTheDocument();
  });
});
