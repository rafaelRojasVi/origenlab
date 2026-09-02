import "@testing-library/jest-dom";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { V2EmptyState } from "./V2EmptyState";

describe("V2EmptyState", () => {
  it("renders title, description, and an optional action", () => {
    const onClick = vi.fn();
    render(
      <V2EmptyState
        title="Aún no hay cotizaciones"
        description="Crea una desde Ventas."
        action={
          <button type="button" onClick={onClick}>
            Ir a Ventas
          </button>
        }
      />,
    );

    expect(screen.getByTestId("v2-empty-state")).toHaveTextContent("Aún no hay cotizaciones");
    fireEvent.click(screen.getByRole("button", { name: "Ir a Ventas" }));
    expect(onClick).toHaveBeenCalled();
  });
});
