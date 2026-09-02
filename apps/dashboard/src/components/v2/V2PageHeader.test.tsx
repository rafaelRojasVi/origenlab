import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { V2PageHeader } from "./V2PageHeader";

describe("V2PageHeader", () => {
  it("renders title, optional subtitle, and actions", () => {
    render(
      <V2PageHeader
        title="Ventas"
        subtitle="Oportunidades activas"
        actions={<button type="button">Actualizar</button>}
      />,
    );

    expect(screen.getByRole("heading", { name: "Ventas" })).toBeInTheDocument();
    expect(screen.getByText("Oportunidades activas")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Actualizar" })).toBeInTheDocument();
  });

  it("omits subtitle and actions when not provided", () => {
    render(<V2PageHeader title="Cotizaciones" />);
    expect(screen.getByRole("heading", { name: "Cotizaciones" })).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
