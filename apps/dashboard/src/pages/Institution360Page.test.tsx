import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Institution360Page } from "./Institution360Page";

vi.mock("../api/v2Client", () => ({
  fetchV2Organizations: vi.fn(),
  fetchV2OrganizationCard: vi.fn(),
  fetchV2OrganizationCases: vi.fn(),
  fetchV2QuotesToFollowUp: vi.fn(),
}));

import {
  fetchV2OrganizationCard,
  fetchV2OrganizationCases,
  fetchV2Organizations,
  fetchV2QuotesToFollowUp,
} from "../api/v2Client";
import {
  addressControl,
  cardEvidence,
  connectedActivity,
  connectedInterest,
  connectedQuote,
  emptyConnectionSummary,
  listedOrganization,
  organizationCard,
  organizationCaseWithRoles,
} from "../lib/__fixtures__/crm360";

const ORG_ID = "11111111-2222-4333-8444-555555555555";

const page = <T,>(items: T[], total = items.length) => ({
  items,
  total,
  limit: 50,
  offset: 0,
});

const CHANNELS = [
  {
    contact_point_id: "aaaaaaaa-1111-4111-8111-111111111111",
    address: "maria@instituto.invalid",
    channel_kind: "email",
    usage: "work" as const,
    confirmation: "confirmed" as const,
    person_display_name: "María Ficticia",
  },
  {
    contact_point_id: "bbbbbbbb-2222-4222-8222-222222222222",
    address: "compras@instituto.invalid",
    channel_kind: "email",
    usage: "shared_mailbox" as const,
    confirmation: "machine_proposed" as const,
    person_display_name: null,
  },
];

beforeEach(() => {
  window.location.hash = "";
  vi.mocked(fetchV2Organizations).mockResolvedValue(
    page([listedOrganization()], 1812) as never,
  );
  vi.mocked(fetchV2OrganizationCard).mockResolvedValue(organizationCard());
  vi.mocked(fetchV2OrganizationCases).mockResolvedValue(page([]) as never);
  vi.mocked(fetchV2QuotesToFollowUp).mockResolvedValue(page([]) as never);
});

afterEach(() => {
  window.location.hash = "";
  vi.resetAllMocks();
});

describe("the institution list", () => {
  it("opens an institution as a deep link", async () => {
    render(<Institution360Page />);
    fireEvent.click(await screen.findByText("Instituto Ficticio de Metrología"));
    expect(window.location.hash).toBe(`#/instituciones?id=${ORG_ID}`);
    // Let the detail the hash opened mount inside this test, so its fetches never run
    // after the mocks are reset.
    await screen.findByTestId("institution-360-detail");
  });

  it("counts from the server total", async () => {
    render(<Institution360Page />);
    expect((await screen.findByTestId("institution-360-footer")).textContent).toContain("1.812");
  });

  it("shows every connection count and the last activity on the card", async () => {
    vi.mocked(fetchV2Organizations).mockResolvedValue(
      page([
        listedOrganization({
          confirmation: "confirmed",
          case_count: 3,
          open_case_count: 2,
          interest_count: 4,
          quote_count: 1,
          activity_count: 7,
          confirmed_people_count: 5,
          last_activity_at: "2026-09-16T10:00:00Z",
        }),
      ]) as never,
    );
    render(<Institution360Page />);
    const card = await screen.findByTestId("institution-card");
    expect(card.textContent).toContain("2 casos abiertos");
    expect(card.textContent).toContain("Confirmada");
    expect(card.textContent).toContain("Intereses4");
    expect(card.textContent).toContain("Cotiz.1");
    expect(card.textContent).toContain("Personas5");
    expect(card.textContent).not.toContain("no registrada todavía");
    // The avatar is the recorded name's initials, never a logo looked up by domain.
    expect(card.textContent).toContain("IF");
  });

  it("says a missing last activity is not recorded yet", async () => {
    render(<Institution360Page />);
    const card = await screen.findByTestId("institution-card");
    expect(card.textContent).toContain("no registrada todavía");
  });

  it("sends each chosen filter to the server, and clears them", async () => {
    render(<Institution360Page />);
    await screen.findByTestId("institution-card");
    fireEvent.click(screen.getByRole("button", { name: "Con casos abiertos" }));
    fireEvent.click(screen.getByRole("button", { name: "Con cotizaciones" }));
    await waitFor(() => {
      const calls = vi.mocked(fetchV2Organizations).mock.calls;
      expect(calls[calls.length - 1][0]).toMatchObject({ has: ["open_cases", "quotes"] });
    });
    fireEvent.change(screen.getByTestId("institution-activity-filter"), {
      target: { value: "30" },
    });
    await waitFor(() => {
      const calls = vi.mocked(fetchV2Organizations).mock.calls;
      expect(calls[calls.length - 1][0]).toMatchObject({ activeWithinDays: 30 });
    });
    fireEvent.click(screen.getByRole("button", { name: "Limpiar filtros" }));
    await waitFor(() => {
      const calls = vi.mocked(fetchV2Organizations).mock.calls;
      expect(calls[calls.length - 1][0]).toMatchObject({ has: [], activeWithinDays: undefined });
    });
  });

  it("says nothing recorded matches, not that nothing exists", async () => {
    render(<Institution360Page />);
    await screen.findByTestId("institution-card");
    vi.mocked(fetchV2Organizations).mockResolvedValue(page([]) as never);
    fireEvent.click(screen.getByRole("button", { name: "Con cotizaciones" }));
    expect(
      await screen.findByText(/Que no esté registrado no significa que no exista/),
    ).toBeTruthy();
  });
});

describe("the institution list by commercial role", () => {
  const withFacets = <T,>(items: T[]) => ({
    ...page(items),
    facets: { all: 1813, customers: 1, suppliers: 1, others: 0 },
  });

  it("opens on the institutions that ask OrigenLab for equipment", async () => {
    render(<Institution360Page />);
    await screen.findByTestId("institution-card");
    expect(vi.mocked(fetchV2Organizations).mock.calls[0][0]).toMatchObject({
      segment: "customers",
    });
    const tab = screen.getByRole("tab", { selected: true });
    expect(tab.textContent).toContain("Clientes / instituciones solicitantes");
  });

  it("lists suppliers and manufacturers on their own tab, and every organization under Todas", async () => {
    vi.mocked(fetchV2Organizations).mockResolvedValue(withFacets([listedOrganization()]) as never);
    render(<Institution360Page />);
    await screen.findByTestId("institution-card");
    fireEvent.click(screen.getByRole("tab", { name: /Proveedores y fabricantes/ }));
    await waitFor(() => {
      const calls = vi.mocked(fetchV2Organizations).mock.calls;
      expect(calls[calls.length - 1][0]).toMatchObject({ segment: "suppliers" });
    });
    fireEvent.click(screen.getByRole("tab", { name: /Todas/ }));
    await waitFor(() => {
      const calls = vi.mocked(fetchV2Organizations).mock.calls;
      expect(calls[calls.length - 1][0]).toMatchObject({ segment: undefined });
    });
  });

  it("shows each tab's count from the server facets", async () => {
    vi.mocked(fetchV2Organizations).mockResolvedValue(withFacets([listedOrganization()]) as never);
    render(<Institution360Page />);
    await screen.findByTestId("institution-card");
    const tabs = within(screen.getByTestId("institution-segments")).getAllByRole("tab");
    expect(tabs.map((tab) => tab.textContent)).toEqual([
      "Clientes / instituciones solicitantes1",
      "Proveedores y fabricantes1",
      "Otras instituciones participantes0",
      "Todas1.813",
    ]);
  });

  it("gives a supplier's cases as supplier and manufacturer, never as asking", async () => {
    vi.mocked(fetchV2Organizations).mockResolvedValue(
      page([
        listedOrganization({
          name: "Fabricante Ficticio de Ultrasonidos",
          case_count: 1,
          cases_as_supplier: 1,
          cases_as_manufacturer: 1,
        }),
      ]) as never,
    );
    render(<Institution360Page />);
    const roles = await screen.findByTestId("institution-card-roles");
    expect(roles.textContent).toContain("Pide0");
    expect(roles.textContent).toContain("Proveedor1");
    expect(roles.textContent).toContain("Fabricante1");
    expect(roles.textContent).toContain("Financia0");
    expect(roles.textContent).toContain("Mencionada0");
    expect(roles.textContent).toContain("Total casos1");
  });

  it("names a relationship recorded for OrigenLab apart from the parts on cases", async () => {
    vi.mocked(fetchV2Organizations).mockResolvedValue(
      page([listedOrganization({ relationship_roles: ["customer", "supplier"] })]) as never,
    );
    render(<Institution360Page />);
    const card = await screen.findByTestId("institution-card");
    expect(card.textContent).toContain("Registrada como cliente");
    expect(card.textContent).toContain("Registrada como proveedor");
  });

  it("says an empty segment has nothing recorded yet and points to Todas", async () => {
    vi.mocked(fetchV2Organizations).mockResolvedValue(page([]) as never);
    render(<Institution360Page />);
    expect(await screen.findByText(/Las demás siguen en «Todas»/)).toBeTruthy();
  });
});

describe("Institución 360", () => {
  function open() {
    window.location.hash = `#/instituciones?id=${ORG_ID}`;
    render(<Institution360Page />);
    return screen.findByTestId("institution-360-detail");
  }

  it("never merges 'is a supplier' with 'asks in a case' into one label", async () => {
    vi.mocked(fetchV2OrganizationCard).mockResolvedValue(
      organizationCard({
        relationships: [
          {
            organization_relationship_id: "r-1",
            role: "supplier",
            valid_from: "2026-01-01",
            valid_to: null,
            note: null,
          },
        ],
      }),
    );
    vi.mocked(fetchV2OrganizationCases).mockResolvedValue(
      page([organizationCaseWithRoles([{ role: "requesting_institution" }])]) as never,
    );
    await open();
    const panel = await screen.findByTestId("institution-360-roles");
    await waitFor(() => {
      expect(panel.textContent).toContain("Proveedor");
    });
    expect(panel.textContent).toContain("Relación anotada");
    expect(panel.textContent).toContain("Institución que pide");
  });

  it("gives the header the same evidence total as the Evidencia section", async () => {
    // One observation about the institution itself and two documents on its cases: the
    // header once counted only the two, so the page said 2 above a section headed 3.
    vi.mocked(fetchV2OrganizationCard).mockResolvedValue(
      organizationCard({
        evidence: [cardEvidence({ kind: "organization_name" })],
        counts: { evidence: 1 },
        connection_summary: emptyConnectionSummary({ cases: 1, case_evidence: 2 }),
      }),
    );
    await open();
    const summary = screen.getByTestId("institution-360-summary");
    expect(summary.textContent).toContain("Evidencia3");
    expect(screen.getByTestId("institution-360-evidence").textContent).toContain("Evidencia3");
  });

  it("counts the parts it holds on cases it is not the one asking in", async () => {
    vi.mocked(fetchV2OrganizationCases).mockResolvedValue(
      page([
        organizationCaseWithRoles([{ role: "supplier" }, { role: "manufacturer" }]),
      ]) as never,
    );
    await open();
    const roles = await screen.findByTestId("institution-360-case-roles");
    await waitFor(() => {
      expect(roles.textContent).toContain("Proveedor");
    });
    expect(roles.textContent).toContain("Fabricante");
  });

  it("says «sin papel comercial registrado» rather than drawing an empty box", async () => {
    await open();
    expect(screen.getByTestId("institution-360-roles").textContent).toContain(
      "Sin papel comercial registrado",
    );
  });

  it("puts named people before channels nobody owns, and marks the difference", async () => {
    vi.mocked(fetchV2OrganizationCard).mockResolvedValue(
      organizationCard({ contact_points: CHANNELS, counts: { contact_points: 12 } }),
    );
    await open();
    const panel = screen.getByTestId("institution-360-channels");
    const items = within(panel).getAllByRole("listitem");
    expect(items[0].textContent).toContain("María Ficticia");
    expect(panel.textContent).toContain("Canales pendientes");
    expect(within(panel).getByText("canal pendiente")).toBeTruthy();
    // The server capped the list at its child limit; the heading must still say 12.
    expect(panel.textContent).toContain("12");
  });

  it("walks from a channel to Contacto 360", async () => {
    vi.mocked(fetchV2OrganizationCard).mockResolvedValue(
      organizationCard({ contact_points: CHANNELS }),
    );
    await open();
    fireEvent.click(screen.getByText("María Ficticia"));
    expect(window.location.hash).toBe(
      "#/contactos?id=aaaaaaaa-1111-4111-8111-111111111111",
    );
  });

  it("shows every case it participates in, and the durable quotes of its cases", async () => {
    vi.mocked(fetchV2OrganizationCases).mockResolvedValue(
      page([organizationCaseWithRoles([{ role: "requesting_institution" }])]) as never,
    );
    vi.mocked(fetchV2OrganizationCard).mockResolvedValue(
      organizationCard({
        quotes: [connectedQuote()],
        connection_summary: emptyConnectionSummary({ cases: 1, quotes: 1 }),
      }),
    );
    await open();
    await waitFor(() => {
      expect(screen.getByTestId("case-summary-card")).toBeTruthy();
    });
    const quotes = screen.getByTestId("institution-360-quotes");
    expect(quotes.textContent).toContain("1235 · rev. 2 · Enviada");
  });

  it("says quotes are not migrated yet rather than that there were none", async () => {
    await open();
    expect(screen.getByTestId("institution-360-quotes").textContent).toContain(
      "aún no se migra",
    );
  });

  it("shows people, requested equipment, activity and marketing controls", async () => {
    vi.mocked(fetchV2OrganizationCard).mockResolvedValue(
      organizationCard({
        people: [
          {
            person_id: "p-1",
            display_name: "María Ficticia",
            role_title: "Jefa de laboratorio",
            unit_label: null,
            confirmation: "confirmed",
            valid_from: "2026-01-01",
            valid_to: null,
          },
        ],
        interests: [connectedInterest()],
        activities: [connectedActivity()],
        address_controls: [
          addressControl({ value_norm: "compras@instituto.invalid", control_kind: "block" }),
        ],
        connection_summary: emptyConnectionSummary({
          cases: 1,
          interests: 1,
          activities: 1,
          confirmed_people: 1,
        }),
      }),
    );
    await open();
    expect(screen.getByTestId("institution-360-people").textContent).toContain(
      "María Ficticia",
    );
    expect(screen.getByTestId("institution-360-interests").textContent).toContain(
      "Centrífuga CX-1",
    );
    expect(screen.getByTestId("institution-360-activity").textContent).toContain(
      "Pidió ficha técnica",
    );
    const marketing = screen.getByTestId("institution-360-marketing");
    expect(marketing.textContent).toContain("compras@instituto.invalid");
    expect(marketing.textContent).toContain("Bloqueo");
    expect(marketing.textContent).not.toMatch(/permitid[oa] ·/i);
  });

  it("asks the server for the cases by institution, never filtering a global list", async () => {
    await open();
    await waitFor(() => {
      expect(vi.mocked(fetchV2OrganizationCases)).toHaveBeenCalled();
    });
    expect(vi.mocked(fetchV2OrganizationCases).mock.calls[0][0]).toBe(ORG_ID);
  });

  it("names each part it holds on a case, and both when it holds two", async () => {
    vi.mocked(fetchV2OrganizationCases).mockResolvedValue(
      page([
        organizationCaseWithRoles([
          { role: "supplier" },
          { role: "manufacturer" },
          // A part that ended is history, and reads as history rather than as today.
          { role: "purchasing_agent", valid_to: "2026-06-01", is_current: false },
        ]),
      ]) as never,
    );
    await open();
    const role = await screen.findByTestId("institution-360-case-role");
    expect(role.textContent).toContain("Proveedor");
    expect(role.textContent).toContain("Fabricante");
    expect(role.textContent).toContain("Agente de compras · terminado");
  });

  it("says it participates in nothing rather than that it asks in nothing", async () => {
    await open();
    await waitFor(() => {
      expect(screen.getByTestId("institution-360-cases").textContent).toContain(
        "No participa en ningún caso cargado",
      );
    });
  });

  it("keeps ids, raw vocabulary and relationships in a drawer that starts closed", async () => {
    await open();
    const drawer = screen.getByTestId("v2-technical-details") as HTMLDetailsElement;
    expect(drawer.open).toBe(false);
    expect(drawer.textContent).toContain(ORG_ID);
  });

  it("reports a failed load instead of drawing an empty institution", async () => {
    vi.mocked(fetchV2OrganizationCard).mockRejectedValue(new Error("boom"));
    window.location.hash = `#/instituciones?id=${ORG_ID}`;
    render(<Institution360Page />);
    await waitFor(() => {
      expect(screen.getAllByText(/Ficha de institución/).length).toBeGreaterThan(0);
    });
    expect(screen.queryByTestId("institution-360-detail")).toBeNull();
  });
});
