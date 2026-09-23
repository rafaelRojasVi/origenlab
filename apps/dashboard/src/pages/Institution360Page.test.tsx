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
  listedQuote,
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
    page(
      [
        {
          organization_id: ORG_ID,
          name: "Instituto Ficticio de Metrología",
          kind: "unknown",
          confirmation: "machine_proposed" as const,
          parent_organization_id: null,
          contact_point_count: 12,
          created_at: null,
        },
      ],
      1812,
    ) as never,
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
  });

  it("counts from the server total", async () => {
    render(<Institution360Page />);
    expect((await screen.findByTestId("institution-360-footer")).textContent).toContain("1.812");
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

  it("shows every case it participates in, and their quotes", async () => {
    vi.mocked(fetchV2OrganizationCases).mockResolvedValue(
      page([organizationCaseWithRoles([{ role: "requesting_institution" }])]) as never,
    );
    vi.mocked(fetchV2QuotesToFollowUp).mockResolvedValue(page([listedQuote()]) as never);
    await open();
    await waitFor(() => {
      expect(screen.getByTestId("case-summary-card")).toBeTruthy();
    });
    expect(within(screen.getByTestId("institution-360-quotes")).getByText(/1235/)).toBeTruthy();
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
