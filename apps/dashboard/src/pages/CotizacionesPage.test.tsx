import "@testing-library/jest-dom";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CotizacionesPage } from "./CotizacionesPage";

describe("CotizacionesPage", () => {
  it("renders an honest empty state and a link to Ventas", () => {
    const onOpenVentas = vi.fn();
    render(<CotizacionesPage onOpenVentas={onOpenVentas} />);

    expect(screen.getByRole("heading", { name: "Vista consolidada" })).toBeInTheDocument();
    expect(screen.getByText(/cada cotización vive dentro de su oportunidad/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Ir a Ventas" }));
    expect(onOpenVentas).toHaveBeenCalled();
  });

  it("does not claim any SQLite/dashboard implementation vocabulary", () => {
    render(<CotizacionesPage onOpenVentas={vi.fn()} />);
    expect(screen.queryByText(/SQLite/i)).not.toBeInTheDocument();
  });
});
