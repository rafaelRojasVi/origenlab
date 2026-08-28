import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  fetchCommercialOpportunities,
  fetchCommercialOpportunityDetail,
} from "../../api/operatorClient";
import {
  fetchCommercialOpportunityActivities,
  fetchCommercialOpportunityOperatorState,
  fetchCommercialOpportunityTasks,
  fetchSalesOpportunities,
  promoteSalesOpportunity,
} from "../../api/commercialOperationsClient";
import { CommercialOpportunitiesCockpit } from "./CommercialOpportunitiesCockpit";

vi.mock("../../api/operatorClient", () => ({
  fetchCommercialOpportunities: vi.fn(),
  fetchCommercialOpportunityDetail: vi.fn(),
}));

vi.mock("../../api/commercialOperationsClient", () => ({
  fetchCommercialOpportunityActivities: vi.fn(),
  fetchCommercialOpportunityOperatorState: vi.fn(),
  fetchCommercialOpportunityTasks: vi.fn(),
  cancelCommercialTask: vi.fn(),
  completeCommercialTask: vi.fn(),
  createCommercialActivity: vi.fn(),
  createCommercialTask: vi.fn(),
  setCommercialOpportunityOperatorState: vi.fn(),
  fetchSalesOpportunities: vi.fn(),
  promoteSalesOpportunity: vi.fn(),
}));

const listItem = {
  opportunity_id: "o_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
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
  stage_evidence_at: "2026-08-23T10:00:00+00:00",
  stage_evidence_id: "e_1",
  first_activity_at: "2026-08-22T10:00:00+00:00",
  last_activity_at: "2026-08-23T10:00:00+00:00",
  identity_link_status: "linked",
  review_status: "needs_review",
  synced_at: null,
};

describe("CommercialOpportunitiesCockpit", () => {
  beforeEach(() => {
    vi.mocked(fetchCommercialOpportunities).mockReset();
    vi.mocked(fetchCommercialOpportunityDetail).mockReset();
    vi.mocked(fetchCommercialOpportunityOperatorState).mockReset();
    vi.mocked(fetchCommercialOpportunityActivities).mockReset();
    vi.mocked(fetchCommercialOpportunityTasks).mockReset();
    vi.mocked(fetchSalesOpportunities).mockReset().mockResolvedValue({
      meta: { data_source: "postgres", read_only: true, count: 0, total_count: 0, limit: 200, offset: 0 },
      items: [],
    });
    vi.mocked(promoteSalesOpportunity).mockReset();

    vi.mocked(fetchCommercialOpportunityOperatorState).mockResolvedValue({
      state: null,
    });

    vi.mocked(fetchCommercialOpportunityActivities).mockResolvedValue({
      items: [],
    });

    vi.mocked(fetchCommercialOpportunityTasks).mockResolvedValue({
      items: [],
    });

    vi.mocked(fetchCommercialOpportunities).mockResolvedValue({
      meta: {
        data_source: "sqlite_pr3",
        read_only: true,
        count: 1,
        total_count: 9577,
        limit: 25,
        offset: 0,
        reduced_mode: false,
        note: "",
      },
      items: [listItem],
    });

    vi.mocked(fetchCommercialOpportunityDetail).mockResolvedValue({
      meta: {
        data_source: "sqlite_pr3",
        read_only: true,
      },
      opportunity: listItem,
      events: [
        {
          event_id: "evt_1",
          opportunity_id: "o_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
          canonical_event_type: "client_quote_sent",
          source_event_type: "client_quote_sent",
          event_at: "2026-08-23T10:00:00+00:00",
          source_table: "commercial_deal_event",
          source_record_id: "1",
          source_email_id: 701,
          source_attachment_id: 33,
          confidence: "operator_confirmed",
          operator_confirmed: true,
          detail_json: {
            should_not_render: "SECRET_DETAIL_VALUE",
          },
          synced_at: null,
        },
      ],
      evidence: [],
      conflicts: [],
    });
  });

  it("renders PR3 lifecycle rows separately from deals", async () => {
    render(
      <CommercialOpportunitiesCockpit
        onSelectContact={vi.fn()}
        onOpenPipeline={vi.fn()}
      />,
    );

    expect(await screen.findByText("example.cl")).toBeTruthy();
    expect(
      screen.getByTitle("quote_requested").textContent,
    ).toBe("Cotización solicitada");
    expect(
      screen.getByTitle("needs_review").textContent,
    ).toBe("Requiere revisión");
    expect(screen.getByText("Etapa vigente")).toBeTruthy();
    expect(
      screen.getByText(/9\.577 oportunidad/),
    ).toBeTruthy();
  });

  it("uses server-side stage filters", async () => {
    render(
      <CommercialOpportunitiesCockpit
        onSelectContact={vi.fn()}
        onOpenPipeline={vi.fn()}
      />,
    );

    await screen.findByText("example.cl");

    fireEvent.change(screen.getByLabelText("Etapa"), {
      target: { value: "quote_sent" },
    });

    await waitFor(() => {
      expect(fetchCommercialOpportunities).toHaveBeenLastCalledWith(
        expect.objectContaining({
          canonical_stage: "quote_sent",
          offset: 0,
        }),
      );
    });
  });

  it("opens lifecycle detail without rendering raw detail_json", async () => {
    render(
      <CommercialOpportunitiesCockpit
        onSelectContact={vi.fn()}
        onOpenPipeline={vi.fn()}
      />,
    );

    await screen.findByText("example.cl");

    fireEvent.click(screen.getByRole("button", { name: "Ver ciclo" }));

    expect(
      await screen.findByTestId(
        "commercial-opportunity-detail-drawer",
      ),
    ).toBeTruthy();

    expect(await screen.findByText("Client Quote Sent")).toBeTruthy();
    expect(screen.getAllByText("Etapa vigente").length).toBeGreaterThan(0);
    expect(screen.getByText("e_1")).toBeTruthy();
    expect(screen.getByText(/correo #701/)).toBeTruthy();
    expect(screen.getByText(/adjunto #33/)).toBeTruthy();

    expect(
      screen.queryByText("SECRET_DETAIL_VALUE"),
    ).toBeNull();
  });

  it("changes server page size without an all option", async () => {
    render(
      <CommercialOpportunitiesCockpit
        onSelectContact={vi.fn()}
        onOpenPipeline={vi.fn()}
      />,
    );

    await screen.findByText("example.cl");

    const pageSize = screen.getByLabelText("Filas por página");

    expect(
      screen.queryByRole("option", { name: "Todos" }),
    ).toBeNull();

    fireEvent.change(pageSize, {
      target: { value: "50" },
    });

    await waitFor(() => {
      expect(fetchCommercialOpportunities).toHaveBeenLastCalledWith(
        expect.objectContaining({
          limit: 50,
          offset: 0,
        }),
      );
    });
  });

  it("opens existing contact drilldown callback", async () => {
    const onSelectContact = vi.fn();

    render(
      <CommercialOpportunitiesCockpit
        onSelectContact={onSelectContact}
        onOpenPipeline={vi.fn()}
      />,
    );

    const contact = await screen.findByRole("button", {
      name: "buyer@example.cl",
    });

    fireEvent.click(contact);

    expect(onSelectContact).toHaveBeenCalledWith(
      "buyer@example.cl",
    );
  });

  it("resolves promotion status for the current page in one request", async () => {
    vi.mocked(fetchSalesOpportunities).mockResolvedValue({
      meta: { data_source: "postgres", read_only: true, count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [
        {
          sales_opportunity_id: "sales_1",
          source_kind: "pr3",
          source_opportunity_id: listItem.opportunity_id,
          account_id: null,
          primary_contact_id: null,
          organization_id: null,
          primary_crm_contact_id: null,
          title: "Centrífuga",
          stage: "new",
          owner_key: "tatiana@origenlab.cl",
          version: 1,
          created_by: "tatiana@origenlab.cl",
          updated_by: "tatiana@origenlab.cl",
          created_at: "2026-08-28T12:00:00+00:00",
          updated_at: "2026-08-28T12:00:00+00:00",
          stage_updated_at: "2026-08-28T12:00:00+00:00",
          contact_display_email: null,
          account_display_domain: null,
          open_task_count: 0,
          next_task_id: null,
          next_task_title: null,
          next_task_due_at: null,
        },
      ],
    });

    render(<CommercialOpportunitiesCockpit onSelectContact={vi.fn()} onOpenPipeline={vi.fn()} />);

    await waitFor(() =>
      expect(vi.mocked(fetchSalesOpportunities)).toHaveBeenCalledWith({
        sourceOpportunityId: [listItem.opportunity_id],
      }),
    );
    await waitFor(() => expect(screen.getByText("En pipeline")).toBeInTheDocument());
  });

  it("reflects a successful in-drawer promotion immediately, without an extra fetch", async () => {
    vi.mocked(promoteSalesOpportunity).mockResolvedValue({
      sales_opportunity_id: "sales_99",
      source_kind: "pr3",
      source_opportunity_id: listItem.opportunity_id,
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

    render(<CommercialOpportunitiesCockpit onSelectContact={vi.fn()} onOpenPipeline={vi.fn()} />);

    await screen.findByText("example.cl");
    await waitFor(() => expect(fetchSalesOpportunities).toHaveBeenCalledTimes(1));
    expect(screen.queryByText("En pipeline")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Ver ciclo" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Promover a CRM" })).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByRole("button", { name: "Promover a CRM" }));
    await waitFor(() => expect(screen.getByText("Abrir en Pipeline")).toBeInTheDocument());

    // Close the drawer — the table row's badge must already reflect the promotion,
    // and reopening the SAME row must show "Abrir en Pipeline" straight away, with
    // no second fetchSalesOpportunities call triggered by either action.
    fireEvent.click(screen.getByRole("button", { name: "Cerrar" }));

    await waitFor(() => expect(screen.getByText("En pipeline")).toBeInTheDocument());
    expect(fetchSalesOpportunities).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "Ver ciclo" }));
    await waitFor(() => expect(screen.getByText("Abrir en Pipeline")).toBeInTheDocument());
    expect(screen.queryByLabelText("Título de la oportunidad")).not.toBeInTheDocument();
    expect(fetchSalesOpportunities).toHaveBeenCalledTimes(1);
  });
});
