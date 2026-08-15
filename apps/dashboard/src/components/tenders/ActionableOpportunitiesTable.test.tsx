import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Availed, CurrentOpportunityRow } from "../../api/institutionIntel/types";
import { ActionableOpportunitiesTable } from "./ActionableOpportunitiesTable";

const getCurrentOpportunities = vi.fn();

vi.mock("../../api/institutionIntel/adapter", () => ({
  institutionIntelAdapter: {
    getCurrentOpportunities: (...args: unknown[]) => getCurrentOpportunities(...args),
  },
}));

function availed(
  status: "available" | "available_empty" | "not_available",
  data?: readonly CurrentOpportunityRow[],
): Availed<readonly CurrentOpportunityRow[]> {
  if (status === "available") return { status: "available", data: data ?? [] };
  if (status === "available_empty") return { status: "available_empty" };
  return { status: "not_available" };
}

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

function stub(result: { availability: Availed<readonly CurrentOpportunityRow[]>; pageInfo: { page: number; pageSize: number; totalItems: number } }) {
  getCurrentOpportunities.mockReset();
  getCurrentOpportunities.mockResolvedValue(result);
}

describe("ActionableOpportunitiesTable", () => {
  it("A: fetches rows from institutionIntelAdapter.getCurrentOpportunities (W1), not any legacy client", async () => {
    stub({ availability: availed("available", [ANTOFAGASTA_ROW]), pageInfo: { page: 1, pageSize: 15, totalItems: 1 } });
    render(<ActionableOpportunitiesTable />);
    await waitFor(() => {
      expect(getCurrentOpportunities).toHaveBeenCalledTimes(1);
    });
    screen.getByText("UNIVERSIDAD DE ANTOFAGASTA");
  });

  it("B: renders a representative public/open W1 row (Universidad de Antofagasta / 4291-46-le26 / centrifuge)", async () => {
    stub({ availability: availed("available", [ANTOFAGASTA_ROW]), pageInfo: { page: 1, pageSize: 15, totalItems: 1 } });
    render(<ActionableOpportunitiesTable />);
    await waitFor(() => {
      screen.getByText("UNIVERSIDAD DE ANTOFAGASTA");
    });
    screen.getByText("4291-46-le26");
    expect(screen.getAllByTestId("actionable-opportunity-row")).toHaveLength(1);
  });

  it("C: a restricted tender (ISP / 1093303-5-CO26) cannot appear — the component renders only what the pre-filtered W1 queue returns, no client-side re-filtering", async () => {
    // Reproduces real production shape: the backend queue simply never
    // includes the restricted tender, so this fixture contains only the
    // open one. No frontend eligibility rule is being tested here — only
    // that the component doesn't add a second, different data source.
    stub({ availability: availed("available", [ANTOFAGASTA_ROW]), pageInfo: { page: 1, pageSize: 15, totalItems: 1 } });
    render(<ActionableOpportunitiesTable />);
    await waitFor(() => {
      screen.getByText("UNIVERSIDAD DE ANTOFAGASTA");
    });
    expect(screen.queryByText(/1093303-5-co26/i)).toBeNull();
    expect(screen.getAllByTestId("actionable-opportunity-row")).toHaveLength(1);
  });

  it("D: a genuinely empty actionable queue renders as empty, not as feed-unavailable", async () => {
    stub({ availability: availed("available_empty"), pageInfo: { page: 1, pageSize: 15, totalItems: 0 } });
    render(<ActionableOpportunitiesTable />);
    await waitFor(() => {
      screen.getByTestId("w1-opportunities-empty");
    });
    expect(screen.queryByTestId("w1-opportunities-unavailable")).toBeNull();
    expect(screen.queryByTestId("actionable-opportunities-table")).toBeNull();
  });

  it("E: reduced_mode / unavailable renders a visible unavailable state, never a fake empty queue", async () => {
    stub({ availability: availed("not_available"), pageInfo: { page: 1, pageSize: 15, totalItems: 0 } });
    render(<ActionableOpportunitiesTable />);
    await waitFor(() => {
      screen.getByTestId("w1-opportunities-unavailable");
    });
    expect(screen.queryByTestId("w1-opportunities-empty")).toBeNull();
    expect(screen.queryByTestId("actionable-opportunities-table")).toBeNull();
  });

  it("F: never renders a contact/outreach action, even though rows carry (false) authorization data", async () => {
    stub({ availability: availed("available", [ANTOFAGASTA_ROW]), pageInfo: { page: 1, pageSize: 15, totalItems: 1 } });
    render(<ActionableOpportunitiesTable />);
    await waitFor(() => {
      screen.getByText("UNIVERSIDAD DE ANTOFAGASTA");
    });
    expect(screen.queryByRole("button", { name: /contact/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /contactar/i })).toBeNull();
    expect(screen.queryByRole("link", { name: /contact/i })).toBeNull();
    expect(screen.queryByText(/outreach/i)).toBeNull();
  });

  it("never shows region, contact email, or a Mercado Público link — W1 does not supply those fields", async () => {
    stub({ availability: availed("available", [ANTOFAGASTA_ROW]), pageInfo: { page: 1, pageSize: 15, totalItems: 1 } });
    render(<ActionableOpportunitiesTable />);
    await waitFor(() => {
      screen.getByText("UNIVERSIDAD DE ANTOFAGASTA");
    });
    expect(screen.queryByText(/mercadopublico\.cl/i)).toBeNull();
  });

  it("fails closed to unavailable when the W1 request itself rejects", async () => {
    getCurrentOpportunities.mockReset();
    getCurrentOpportunities.mockRejectedValue(new Error("network unavailable"));

    render(<ActionableOpportunitiesTable />);

    await waitFor(() => {
      screen.getByTestId("w1-opportunities-unavailable");
    });

    expect(screen.queryByTestId("w1-opportunities-empty")).toBeNull();
    expect(screen.queryByTestId("actionable-opportunities-table")).toBeNull();
  });
});
