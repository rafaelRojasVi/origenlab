import "@testing-library/jest-dom";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DashboardSidebar } from "./DashboardSidebar";

describe("DashboardSidebar", () => {
  it("renders the 8 primary items in Cotizaciones-first order", () => {
    render(
      <DashboardSidebar active="cotizaciones" collapsed={false} onNavigate={vi.fn()} onToggleCollapsed={vi.fn()} />,
    );
    const nav = screen.getByRole("navigation", { name: "Navegación del panel" });
    expect(within(nav).getAllByRole("link").map((el) => el.textContent)).toEqual([
      "Cotizaciones",
      "Licitaciones",
      "Ventas",
      "Clientes",
      "Prospectos",
      "Correos",
      "Catálogo",
      "Sistema",
    ]);
  });

  it("marks Cotizaciones, Licitaciones and Ventas as visually emphasized", () => {
    render(
      <DashboardSidebar active="cotizaciones" collapsed={false} onNavigate={vi.fn()} onToggleCollapsed={vi.fn()} />,
    );
    const nav = screen.getByRole("navigation", { name: "Navegación del panel" });
    for (const label of ["Cotizaciones", "Licitaciones", "Ventas"]) {
      expect(within(nav).getByRole("link", { name: label }).getAttribute("data-emphasized")).toBe("true");
    }
    for (const label of ["Clientes", "Prospectos", "Correos", "Catálogo", "Sistema"]) {
      expect(within(nav).getByRole("link", { name: label }).getAttribute("data-emphasized")).toBe("false");
    }
  });

  it("bare-hash href resolves to the default section, not literally 'today'", () => {
    render(
      <DashboardSidebar active="cotizaciones" collapsed={false} onNavigate={vi.fn()} onToggleCollapsed={vi.fn()} />,
    );
    const nav = screen.getByRole("navigation", { name: "Navegación del panel" });
    expect(within(nav).getByRole("link", { name: "Cotizaciones" }).getAttribute("href")).toBe("#/");
  });
});
