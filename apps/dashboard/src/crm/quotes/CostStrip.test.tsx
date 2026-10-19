/**
 * The cost strip under a line: «Me cuesta → margen → Le cobro», every number the API's. Invented values.
 */
import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CostStrip } from "./CostStrip";
import { lineEconomics } from "./quoteFixtures";

describe("CostStrip", () => {
  it("reads cost with its expenses, the margin and what is charged", () => {
    render(<CostStrip economics={lineEconomics()} n={1} />);
    const strip = screen.getByTestId("cost-strip");
    expect(strip).toHaveAccessibleName("Costo y margen de la línea 1");
    expect(strip).toHaveTextContent("Me cuesta$ 880.000equipo $ 450.000 + gastos $ 430.000");
    expect(strip).toHaveTextContent("31,0 %");
    expect(strip).toHaveTextContent("gano $ 395.000");
    expect(strip).toHaveTextContent("Le cobro (neto)$ 1.275.000+ IVA = $ 1.517.250");
    expect(screen.getByText("31,0 %").className).toContain("text-good");
  });

  it("is amber outside the range and red at a loss", () => {
    const { unmount } = render(<CostStrip economics={lineEconomics({ gross_margin: "0.12", margin_state: "below_target" })} n={1} />);
    expect(screen.getByText("12,0 %").className).toContain("text-warn");
    unmount();
    render(<CostStrip economics={lineEconomics({ gross_margin: "-0.05", gain_clp: "-50000", margin_state: "loss" })} n={1} />);
    expect(screen.getByText("-5,0 %").className).toContain("text-bad");
  });

  it("a line with no expenses says so; one without cost says the margin is unknown", () => {
    const { unmount } = render(<CostStrip economics={lineEconomics({ expenses_clp: "0", cost_clp: "450000" })} n={2} />);
    expect(screen.getByTestId("cost-strip")).toHaveTextContent("sin gastos de importación");
    unmount();
    render(
      <CostStrip
        economics={lineEconomics({ product_cost_clp: null, cost_clp: null, gain_clp: null, gross_margin: null, margin_state: "no_cost" })}
        n={2}
      />,
    );
    const strip = screen.getByTestId("cost-strip");
    expect(strip).toHaveTextContent("Sin costo: no se puede saber el margen.");
    expect(strip).not.toHaveTextContent("gano");
    expect(strip).toHaveTextContent("$ 1.275.000");
  });
});
