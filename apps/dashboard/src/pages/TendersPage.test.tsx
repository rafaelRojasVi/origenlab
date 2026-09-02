import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Availed, CurrentOpportunityRow, LicitacionIntel } from "../api/institutionIntel/types";
import { dashboardSectionLabel } from "../lib/dashboardNav";
import { TendersPage } from "./TendersPage";

const getCurrentOpportunities = vi.fn();
const getLicitacionIntel = vi.fn();

vi.mock("../api/institutionIntel/adapter", () => ({
  institutionIntelAdapter: {
    getCurrentOpportunities: (...args: unknown[]) => getCurrentOpportunities(...args),
    getLicitacionIntel: (...args: unknown[]) => getLicitacionIntel(...args),
  },
}));

const ANTOFAGASTA_ROW: CurrentOpportunityRow = {
  queue: "current_opportunity_queue",
  rowId: "row-antofagasta-1",
  institutionId: "univ-antofagasta-hash",
  institutionDisplayName: "UNIVERSIDAD DE ANTOFAGASTA",
  tenderCode: "4291-46-le26",
  categoryOrTitle: "centrifuge",
  closeTimestamp: "2026-08-24T19:00:00",
  eligibilityStatus: "open_public",
  prospectStrengthBand: "high",
};

function stub(availability: Availed<readonly CurrentOpportunityRow[]>) {
  getCurrentOpportunities.mockReset();
  getCurrentOpportunities.mockResolvedValue({
    availability,
    pageInfo: { page: 1, pageSize: 15, totalItems: availability.status === "available" ? availability.data.length : 0 },
  });
}

describe("TendersPage", () => {
  it("G: dashboard navigation labels this section 'Licitaciones'", () => {
    expect(dashboardSectionLabel("tenders")).toBe("Licitaciones");
  });

  it("W1 current_opportunity_queue is the page's only procurement-opportunity source", async () => {
    stub({ status: "available", data: [ANTOFAGASTA_ROW] });
    render(<TendersPage />);

    await waitFor(() => {
      expect(getCurrentOpportunities).toHaveBeenCalledTimes(1);
    });
    screen.getByText("Oportunidades accionables");
    screen.getByText("UNIVERSIDAD DE ANTOFAGASTA");
    screen.getByText("4291-46-le26");
  });

  it("does not render the legacy EquipmentOpportunitiesTable in any form", async () => {
    stub({ status: "available", data: [ANTOFAGASTA_ROW] });
    render(<TendersPage />);
    await waitFor(() => {
      screen.getByText("UNIVERSIDAD DE ANTOFAGASTA");
    });

    // Markers unique to the legacy table — none of them should exist anywhere
    // on this page now, not even demoted/labeled.
    expect(screen.queryByText("Oportunidades de equipos")).toBeNull();
    expect(screen.queryByTestId("equipment-table-footer")).toBeNull();
    expect(screen.queryByTestId("equipment-feed-unavailable")).toBeNull();
    expect(screen.queryByTestId("legacy-equipment-signals-section")).toBeNull();
    expect(screen.queryByText(/Señales candidatas/)).toBeNull();
  });

  it("quote_now / 'Cotizar ahora' from the legacy feed can never appear on this page", async () => {
    // Even with a populated W1 result, nothing in the real TendersPage tree
    // can ever render the legacy triage vocabulary, because the legacy
    // table is not mounted here at all (not just filtered/hidden).
    stub({ status: "available", data: [ANTOFAGASTA_ROW] });
    render(<TendersPage />);
    await waitFor(() => {
      screen.getByText("UNIVERSIDAD DE ANTOFAGASTA");
    });
    // Exact-text match, not a substring/regex: the new W1 table's own
    // subtitle honestly describes itself as the source for "cotizar ahora"
    // decisions, which is fine — what must never appear is the legacy
    // per-row TokenLabel triage badge, whose entire text content is exactly
    // this string with no surrounding sentence.
    expect(screen.queryByText("Cotizar ahora")).toBeNull();
    expect(screen.queryByText("quote_now")).toBeNull();
    expect(screen.queryByText("Próxima acción")).toBeNull();
  });

  it("no legacy contact action (ContactEmailButton or equivalent) can appear on this page", async () => {
    stub({ status: "available", data: [ANTOFAGASTA_ROW] });
    render(<TendersPage />);
    await waitFor(() => {
      screen.getByText("UNIVERSIDAD DE ANTOFAGASTA");
    });
    expect(screen.queryByRole("button", { name: /contact/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /contactar/i })).toBeNull();
    expect(screen.queryByText(/mercadopublico\.cl/i)).toBeNull();
    expect(screen.queryByLabelText(/guardar oportunidad/i)).toBeNull();
  });

  it("shows an empty detail prompt until a row is selected, then opens the drawer for the clicked tender", async () => {
    getLicitacionIntel.mockReset();
    getLicitacionIntel.mockResolvedValue({
      tenderCode: ANTOFAGASTA_ROW.tenderCode,
      buyerDisplayName: ANTOFAGASTA_ROW.institutionDisplayName,
      eligibilityStatus: "open_public",
      procurementMethodRaw: null,
      t1SourceKind: null,
      terms: { status: "not_available" },
      itemBudget: { status: "not_available" },
      totalBudgetReconciled: false,
      recognitionDelta: { status: "not_available" },
      coverage: { status: "not_available" },
    } satisfies LicitacionIntel);

    stub({ status: "available", data: [ANTOFAGASTA_ROW] });
    render(<TendersPage />);

    await waitFor(() => {
      screen.getByText("UNIVERSIDAD DE ANTOFAGASTA");
    });
    screen.getByTestId("tender-detail-empty");

    fireEvent.click(screen.getAllByTestId("actionable-opportunity-row")[0]);

    await waitFor(() => {
      expect(getLicitacionIntel).toHaveBeenCalledWith(ANTOFAGASTA_ROW.tenderCode);
    });
    await waitFor(() => {
      screen.getByTestId("tender-detail-drawer");
    });
    expect(screen.queryByTestId("tender-detail-empty")).toBeNull();

    // Selecting a tender must perform exactly one detail request. The drawer
    // body used to be a self-fetching LicitacionIntelCard mounted alongside
    // the drawer's own fetch, doubling the request for one selection — the
    // body is now presentation-only and fetches nothing.
    expect(getLicitacionIntel).toHaveBeenCalledTimes(1);
  });
});
