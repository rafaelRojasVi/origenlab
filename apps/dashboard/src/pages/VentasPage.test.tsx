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
      item: { title: string; sales_opportunity_id: string } | null;
      open: boolean;
      onClose: () => void;
      onStageChanged: () => void;
      onTaskChanged?: () => void;
    }) =>
      open ? (
        <div data-testid="drawer-stub">
          <span>{item?.title}</span>
          <span data-testid="drawer-stub-id">{item?.sales_opportunity_id}</span>
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

  it("safely opens the exact opportunity's drawer when a valid deep-link id matches a loaded item", async () => {
    const targetId = "sales_" + "d".repeat(32);
    vi.mocked(fetchSalesOpportunities).mockReset().mockResolvedValue({
      meta: { data_source: "postgres", read_only: true, count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [
        {
          sales_opportunity_id: targetId,
          source_kind: "manual",
          source_opportunity_id: targetId,
          account_id: null,
          primary_contact_id: null,
          organization_id: null,
          primary_crm_contact_id: null,
          title: "Reactor CEAF",
          stage: "quoting",
          owner_key: "op@origenlab.cl",
          version: 1,
          created_by: "op@origenlab.cl",
          updated_by: "op@origenlab.cl",
          created_at: "2026-08-30T10:00:00Z",
          updated_at: "2026-08-30T10:00:00Z",
          stage_updated_at: "2026-08-30T10:00:00Z",
          contact_display_email: null,
          account_display_domain: null,
          organization_display_name: "CEAF",
          contact_display_name: "Tatiana Rojas",
          contact_primary_email: "tatiana@ceaf.cl",
          open_task_count: 0,
          next_task_id: null,
          next_task_title: null,
          next_task_due_at: null,
        },
      ],
    });

    render(<VentasPage deepLinkOpportunityId={targetId} />);

    await waitFor(() => {
      expect(screen.getByTestId("drawer-stub")).toBeInTheDocument();
    });
    expect(screen.getByTestId("drawer-stub-id").textContent).toBe(targetId);
  });

  it("does nothing for an invalid or unmatched deep-link id — no crash, no drawer", async () => {
    vi.mocked(fetchSalesOpportunities).mockReset().mockResolvedValue({
      meta: { data_source: "postgres", read_only: true, count: 0, total_count: 0, limit: 200, offset: 0 },
      items: [],
    });

    render(<VentasPage deepLinkOpportunityId="not-a-real-id" />);

    await waitFor(() => expect(vi.mocked(fetchSalesOpportunities)).toHaveBeenCalled());
    expect(screen.queryByTestId("drawer-stub")).not.toBeInTheDocument();
  });
});
