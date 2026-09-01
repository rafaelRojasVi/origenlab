import "@testing-library/jest-dom";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { VentasPage } from "./VentasPage";
import { fetchSalesOpportunities } from "../api/commercialOperationsClient";

vi.mock("../api/commercialOperationsClient", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/commercialOperationsClient")>();
  return {
    ...actual,
    fetchSalesOpportunities: vi.fn(),
  };
});

describe("VentasPage", () => {
  beforeEach(() => {
    vi.mocked(fetchSalesOpportunities).mockReset().mockResolvedValue({
      meta: { data_source: "postgres", read_only: true, count: 0, total_count: 0, limit: 200, offset: 0 },
      items: [],
    });
  });

  it("renders one heading (no duplicate page title) and the active board", async () => {
    render(<VentasPage />);

    await waitFor(() => {
      expect(screen.getByTestId("sales-opportunity-board-desktop")).toBeInTheDocument();
    });

    expect(screen.getByRole("heading", { name: "Oportunidades activas" })).toBeInTheDocument();
    expect(vi.mocked(fetchSalesOpportunities)).toHaveBeenCalledWith(
      expect.objectContaining({ stage: ["new", "qualifying", "qualified", "quoting", "negotiating"] }),
    );
  });

  it("refetches when the refresh action is clicked", async () => {
    render(<VentasPage />);
    await waitFor(() => expect(vi.mocked(fetchSalesOpportunities)).toHaveBeenCalledTimes(1));

    screen.getByRole("button", { name: /Actualizar datos/ }).click();
    await waitFor(() => expect(vi.mocked(fetchSalesOpportunities)).toHaveBeenCalledTimes(2));
  });
});
