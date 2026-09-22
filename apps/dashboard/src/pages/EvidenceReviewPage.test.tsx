import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EvidenceReviewPage } from "./EvidenceReviewPage";

vi.mock("../api/v2Client", () => ({
  fetchV2Contacts: vi.fn(),
  fetchV2Organizations: vi.fn(),
  fetchV2Prospects: vi.fn(),
  fetchV2EvidenceRecords: vi.fn(),
}));

import {
  fetchV2Contacts,
  fetchV2EvidenceRecords,
  fetchV2Organizations,
  fetchV2Prospects,
} from "../api/v2Client";

const page = <T,>(items: T[], total = items.length) => ({
  items,
  total,
  limit: 50,
  offset: 0,
});

const KNOWN_ADDRESS = {
  source_record_id: "aaaaaaaa-1111-4111-8111-111111111111",
  source_kind: "gmail_message",
  dedupe_key: "gmail_message:m1",
  source_uri: "gmail://msg/m1",
  acquired_at: "2026-09-21T20:05:00Z",
  review_status: "pending",
  is_quarantined: false,
  subject: "Cotización insumos osmómetro",
  from_address: "diego.soto@farmadelta.example.cl",
  from_domain: "farmadelta.example.cl",
  message_date: "2026-09-21T12:08:33Z",
  thread_id: "t1",
  assertion_total: 1,
  assertions: [
    {
      assertion_id: "as1",
      kind: "contact_address",
      value_norm: "diego.soto@farmadelta.example.cl",
      resolution: "unresolved",
      resolved_kind: null,
      resolved_id: null,
      ambiguity_note: null,
    },
  ],
  contact_matches: [
    {
      value_norm: "diego.soto@farmadelta.example.cl",
      contact_point_id: "c56dabff-fc17-5a6b-a2f4-87a3826510cb",
      usage: "unattributed" as const,
      confirmation: "machine_proposed" as const,
      person_id: null,
      person_display_name: null,
      organization_id: null,
      organization_name: null,
    },
  ],
  organization_matches: [],
  domain_organization: null,
};

const NAMED_ORGANIZATION = {
  ...KNOWN_ADDRESS,
  source_record_id: "bbbbbbbb-2222-4222-8222-222222222222",
  dedupe_key: "gmail_message:m2",
  source_uri: "gmail://msg/m2",
  subject: "Solicitud de cotización — Especial septiembre",
  from_address: "usuariaparticular9@gmail.com",
  from_domain: "gmail.com",
  assertion_total: 2,
  assertions: [
    {
      assertion_id: "as2",
      kind: "contact_address",
      value_norm: "usuariaparticular9@gmail.com",
      resolution: "unresolved",
      resolved_kind: null,
      resolved_id: null,
      ambiguity_note: null,
    },
    {
      assertion_id: "as3",
      kind: "organization_name",
      value_norm: "labsalud del sur",
      resolution: "unresolved",
      resolved_kind: null,
      resolved_id: null,
      ambiguity_note: null,
    },
  ],
  contact_matches: [],
  organization_matches: [],
};

beforeEach(() => {
  vi.mocked(fetchV2EvidenceRecords).mockResolvedValue(
    page([KNOWN_ADDRESS, NAMED_ORGANIZATION], 20) as never,
  );
  vi.mocked(fetchV2Contacts).mockResolvedValue(page([], 9460) as never);
  vi.mocked(fetchV2Organizations).mockResolvedValue(page([], 1812) as never);
  vi.mocked(fetchV2Prospects).mockResolvedValue(page([], 0) as never);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("EvidenceReviewPage", () => {
  it("asks only for pending records", async () => {
    render(<EvidenceReviewPage />);
    await waitFor(() => expect(fetchV2EvidenceRecords).toHaveBeenCalled());
    // Scoped to Gmail as well as to pending: the four bulk migration manifests are also
    // `pending` records, and they are an import artefact, not a message anyone reviews.
    expect(vi.mocked(fetchV2EvidenceRecords).mock.calls[0][0]).toMatchObject({
      sourceKind: "gmail_message",
      reviewStatus: "pending",
    });
  });

  it("draws the whole flow, including the steps that have no work in them", async () => {
    render(<EvidenceReviewPage />);
    const flow = await screen.findByTestId("review-flow");
    expect(flow.querySelectorAll("li")).toHaveLength(5);
    expect(flow.textContent).toContain("Evidencia");
    expect(flow.textContent).toContain("Marketing");
    expect(flow.textContent).toContain("Cotización");
  });

  it("counts from the server total, never from the rows on screen", async () => {
    render(<EvidenceReviewPage />);
    // Two rows are rendered and twenty are pending. Reporting 2 would understate the queue.
    expect(await screen.findByText("20")).toBeTruthy();
    expect(screen.getByText("9.460")).toBeTruthy();
    expect(screen.getByText("1.812")).toBeTruthy();
  });

  it("lists one row per record with its sender, subject and date", async () => {
    render(<EvidenceReviewPage />);
    await screen.findByTestId("review-queue-table");
    expect(screen.getAllByTestId("review-queue-row")).toHaveLength(2);
    expect(screen.getByText("diego.soto@farmadelta.example.cl")).toBeTruthy();
    expect(screen.getByText("Cotización insumos osmómetro")).toBeTruthy();
  });

  it("says an existing address is a known address, not a confirmed person", async () => {
    render(<EvidenceReviewPage />);
    await screen.findByTestId("review-queue-table");
    expect(screen.getByText("Dirección conocida, sin persona")).toBeTruthy();
    fireEvent.click(screen.getByText("diego.soto@farmadelta.example.cl"));
    expect(screen.getByText("Sin persona confirmada")).toBeTruthy();
    expect(screen.getByText("Sin institución atribuida")).toBeTruthy();
  });

  it("separates a domain hint from a confirmed organization", async () => {
    render(<EvidenceReviewPage />);
    await screen.findByTestId("review-queue-table");
    fireEvent.click(screen.getByText("diego.soto@farmadelta.example.cl"));
    expect(screen.getByText("Dominio sin registrar — pista, no evidencia")).toBeTruthy();
  });

  it("spells out what is unresolved for the open record", async () => {
    render(<EvidenceReviewPage />);
    await screen.findByTestId("review-queue-table");
    fireEvent.click(screen.getByText("usuariaparticular9@gmail.com"));
    const flags = screen.getAllByTestId("review-flag").map((node) => node.textContent ?? "");
    expect(flags.some((text) => text.includes("no existe todavía en el CRM durable"))).toBe(true);
    expect(flags.some((text) => text.includes("correo personal"))).toBe(true);
    expect(flags.some((text) => text.includes("labsalud del sur"))).toBe(true);
  });

  it("shows the provenance of every record it displays", async () => {
    render(<EvidenceReviewPage />);
    await screen.findByTestId("review-queue-table");
    fireEvent.click(screen.getByText("diego.soto@farmadelta.example.cl"));
    expect(screen.getByText(/gmail:\/\/msg\/m1/)).toBeTruthy();
  });

  it("offers every action as a disabled preview with its reason", async () => {
    render(<EvidenceReviewPage />);
    await screen.findByTestId("review-queue-table");
    fireEvent.click(screen.getByText("diego.soto@farmadelta.example.cl"));
    const actions = screen.getAllByTestId("review-preview-action");
    expect(actions.length).toBeGreaterThan(0);
    // Not one of them may be clickable. A live button here would be a second writer into
    // durable truth, through a proxy that allows no POST under /v2.
    for (const action of actions) {
      expect((action as HTMLButtonElement).disabled).toBe(true);
    }
  });

  it("marks marketing unavailable and says permission is what is missing", async () => {
    render(<EvidenceReviewPage />);
    const marketing = await screen.findByTestId("review-marketing-section");
    expect(marketing.textContent).toContain("No disponible");
    expect(marketing.textContent).toContain("permiso");
  });

  it("marks quotes unavailable until a reviewed commercial record exists", async () => {
    render(<EvidenceReviewPage />);
    const quotes = await screen.findByTestId("review-quotes-section");
    expect(quotes.textContent).toContain("No disponible");
    expect(quotes.textContent).toContain("revisados");
  });

  it("says so when a record carries more observations than it shows", async () => {
    vi.mocked(fetchV2EvidenceRecords).mockResolvedValue(
      page([{ ...KNOWN_ADDRESS, assertion_total: 11448 }], 1) as never,
    );
    render(<EvidenceReviewPage />);
    await screen.findByTestId("review-queue-table");
    fireEvent.click(screen.getByText("diego.soto@farmadelta.example.cl"));
    expect(screen.getByText(/Mostrando 1 de 11\.448 observaciones/)).toBeTruthy();
  });

  it("explains an empty queue instead of rendering a blank page", async () => {
    vi.mocked(fetchV2EvidenceRecords).mockResolvedValue(page([], 0) as never);
    render(<EvidenceReviewPage />);
    expect((await screen.findByTestId("v2-empty-state")).textContent).toContain(
      "Sin evidencia pendiente",
    );
  });

  it("surfaces a load failure rather than an empty queue", async () => {
    vi.mocked(fetchV2EvidenceRecords).mockRejectedValue(new Error("boom"));
    render(<EvidenceReviewPage />);
    expect(await screen.findByText(/Revisión de evidencia/)).toBeTruthy();
    await waitFor(() => expect(screen.queryByTestId("review-queue-table")).toBeNull());
  });
});
