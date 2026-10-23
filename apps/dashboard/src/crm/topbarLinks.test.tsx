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

describe("the Cotizador section", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    clearResourceCache();
    localStorage.clear();
  });

  const QUOTE = "6bafddfa-0000-4000-8000-0000000000d1";
  const session = (role: string): AuthSessionState => ({
    kind: "signed_in",
    method: "google_profile",
    operator: { operatorId: `op-${role}`, email: "ventas@example.cl", displayName: "Ventas", role },
    caseCommandsEnabled: true,
    crmAuthoringEnabled: true,
  });
  function stubFetch() {
    const paths: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
        paths.push(new URL(raw, "http://localhost").pathname);
        return Promise.resolve(new Response("{}", { status: 404 }));
      }),
    );
    return paths;
  }

  it("is in the menu for sales, between Oportunidades and Organizaciones, lit while open", async () => {
    stubFetch();
    render(
      <AuthSessionContext.Provider value={{ session: session("sales"), signOut: async () => true }}>
        <CrmApp route={{ section: "cotizador", id: QUOTE }} />
      </AuthSessionContext.Provider>,
    );
    expect(document.title).toBe("Cotizador · OrigenLab");
    const menu = within(screen.getByRole("navigation", { name: "Secciones del panel" }));
    const names = menu.getAllByRole("link").map((a) => a.textContent);
    expect(names.indexOf("Cotizador")).toBe(names.indexOf("Oportunidades") + 1);
    expect(names.indexOf("Licitaciones")).toBe(names.indexOf("Cotizador") + 1);
    expect(names.indexOf("Prospectos")).toBe(names.indexOf("Licitaciones") + 1);
    expect(names.indexOf("Organizaciones")).toBe(names.indexOf("Prospectos") + 1);
    expect(menu.getByRole("link", { name: "Licitaciones" })).toHaveAttribute("href", "#/crm/licitaciones");
    expect(menu.getByRole("link", { name: "Cotizador" })).toHaveAttribute("aria-current", "page");
    expect(menu.getByRole("link", { name: "Cotizador" })).toHaveAttribute("href", "#/crm/cotizador");
    expect(screen.getByRole("tablist", { name: "Cotizaciones abiertas" })).toBeInTheDocument();
    expect(await screen.findByText("Esta cotización no existe.")).toBeInTheDocument();
  });

  it("is hidden from a viewer, and a viewer's deep link says only sales and administration quote, reading nothing", async () => {
    const paths = stubFetch();
    render(
      <AuthSessionContext.Provider value={{ session: session("viewer"), signOut: async () => true }}>
        <CrmApp route={{ section: "cotizador", id: QUOTE }} />
      </AuthSessionContext.Provider>,
    );
    const menu = within(screen.getByRole("navigation", { name: "Secciones del panel" }));
    expect(menu.queryByRole("link", { name: "Cotizador" })).not.toBeInTheDocument();
    expect(menu.getByRole("link", { name: "Oportunidades" })).toBeInTheDocument();
    expect(menu.getByRole("link", { name: "Licitaciones" })).toBeInTheDocument();
    expect(screen.getByText("Sólo ventas y administración pueden cotizar.")).toBeInTheDocument();
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(paths.some((p) => p.startsWith("/v2/quotes"))).toBe(false);
    expect(localStorage.getItem("cotizador:tabs:op-viewer")).toBeNull();
  });
});
