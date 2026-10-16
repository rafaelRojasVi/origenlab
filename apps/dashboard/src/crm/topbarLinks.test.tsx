import "@testing-library/jest-dom";
import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../api/authClient";
import { AuthSessionContext } from "../context/AuthSessionContext";
import { CrmApp, TopBar } from "./CrmApp";
import { clearResourceCache } from "./useResource";

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

describe("the hidden Cotizador section", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    clearResourceCache();
  });

  it("opens with its own title, keeps Oportunidades lit and has no menu entry of its own", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response("{}", { status: 404 }))));
    const session: AuthSessionState = {
      kind: "signed_in",
      method: "google_profile",
      operator: { operatorId: "op-sales", email: "ventas@example.cl", displayName: "Ventas", role: "sales" },
      caseCommandsEnabled: true,
      crmAuthoringEnabled: true,
    };
    render(
      <AuthSessionContext.Provider value={{ session, signOut: async () => true }}>
        <CrmApp route={{ section: "cotizar", id: "6bafddfa-0000-4000-8000-0000000000d1" }} />
      </AuthSessionContext.Provider>,
    );
    expect(document.title).toBe("Cotizador · OrigenLab");
    const menu = within(screen.getByRole("navigation", { name: "Secciones del panel" }));
    expect(menu.getByRole("link", { name: "Oportunidades" })).toHaveAttribute("aria-current", "page");
    expect(menu.queryByRole("link", { name: "Cotizador" })).not.toBeInTheDocument();
    expect(await screen.findByText("Esta cotización no existe.")).toBeInTheDocument();
  });
});
