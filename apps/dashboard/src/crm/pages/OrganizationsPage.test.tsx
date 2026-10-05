/**
 * Organizaciones lists institutions only. Suppliers have their own page (Proveedores), so the
 * segment control offers Clientes and Otras, and a link leads to Proveedores.
 */
import "@testing-library/jest-dom";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OrganizationsPage } from "./OrganizationsPage";

// Every value below is invented; the repository is public.
function stub(facets: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const path = new URL(url, "http://localhost").pathname;
      if (path === "/v2/organizations") {
        const body = { items: [], total: 0, limit: 60, offset: 0, ...(facets ? { facets } : {}) };
        return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
      }
      return Promise.resolve(new Response("{}", { status: 404 }));
    }),
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("OrganizationsPage segments", () => {
  it("offers exactly Clientes and Otras, and links to Proveedores without a misleading count", async () => {
    stub({ customers: 5, suppliers: 7, others: 2, all: 14 });
    const navigate = vi.fn();
    render(<OrganizationsPage navigate={navigate} />);
    await screen.findByText("Ninguna organización coincide");
    const group = screen.getByRole("group", { name: "Segmento" });
    expect(within(group).getAllByRole("button").map((b) => b.textContent)).toEqual(["Clientes", "Otras"]);
    expect(screen.queryByText("Proveedores", { selector: "dt" })).toBeNull();
    // the supplier facet counts CRM organisations, not the brands Proveedores lists — no number
    fireEvent.click(screen.getByRole("button", { name: "Proveedores →" }));
    expect(navigate).toHaveBeenCalledWith("proveedores");
  });

});
