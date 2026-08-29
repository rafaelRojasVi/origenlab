import "@testing-library/jest-dom";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PipelinePage } from "./PipelinePage";
import { useSalesOpportunityBoard } from "../components/pipeline/useSalesOpportunityBoard";

vi.mock("../components/pipeline/useSalesOpportunityBoard", () => ({
  useSalesOpportunityBoard: vi.fn(),
}));

vi.mock("../components/pipeline/SalesOpportunityBoard", () => ({
  SalesOpportunityBoard: ({ onOpenOpportunity }: { onOpenOpportunity: (item: unknown) => void }) => (
    <button onClick={() => onOpenOpportunity({ sales_opportunity_id: "sales_1", title: "Centrífuga" })}>
      abrir-desktop
    </button>
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

function fakeBoard(refetch = vi.fn()) {
  return {
    items: [],
    loading: false,
    error: null,
    enabledToggles: [],
    toggleStage: vi.fn(),
    refetch,
    changeStage: vi.fn(),
    pendingStageChangeId: null,
    stageError: null,
    dismissStageError: vi.fn(),
  };
}

describe("PipelinePage", () => {
  it("renders both layouts and opens the drawer from a board callback", async () => {
    vi.mocked(useSalesOpportunityBoard).mockReturnValue(fakeBoard());
    render(<PipelinePage />);

    expect(screen.getByTestId("mobile-stub")).toBeInTheDocument();
    expect(screen.queryByTestId("drawer-stub")).not.toBeInTheDocument();

    screen.getByText("abrir-desktop").click();
    await waitFor(() => {
      expect(screen.getByTestId("drawer-stub")).toBeInTheDocument();
    });
    expect(screen.getByText("Centrífuga")).toBeInTheDocument();
  });

  it("closes the drawer and refetches the board when the drawer reports a stage change", async () => {
    const refetch = vi.fn();
    vi.mocked(useSalesOpportunityBoard).mockReturnValue(fakeBoard(refetch));
    render(<PipelinePage />);

    screen.getByText("abrir-desktop").click();
    await waitFor(() => {
      expect(screen.getByTestId("drawer-stub")).toBeInTheDocument();
    });

    screen.getByText("disparar-cambio").click();
    expect(refetch).toHaveBeenCalled();

    screen.getByText("cerrar-drawer").click();
    await waitFor(() => {
      expect(screen.queryByTestId("drawer-stub")).not.toBeInTheDocument();
    });
  });

  it("refetches the board when the drawer reports a task change", async () => {
    const refetch = vi.fn();
    vi.mocked(useSalesOpportunityBoard).mockReturnValue(fakeBoard(refetch));
    render(<PipelinePage />);

    screen.getByText("abrir-desktop").click();
    await waitFor(() => {
      expect(screen.getByTestId("drawer-stub")).toBeInTheDocument();
    });

    screen.getByText("disparar-tarea").click();
    expect(refetch).toHaveBeenCalled();
  });

  it("refetches the board when the page-local refresh button is clicked", () => {
    const refetch = vi.fn();
    vi.mocked(useSalesOpportunityBoard).mockReturnValue(fakeBoard(refetch));
    render(<PipelinePage />);

    screen.getByRole("button", { name: "Actualizar datos" }).click();
    expect(refetch).toHaveBeenCalled();
  });

  it("disables the refresh button and shows a loading label while the board is loading", () => {
    vi.mocked(useSalesOpportunityBoard).mockReturnValue({ ...fakeBoard(), loading: true });
    render(<PipelinePage />);

    const button = screen.getByRole("button", { name: "Actualizando…" });
    expect(button).toBeDisabled();
  });
});
