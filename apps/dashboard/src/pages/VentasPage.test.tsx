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

// These three child components are stubbed (not the useSalesOpportunityBoard
// hook) so the wiring tests below can drive VentasPage's own callbacks
// (open/close drawer, stage/task-change -> refetch) through simple buttons,
// while the existing tests above still exercise the real hook + real
// fetchSalesOpportunities integration (stage filter, refetch count).
vi.mock("../components/pipeline/SalesOpportunityBoard", () => ({
  SalesOpportunityBoard: ({ onOpenOpportunity }: { onOpenOpportunity: (item: unknown) => void }) => (
    <div data-testid="sales-opportunity-board-desktop">
      <button onClick={() => onOpenOpportunity({ sales_opportunity_id: "sales_1", title: "Centrífuga" })}>
        abrir-desktop
      </button>
    </div>
  ),
}));

vi.mock("../components/pipeline/MobileSalesOpportunityList", () => ({
  MobileSalesOpportunityList: () => <div data-testid="mobile-stub" />,
}));

vi.mock(
  "../components/pipeline/SalesOpportunityWorkspaceDrawer",
  () => ({
    SalesOpportunityWorkspaceDrawer: ({
      item,
      open,
      onClose,
      onStageChanged,
      onTaskChanged,
    }: {
      item: { title: string } | null;
      open: boolean;
      onClose: () => void;
      onStageChanged: () => void;
      onTaskChanged?: () => void;
    }) =>
      open ? (
        <div data-testid="drawer-stub">
          <span>{item?.title}</span>
          <button onClick={onClose}>cerrar-drawer</button>
          <button onClick={onStageChanged}>disparar-cambio</button>
          <button onClick={() => onTaskChanged?.()}>disparar-tarea</button>
        </div>
      ) : null,
  }),
);

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

  it("refetches when the refresh action is clicked (also covers the page-local refresh button)", async () => {
    render(<VentasPage />);
    await waitFor(() => expect(vi.mocked(fetchSalesOpportunities)).toHaveBeenCalledTimes(1));

    screen.getByRole("button", { name: /Actualizar datos/ }).click();
    await waitFor(() => expect(vi.mocked(fetchSalesOpportunities)).toHaveBeenCalledTimes(2));
  });

  it("renders both layouts and opens the drawer from a board callback", async () => {
    render(<VentasPage />);

    expect(screen.getByTestId("mobile-stub")).toBeInTheDocument();
    expect(screen.queryByTestId("drawer-stub")).not.toBeInTheDocument();

    screen.getByText("abrir-desktop").click();
    await waitFor(() => {
      expect(screen.getByTestId("drawer-stub")).toBeInTheDocument();
    });
    expect(screen.getByText("Centrífuga")).toBeInTheDocument();
  });

  it("closes the drawer and refetches the board when the drawer reports a stage change", async () => {
    render(<VentasPage />);
    await waitFor(() => expect(vi.mocked(fetchSalesOpportunities)).toHaveBeenCalledTimes(1));

    screen.getByText("abrir-desktop").click();
    await waitFor(() => {
      expect(screen.getByTestId("drawer-stub")).toBeInTheDocument();
    });

    screen.getByText("disparar-cambio").click();
    await waitFor(() => expect(vi.mocked(fetchSalesOpportunities)).toHaveBeenCalledTimes(2));

    screen.getByText("cerrar-drawer").click();
    await waitFor(() => {
      expect(screen.queryByTestId("drawer-stub")).not.toBeInTheDocument();
    });
  });

  it("refetches the board when the drawer reports a task change", async () => {
    render(<VentasPage />);
    await waitFor(() => expect(vi.mocked(fetchSalesOpportunities)).toHaveBeenCalledTimes(1));

    screen.getByText("abrir-desktop").click();
    await waitFor(() => {
      expect(screen.getByTestId("drawer-stub")).toBeInTheDocument();
    });

    screen.getByText("disparar-tarea").click();
    await waitFor(() => expect(vi.mocked(fetchSalesOpportunities)).toHaveBeenCalledTimes(2));
  });

  it("disables the refresh button and shows a loading label while the board is loading", () => {
    render(<VentasPage />);

    const button = screen.getByRole("button", { name: "Actualizando…" });
    expect(button).toBeDisabled();
  });
});
