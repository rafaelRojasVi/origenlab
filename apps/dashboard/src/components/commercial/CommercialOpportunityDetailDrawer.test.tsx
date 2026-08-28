import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CommercialOpportunityDetailDrawer } from "./CommercialOpportunityDetailDrawer";
import { fetchCommercialOpportunityDetail } from "../../api/operatorClient";
import {
  fetchCommercialOpportunityActivities,
  fetchCommercialOpportunityOperatorState,
  fetchCommercialOpportunityTasks,
  promoteSalesOpportunity,
} from "../../api/commercialOperationsClient";
import { OperatorApiError } from "../../api/operatorClient";

vi.mock("../../api/operatorClient", async () => {
  const actual = await vi.importActual<typeof import("../../api/operatorClient")>("../../api/operatorClient");
  return { ...actual, fetchCommercialOpportunityDetail: vi.fn() };
});

vi.mock("../../api/commercialOperationsClient", () => ({
  fetchCommercialOpportunityActivities: vi.fn(),
  fetchCommercialOpportunityOperatorState: vi.fn(),
  fetchCommercialOpportunityTasks: vi.fn(),
  promoteSalesOpportunity: vi.fn(),
}));

const OPPORTUNITY_ID = "o_" + "a".repeat(32);

const DETAIL = {
  meta: { data_source: "sqlite_pr3" as const, read_only: true },
  opportunity: {
    opportunity_id: OPPORTUNITY_ID,
    record_kind: "explicit_opportunity",
    account_id: "a_1",
    primary_contact_id: "c_1",
    contact_display_email: "buyer@example.cl",
    account_display_domain: "example.cl",
    source_kind: "email",
    source_key: "email:1",
    deal_key: null,
    canonical_stage: "quote_requested",
    source_stage: "quote_requested",
    stage_reason_code: "quote_requested",
    stage_confidence: "derived_high_confidence",
    stage_is_current: true,
    stage_is_terminal: false,
    stage_evidence_at: null,
    stage_evidence_id: null,
    first_activity_at: null,
    last_activity_at: null,
    identity_link_status: "linked",
    review_status: "needs_review",
    synced_at: null,
  },
  events: [],
  evidence: [],
  conflicts: [],
};

describe("CommercialOpportunityDetailDrawer — promotion", () => {
  beforeEach(() => {
    vi.mocked(fetchCommercialOpportunityDetail).mockReset().mockResolvedValue(DETAIL);
    vi.mocked(fetchCommercialOpportunityOperatorState).mockReset().mockResolvedValue({ state: null });
    vi.mocked(fetchCommercialOpportunityActivities).mockReset().mockResolvedValue({ items: [] });
    vi.mocked(fetchCommercialOpportunityTasks).mockReset().mockResolvedValue({ items: [] });
    vi.mocked(promoteSalesOpportunity).mockReset();
  });

  it("shows a promote form when not yet promoted", async () => {
    render(
      <CommercialOpportunityDetailDrawer
        opportunityId={OPPORTUNITY_ID}
        open
        onClose={vi.fn()}
        onSelectContact={vi.fn()}
        promotedSalesOpportunityId={null}
        onOpenPipeline={vi.fn()}
        onPromoted={vi.fn()}
      />,
    );

    await waitFor(() => expect(screen.getByRole("button", { name: "Promover a CRM" })).toBeInTheDocument());
    expect(screen.queryByText("Abrir en Pipeline")).not.toBeInTheDocument();
  });

  it("shows Abrir en Pipeline when already promoted", async () => {
    render(
      <CommercialOpportunityDetailDrawer
        opportunityId={OPPORTUNITY_ID}
        open
        onClose={vi.fn()}
        onSelectContact={vi.fn()}
        promotedSalesOpportunityId="sales_1"
        onOpenPipeline={vi.fn()}
        onPromoted={vi.fn()}
      />,
    );

    await waitFor(() => expect(screen.getByText("Abrir en Pipeline")).toBeInTheDocument());
    expect(screen.queryByLabelText("Título de la oportunidad")).not.toBeInTheDocument();
  });

  it("promotes with the self-assign default (no owner_key sent) and switches to the promoted state", async () => {
    vi.mocked(promoteSalesOpportunity).mockResolvedValue({
      sales_opportunity_id: "sales_1",
      source_kind: "pr3",
      source_opportunity_id: OPPORTUNITY_ID,
      account_id: null,
      primary_contact_id: null,
      organization_id: null,
      primary_crm_contact_id: null,
      title: "Oportunidad — example.cl",
      stage: "new",
      owner_key: "tatiana@origenlab.cl",
      version: 1,
      created_by: "tatiana@origenlab.cl",
      updated_by: "tatiana@origenlab.cl",
      created_at: "2026-08-28T12:00:00+00:00",
      updated_at: "2026-08-28T12:00:00+00:00",
    });

    const onPromoted = vi.fn();

    render(
      <CommercialOpportunityDetailDrawer
        opportunityId={OPPORTUNITY_ID}
        open
        onClose={vi.fn()}
        onSelectContact={vi.fn()}
        promotedSalesOpportunityId={null}
        onOpenPipeline={vi.fn()}
        onPromoted={onPromoted}
      />,
    );

    await waitFor(() => expect(screen.getByRole("button", { name: "Promover a CRM" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Promover a CRM" }));

    await waitFor(() =>
      expect(promoteSalesOpportunity).toHaveBeenCalledWith(
        expect.objectContaining({ source_opportunity_id: OPPORTUNITY_ID, owner_key: undefined }),
        expect.any(String),
      ),
    );
    await waitFor(() => expect(screen.getByText("Abrir en Pipeline")).toBeInTheDocument());
    expect(onPromoted).toHaveBeenCalledWith(OPPORTUNITY_ID, "sales_1");
  });

  it("shows a clean message on a duplicate-promotion 409", async () => {
    vi.mocked(promoteSalesOpportunity).mockRejectedValue(new OperatorApiError("already promoted", 409));

    render(
      <CommercialOpportunityDetailDrawer
        opportunityId={OPPORTUNITY_ID}
        open
        onClose={vi.fn()}
        onSelectContact={vi.fn()}
        promotedSalesOpportunityId={null}
        onOpenPipeline={vi.fn()}
        onPromoted={vi.fn()}
      />,
    );

    await waitFor(() => expect(screen.getByRole("button", { name: "Promover a CRM" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Promover a CRM" }));

    await waitFor(() =>
      expect(screen.getByText(/ya fue promovida al pipeline/)).toBeInTheDocument(),
    );
  });
});
