import "@testing-library/jest-dom";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { NuevaCotizacionDialog } from "./NuevaCotizacionDialog";
import * as opsClient from "../../api/commercialOperationsClient";
import * as quoteClient from "../../api/customerQuoteClient";

vi.mock("../../api/commercialOperationsClient");
vi.mock("../../api/customerQuoteClient");

function mockNoExistingOpportunities() {
  vi.mocked(opsClient.fetchSalesOpportunities).mockResolvedValue({
    meta: { data_source: "postgres", read_only: true, count: 0, total_count: 0, limit: 200, offset: 0 },
    items: [],
  });
}

/**
 * The picker's plain `fetchSalesOpportunities({limit,offset})` call (no
 * filter) returns an empty existing-opportunity list; the dialog's
 * post-manual-create resolve call (filtered by `sourceOpportunityId`)
 * returns the given durable record. Mirrors the two distinct call shapes
 * the component actually makes.
 */
function mockCreatedOpportunityResolvesTo(item: ReturnType<typeof existingOpportunity>) {
  vi.mocked(opsClient.fetchSalesOpportunities).mockImplementation(async (params) => {
    if (params?.sourceOpportunityId?.length) {
      return {
        meta: { data_source: "postgres", read_only: true, count: 1, total_count: 1, limit: 1, offset: 0 },
        items: [item],
      };
    }
    return {
      meta: { data_source: "postgres", read_only: true, count: 0, total_count: 0, limit: 200, offset: 0 },
      items: [],
    };
  });
}

function existingOpportunity(overrides: Record<string, unknown> = {}) {
  return {
    sales_opportunity_id: "sales_" + "c".repeat(32),
    source_kind: "manual" as const,
    source_opportunity_id: "sales_" + "c".repeat(32),
    account_id: null,
    primary_contact_id: null,
    organization_id: null,
    primary_crm_contact_id: null,
    title: "Reactor CEAF",
    stage: "quoting" as const,
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
    ...overrides,
  };
}

function realQuote(overrides: Record<string, unknown> = {}) {
  return {
    quote_id: "quote_" + "e".repeat(32),
    sales_opportunity_id: "sales_" + "d".repeat(32),
    quote_number: "01184-26",
    document_number: "CN01184",
    sales_opportunity_title: "Balanza analítica",
    status: "draft" as const,
    version: 1,
    latest_revision_number: 1,
    created_by: "op@origenlab.cl",
    updated_by: "op@origenlab.cl",
    created_at: "2026-09-01T10:00:05Z",
    updated_at: "2026-09-01T10:00:05Z",
    drive_workspace: {
      provider: "google_drive" as const,
      provisioning_status: "pending" as const,
      folder_id: null,
      folder_web_url: null,
      sheet_file_id: null,
      sheet_web_url: null,
      failure_category: null,
      attempt_count: 1,
      version: 1,
      retryable: false,
      lease_expires_at: null,
      requested_at: "2026-09-01T10:00:05Z",
      completed_at: null,
    },
    ...overrides,
  };
}

describe("NuevaCotizacionDialog — numbering invariants", () => {
  beforeEach(() => {
    vi.mocked(opsClient.fetchSalesOpportunities).mockReset();
    vi.mocked(opsClient.createManualSalesOpportunity).mockReset();
    vi.mocked(quoteClient.createCustomerQuote).mockReset();
    mockNoExistingOpportunities();
  });

  it("opening the dialog allocates nothing", async () => {
    render(<NuevaCotizacionDialog open onClose={vi.fn()} onCreated={vi.fn()} />);
    await waitFor(() => expect(opsClient.fetchSalesOpportunities).toHaveBeenCalled());
    expect(opsClient.createManualSalesOpportunity).not.toHaveBeenCalled();
    expect(quoteClient.createCustomerQuote).not.toHaveBeenCalled();
  });

  it("selecting an existing opportunity allocates nothing", async () => {
    vi.mocked(opsClient.fetchSalesOpportunities).mockResolvedValue({
      meta: { data_source: "postgres", read_only: true, count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [existingOpportunity()],
    });

    render(<NuevaCotizacionDialog open onClose={vi.fn()} onCreated={vi.fn()} />);
    await waitFor(() => screen.getByText("Reactor CEAF"));

    fireEvent.click(screen.getByRole("button", { name: /Reactor CEAF/ }));

    expect(quoteClient.createCustomerQuote).not.toHaveBeenCalled();
  });

  it("filling the manual-opportunity form without submitting allocates nothing", async () => {
    render(<NuevaCotizacionDialog open onClose={vi.fn()} onCreated={vi.fn()} />);
    await waitFor(() => expect(opsClient.fetchSalesOpportunities).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("tab", { name: "Oportunidad nueva" }));
    fireEvent.change(screen.getByLabelText("Título"), { target: { value: "Balanza analítica" } });
    fireEvent.change(screen.getByLabelText("Organización"), { target: { value: "IKA" } });

    expect(opsClient.createManualSalesOpportunity).not.toHaveBeenCalled();
    expect(quoteClient.createCustomerQuote).not.toHaveBeenCalled();
  });

  it("cancelling allocates nothing", async () => {
    const onClose = vi.fn();
    render(<NuevaCotizacionDialog open onClose={onClose} onCreated={vi.fn()} />);
    await waitFor(() => expect(opsClient.fetchSalesOpportunities).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("tab", { name: "Oportunidad nueva" }));
    fireEvent.change(screen.getByLabelText("Título"), { target: { value: "Balanza analítica" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));

    expect(onClose).toHaveBeenCalled();
    expect(opsClient.createManualSalesOpportunity).not.toHaveBeenCalled();
    expect(quoteClient.createCustomerQuote).not.toHaveBeenCalled();
  });

  it("a validation failure (empty title) never calls createCustomerQuote", async () => {
    render(<NuevaCotizacionDialog open onClose={vi.fn()} onCreated={vi.fn()} />);
    await waitFor(() => expect(opsClient.fetchSalesOpportunities).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("tab", { name: "Oportunidad nueva" }));
    expect(screen.getByRole("button", { name: /Crear/ })).toBeDisabled();
    expect(quoteClient.createCustomerQuote).not.toHaveBeenCalled();
  });

  it("the manual-intake fields are labeled accurately: they DO create a durable organization/contact, matching what the server actually does", async () => {
    render(<NuevaCotizacionDialog open onClose={vi.fn()} onCreated={vi.fn()} />);
    await waitFor(() => expect(opsClient.fetchSalesOpportunities).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("tab", { name: "Oportunidad nueva" }));
    // Manual intake resolves/creates real commercial.organization (and
    // commercial.contact) rows server-side — see
    // commercial_operations.py's _resolve_manual_organization /
    // _resolve_manual_contact. The copy must not claim otherwise.
    expect(screen.queryByText(/no crean un cliente/i)).toBeNull();
    expect(screen.queryByText(/no crean un contacto/i)).toBeNull();
    screen.getByText(/quedan registrados en el crm durable/i);
  });

  it("a successful manual-opportunity submit calls createManualSalesOpportunity then createCustomerQuote exactly once each, with idempotency keys, and hands back the real created quote with no placeholder", async () => {
    const opportunityId = "sales_" + "d".repeat(32);
    vi.mocked(opsClient.createManualSalesOpportunity).mockResolvedValue({
      sales_opportunity_id: opportunityId,
      source_kind: "manual",
      source_opportunity_id: opportunityId,
      account_id: null,
      primary_contact_id: null,
      organization_id: null,
      primary_crm_contact_id: null,
      title: "Balanza analítica",
      stage: "new",
      owner_key: "op@origenlab.cl",
      version: 1,
      created_by: "op@origenlab.cl",
      updated_by: "op@origenlab.cl",
      created_at: "2026-09-01T10:00:00Z",
      updated_at: "2026-09-01T10:00:00Z",
    });
    mockCreatedOpportunityResolvesTo(
      existingOpportunity({
        sales_opportunity_id: opportunityId,
        source_opportunity_id: opportunityId,
        title: "Balanza analítica",
        stage: "new",
        organization_display_name: "IKA",
        contact_display_name: null,
        contact_primary_email: null,
      }),
    );
    const created = realQuote({ sales_opportunity_id: opportunityId });
    vi.mocked(quoteClient.createCustomerQuote).mockResolvedValue(created);
    const onCreated = vi.fn();

    render(<NuevaCotizacionDialog open onClose={vi.fn()} onCreated={onCreated} />);
    await waitFor(() => expect(opsClient.fetchSalesOpportunities).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("tab", { name: "Oportunidad nueva" }));
    fireEvent.change(screen.getByLabelText("Título"), { target: { value: "Balanza analítica" } });
    fireEvent.change(screen.getByLabelText("Organización"), { target: { value: "IKA" } });
    fireEvent.click(screen.getByRole("button", { name: /Crear/ }));

    await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));

    expect(opsClient.createManualSalesOpportunity).toHaveBeenCalledTimes(1);
    expect(opsClient.createManualSalesOpportunity).toHaveBeenCalledWith(
      { title: "Balanza analítica", organization_display_name: "IKA" },
      expect.stringMatching(/^opportunity:/),
    );
    expect(opsClient.fetchSalesOpportunities).toHaveBeenCalledWith(
      expect.objectContaining({ sourceOpportunityId: [opportunityId] }),
    );
    expect(quoteClient.createCustomerQuote).toHaveBeenCalledTimes(1);
    expect(quoteClient.createCustomerQuote).toHaveBeenCalledWith(opportunityId, expect.stringMatching(/^quote:/));

    const passedItem = onCreated.mock.calls[0][0];
    expect(passedItem.quote).toEqual(created);
    expect(passedItem.organization_display_name).toBe("IKA");
    expect(passedItem.sales_opportunity_stage).toBe("new");
    expect(passedItem.sales_opportunity_owner_key).toBe("op@origenlab.cl");
  });

  it("renders the server-returned durable opportunity, not the submitted form values, when they diverge (server normalization)", async () => {
    const opportunityId = "sales_" + "d".repeat(32);
    vi.mocked(opsClient.createManualSalesOpportunity).mockResolvedValue({
      sales_opportunity_id: opportunityId,
      source_kind: "manual",
      source_opportunity_id: opportunityId,
      account_id: null,
      primary_contact_id: null,
      organization_id: "org_" + "f".repeat(32),
      primary_crm_contact_id: null,
      title: "Balanza analítica",
      stage: "new",
      owner_key: "op@origenlab.cl",
      version: 1,
      created_by: "op@origenlab.cl",
      updated_by: "op@origenlab.cl",
      created_at: "2026-09-01T10:00:00Z",
      updated_at: "2026-09-01T10:00:00Z",
    });
    // The server-normalized organization name differs from what the
    // operator typed (e.g. dedup onto an existing canonical org, or
    // display-name normalization) — the durable record is what must win.
    mockCreatedOpportunityResolvesTo(
      existingOpportunity({
        sales_opportunity_id: opportunityId,
        source_opportunity_id: opportunityId,
        title: "Balanza analítica",
        stage: "new",
        organization_display_name: "IKA Chile S.A. (normalizado)",
        contact_display_name: null,
        contact_primary_email: null,
      }),
    );
    const created = realQuote({ sales_opportunity_id: opportunityId });
    vi.mocked(quoteClient.createCustomerQuote).mockResolvedValue(created);
    const onCreated = vi.fn();

    render(<NuevaCotizacionDialog open onClose={vi.fn()} onCreated={onCreated} />);
    await waitFor(() => expect(opsClient.fetchSalesOpportunities).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("tab", { name: "Oportunidad nueva" }));
    fireEvent.change(screen.getByLabelText("Título"), { target: { value: "Balanza analítica" } });
    // Operator types "IKA" — the server's durable record says something else.
    fireEvent.change(screen.getByLabelText("Organización"), { target: { value: "IKA" } });
    fireEvent.click(screen.getByRole("button", { name: /Crear/ }));

    await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));

    const passedItem = onCreated.mock.calls[0][0];
    expect(passedItem.organization_display_name).toBe("IKA Chile S.A. (normalizado)");
    expect(passedItem.organization_display_name).not.toBe("IKA");
  });

  it("a successful existing-opportunity submit calls createCustomerQuote exactly once, never calls createManualSalesOpportunity, and passes the real selected identity through", async () => {
    const opportunityId = "sales_" + "c".repeat(32);
    vi.mocked(opsClient.fetchSalesOpportunities).mockResolvedValue({
      meta: { data_source: "postgres", read_only: true, count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [existingOpportunity({ sales_opportunity_id: opportunityId, source_opportunity_id: opportunityId })],
    });
    const created = realQuote({ sales_opportunity_id: opportunityId, quote_number: "01185-26" });
    vi.mocked(quoteClient.createCustomerQuote).mockResolvedValue(created);
    const onCreated = vi.fn();

    render(<NuevaCotizacionDialog open onClose={vi.fn()} onCreated={onCreated} />);
    await waitFor(() => screen.getByText("Reactor CEAF"));

    fireEvent.click(screen.getByRole("button", { name: /Reactor CEAF/ }));
    fireEvent.click(screen.getByRole("button", { name: /Crear cotización/ }));

    await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));
    expect(opsClient.createManualSalesOpportunity).not.toHaveBeenCalled();
    expect(quoteClient.createCustomerQuote).toHaveBeenCalledWith(opportunityId, expect.stringMatching(/^quote:/));

    const passedItem = onCreated.mock.calls[0][0];
    expect(passedItem.quote).toEqual(created);
    expect(passedItem.organization_display_name).toBe("CEAF");
    expect(passedItem.contact_display_name).toBe("Tatiana Rojas");
    expect(passedItem.sales_opportunity_stage).toBe("quoting");
  });

  it("manual opportunity success + quote failure retries only quote creation, never re-creating the opportunity", async () => {
    const opportunityId = "sales_" + "d".repeat(32);
    vi.mocked(opsClient.createManualSalesOpportunity).mockResolvedValue({
      sales_opportunity_id: opportunityId,
      source_kind: "manual",
      source_opportunity_id: opportunityId,
      account_id: null,
      primary_contact_id: null,
      organization_id: null,
      primary_crm_contact_id: null,
      title: "Balanza analítica",
      stage: "new",
      owner_key: "op@origenlab.cl",
      version: 1,
      created_by: "op@origenlab.cl",
      updated_by: "op@origenlab.cl",
      created_at: "2026-09-01T10:00:00Z",
      updated_at: "2026-09-01T10:00:00Z",
    });
    mockCreatedOpportunityResolvesTo(
      existingOpportunity({
        sales_opportunity_id: opportunityId,
        source_opportunity_id: opportunityId,
        title: "Balanza analítica",
        stage: "new",
        organization_display_name: "IKA",
        contact_display_name: null,
        contact_primary_email: null,
      }),
    );
    const created = realQuote({ sales_opportunity_id: opportunityId });
    vi.mocked(quoteClient.createCustomerQuote)
      .mockRejectedValueOnce(new Error("network blip"))
      .mockResolvedValueOnce(created);
    const onCreated = vi.fn();

    render(<NuevaCotizacionDialog open onClose={vi.fn()} onCreated={onCreated} />);
    await waitFor(() => expect(opsClient.fetchSalesOpportunities).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("tab", { name: "Oportunidad nueva" }));
    fireEvent.change(screen.getByLabelText("Título"), { target: { value: "Balanza analítica" } });
    fireEvent.change(screen.getByLabelText("Organización"), { target: { value: "IKA" } });

    fireEvent.click(screen.getByRole("button", { name: /Crear/ }));
    await waitFor(() => expect(quoteClient.createCustomerQuote).toHaveBeenCalledTimes(1));
    await screen.findByRole("alert");

    fireEvent.click(screen.getByRole("button", { name: /Crear/ }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));

    expect(opsClient.createManualSalesOpportunity).toHaveBeenCalledTimes(1);
    // The identity re-fetch (filtered by sourceOpportunityId) is cached too
    // — a retry of only the quote step must not re-fetch it either.
    const identityFetchCalls = vi
      .mocked(opsClient.fetchSalesOpportunities)
      .mock.calls.filter((call) => call[0]?.sourceOpportunityId?.length);
    expect(identityFetchCalls).toHaveLength(1);
    expect(quoteClient.createCustomerQuote).toHaveBeenCalledTimes(2);
    const [firstCallOpportunityId, firstKey] = vi.mocked(quoteClient.createCustomerQuote).mock.calls[0];
    const [secondCallOpportunityId, secondKey] = vi.mocked(quoteClient.createCustomerQuote).mock.calls[1];
    expect(firstCallOpportunityId).toBe(opportunityId);
    expect(secondCallOpportunityId).toBe(opportunityId);
    expect(secondKey).toBe(firstKey);
  });

  it("disables the submit button and ignores a second click while a submit is in flight", async () => {
    const opportunityId = "sales_" + "c".repeat(32);
    vi.mocked(opsClient.fetchSalesOpportunities).mockResolvedValue({
      meta: { data_source: "postgres", read_only: true, count: 1, total_count: 1, limit: 200, offset: 0 },
      items: [existingOpportunity({ sales_opportunity_id: opportunityId, source_opportunity_id: opportunityId })],
    });
    let resolveCreate: (value: Awaited<ReturnType<typeof quoteClient.createCustomerQuote>>) => void =
      () => {};
    vi.mocked(quoteClient.createCustomerQuote).mockReturnValue(
      new Promise((resolve) => {
        resolveCreate = resolve;
      }),
    );

    render(<NuevaCotizacionDialog open onClose={vi.fn()} onCreated={vi.fn()} />);
    await waitFor(() => screen.getByText("Reactor CEAF"));
    fireEvent.click(screen.getByRole("button", { name: /Reactor CEAF/ }));

    const submitButton = screen.getByRole("button", { name: /Crear cotización/ });
    fireEvent.click(submitButton);
    expect(submitButton).toBeDisabled();
    fireEvent.click(submitButton);

    resolveCreate(realQuote({ sales_opportunity_id: opportunityId }));
    await waitFor(() => expect(quoteClient.createCustomerQuote).toHaveBeenCalledTimes(1));
  });

  it("keeps the same idempotency key across a failed submit and its retry", async () => {
    vi.mocked(opsClient.createManualSalesOpportunity).mockResolvedValue({
      sales_opportunity_id: "sales_" + "d".repeat(32),
      source_kind: "manual",
      source_opportunity_id: "sales_" + "d".repeat(32),
      account_id: null,
      primary_contact_id: null,
      organization_id: null,
      primary_crm_contact_id: null,
      title: "Balanza analítica",
      stage: "new",
      owner_key: "op@origenlab.cl",
      version: 1,
      created_by: "op@origenlab.cl",
      updated_by: "op@origenlab.cl",
      created_at: "2026-09-01T10:00:00Z",
      updated_at: "2026-09-01T10:00:00Z",
    });
    mockCreatedOpportunityResolvesTo(
      existingOpportunity({
        sales_opportunity_id: "sales_" + "d".repeat(32),
        source_opportunity_id: "sales_" + "d".repeat(32),
        title: "Balanza analítica",
        stage: "new",
        organization_display_name: "IKA",
        contact_display_name: null,
        contact_primary_email: null,
      }),
    );
    vi.mocked(quoteClient.createCustomerQuote)
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce(realQuote());
    render(<NuevaCotizacionDialog open onClose={vi.fn()} onCreated={vi.fn()} />);
    await waitFor(() => expect(opsClient.fetchSalesOpportunities).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("tab", { name: "Oportunidad nueva" }));
    fireEvent.change(screen.getByLabelText("Título"), { target: { value: "Balanza analítica" } });
    fireEvent.change(screen.getByLabelText("Organización"), { target: { value: "IKA" } });
    fireEvent.click(screen.getByRole("button", { name: /Crear/ }));
    await waitFor(() => expect(quoteClient.createCustomerQuote).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("button", { name: /Crear/ }));
    await waitFor(() => expect(quoteClient.createCustomerQuote).toHaveBeenCalledTimes(2));

    const firstKey = vi.mocked(quoteClient.createCustomerQuote).mock.calls[0][1];
    const secondKey = vi.mocked(quoteClient.createCustomerQuote).mock.calls[1][1];
    expect(secondKey).toBe(firstKey);
  });

  it("resets form state and still submits cleanly after a cancel + reopen on the same dialog instance", async () => {
    vi.mocked(opsClient.createManualSalesOpportunity).mockResolvedValue({
      sales_opportunity_id: "sales_" + "d".repeat(32),
      source_kind: "manual",
      source_opportunity_id: "sales_" + "d".repeat(32),
      account_id: null,
      primary_contact_id: null,
      organization_id: null,
      primary_crm_contact_id: null,
      title: "Balanza analítica",
      stage: "new",
      owner_key: "op@origenlab.cl",
      version: 1,
      created_by: "op@origenlab.cl",
      updated_by: "op@origenlab.cl",
      created_at: "2026-09-01T10:00:00Z",
      updated_at: "2026-09-01T10:00:00Z",
    });
    mockCreatedOpportunityResolvesTo(
      existingOpportunity({
        sales_opportunity_id: "sales_" + "d".repeat(32),
        source_opportunity_id: "sales_" + "d".repeat(32),
        title: "Otra",
        stage: "new",
        organization_display_name: "IKA",
        contact_display_name: null,
        contact_primary_email: null,
      }),
    );
    vi.mocked(quoteClient.createCustomerQuote).mockResolvedValue(realQuote());
    const onClose = vi.fn();
    const { rerender } = render(<NuevaCotizacionDialog open onClose={onClose} onCreated={vi.fn()} />);
    await waitFor(() => expect(opsClient.fetchSalesOpportunities).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("tab", { name: "Oportunidad nueva" }));
    fireEvent.change(screen.getByLabelText("Título"), { target: { value: "Balanza analítica" } });
    fireEvent.change(screen.getByLabelText("Organización"), { target: { value: "IKA" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(onClose).toHaveBeenCalled();

    rerender(<NuevaCotizacionDialog open={false} onClose={onClose} onCreated={vi.fn()} />);
    rerender(<NuevaCotizacionDialog open onClose={onClose} onCreated={vi.fn()} />);
    await waitFor(() => expect(opsClient.fetchSalesOpportunities).toHaveBeenCalledTimes(2));

    fireEvent.click(screen.getByRole("tab", { name: "Oportunidad nueva" }));
    fireEvent.change(screen.getByLabelText("Título"), { target: { value: "Otra" } });
    fireEvent.change(screen.getByLabelText("Organización"), { target: { value: "IKA" } });
    fireEvent.click(screen.getByRole("button", { name: /Crear/ }));
    await waitFor(() => expect(quoteClient.createCustomerQuote).toHaveBeenCalledTimes(1));
  });
});
