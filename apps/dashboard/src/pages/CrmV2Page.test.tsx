import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CrmV2Page } from "./CrmV2Page";

vi.mock("../api/v2Client", () => ({
  fetchV2Contacts: vi.fn(),
  fetchV2Organizations: vi.fn(),
  fetchV2Prospects: vi.fn(),
  fetchV2Evidence: vi.fn(),
  fetchV2ContactCard: vi.fn(),
  fetchV2OrganizationCard: vi.fn(),
}));

import {
  fetchV2ContactCard,
  fetchV2Contacts,
  fetchV2Evidence,
  fetchV2OrganizationCard,
  fetchV2Organizations,
  fetchV2Prospects,
} from "../api/v2Client";

const page = <T,>(items: T[], total = items.length) => ({
  items,
  total,
  limit: 50,
  offset: 0,
});

const CONTACT = {
  contact_point_id: "96301691-af05-51ea-82e3-05f5fae40837",
  address: "compras@uni.example",
  usage: "shared_mailbox" as const,
  confirmation: "machine_proposed" as const,
  person_id: null,
  person_display_name: null,
  organization_id: null,
  organization_name: null,
  created_at: null,
};

const ORGANIZATION = {
  organization_id: "11111111-2222-4333-8444-555555555555",
  name: "Universidad Ejemplo",
  kind: "unknown",
  confirmation: "machine_proposed" as const,
  parent_organization_id: null,
  contact_point_count: 12,
  created_at: null,
};

beforeEach(() => {
  vi.mocked(fetchV2Contacts).mockResolvedValue(page([CONTACT], 9460) as never);
  vi.mocked(fetchV2Organizations).mockResolvedValue(page([ORGANIZATION], 1812) as never);
  vi.mocked(fetchV2Prospects).mockResolvedValue(page([], 0) as never);
  vi.mocked(fetchV2Evidence).mockResolvedValue(page([], 0) as never);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("CrmV2Page", () => {
  it("lists contacts from the durable boundary and counts from the server total", async () => {
    render(<CrmV2Page />);
    expect(await screen.findByText("compras@uni.example")).toBeTruthy();
    // 9.460, not 1 — the page shows 50 rows and must not report the page as the total.
    expect(screen.getByTestId("v2-page-footer").textContent).toContain("9.460");
  });

  it("calls an unconfirmed row a machine proposal rather than a customer", async () => {
    render(<CrmV2Page />);
    await screen.findByText("compras@uni.example");
    expect(screen.getAllByText("Propuesta de máquina").length).toBeGreaterThan(0);
  });

  it("switches to organizations and searches by name", async () => {
    render(<CrmV2Page />);
    await screen.findByText("compras@uni.example");
    fireEvent.click(screen.getByText("Organizaciones"));
    expect(await screen.findByText("Universidad Ejemplo")).toBeTruthy();

    fireEvent.change(screen.getByPlaceholderText("nombre"), {
      target: { value: "ejemplo" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Buscar" }));
    await waitFor(() => {
      expect(vi.mocked(fetchV2Organizations)).toHaveBeenCalledWith(
        expect.objectContaining({ q: "ejemplo" }),
      );
    });
  });

  it("says why prospects are empty instead of showing a bare zero", async () => {
    render(<CrmV2Page />);
    fireEvent.click(screen.getByText("Prospectos"));
    const empty = await screen.findByTestId("v2-empty-state");
    // A zero that looks like a clear workload when the table is merely unmigrated is the
    // failure mode this avoids.
    expect(empty.textContent).toContain("falta migrar el histórico V1");
  });

  it("filters evidence by resolution and by provenance", async () => {
    render(<CrmV2Page />);
    fireEvent.click(screen.getByText("Evidencia"));
    await waitFor(() => expect(vi.mocked(fetchV2Evidence)).toHaveBeenCalled());

    fireEvent.change(screen.getByLabelText(/Resolución/), {
      target: { value: "ambiguous" },
    });
    await waitFor(() => {
      expect(vi.mocked(fetchV2Evidence)).toHaveBeenCalledWith(
        expect.objectContaining({ resolution: "ambiguous" }),
      );
    });

    fireEvent.change(screen.getByLabelText(/Procedencia/), {
      target: { value: "gmail_message" },
    });
    await waitFor(() => {
      expect(vi.mocked(fetchV2Evidence)).toHaveBeenCalledWith(
        expect.objectContaining({ sourceKind: "gmail_message" }),
      );
    });
  });

  it("opens a contact card and explains every absence rather than leaving it blank", async () => {
    vi.mocked(fetchV2ContactCard).mockResolvedValue({
      ...CONTACT,
      channel_kind: "email",
      updated_at: null,
      organization_kind: null,
      origin_source_kind: "migration_manifest",
      origin_source_uri: null,
      origin_review_status: "pending",
      sibling_contact_points: [],
      affiliations: [],
      evidence: [
        {
          assertion_id: "a1",
          kind: "contacted_address",
          value_norm: "compras@uni.example",
          resolution: "promoted",
          resolved_kind: "contact_point",
          ambiguity_note: null,
          observed_at: null,
          source_kind: "migration_manifest",
          source_uri: null,
          source_review_status: "pending",
          source_is_quarantined: false,
        },
      ],
      marketing: [],
      address_controls: [],
      counts: { evidence: 1 },
    } as never);

    render(<CrmV2Page />);
    fireEvent.click(await screen.findByText("compras@uni.example"));
    const drawer = await screen.findByTestId("v2-card-drawer");
    expect(drawer.textContent).toContain("buzón de rol");
    expect(drawer.textContent).toContain("pista de ruteo");
    expect(screen.getAllByTestId("v2-card-evidence-row")).toHaveLength(1);
  });

  it("opens an organization card and reports the true channel count, not the visible one", async () => {
    vi.mocked(fetchV2OrganizationCard).mockResolvedValue({
      ...ORGANIZATION,
      legal_name: null,
      note: null,
      version: 1,
      updated_at: null,
      parent_organization_name: null,
      merged_into_organization_id: null,
      merged_into_organization_name: null,
      origin_source_kind: "migration_manifest",
      origin_source_uri: null,
      origin_review_status: "pending",
      contact_points: [
        {
          contact_point_id: "c1",
          address: "compras@uni.example",
          channel_kind: "email",
          usage: "shared_mailbox",
          confirmation: "machine_proposed",
          person_display_name: null,
        },
      ],
      people: [],
      domains: [],
      relationships: [],
      child_organizations: [],
      evidence: [],
      counts: { contact_points: 4321 },
    } as never);

    render(<CrmV2Page />);
    await screen.findByText("compras@uni.example");
    fireEvent.click(screen.getByText("Organizaciones"));
    fireEvent.click(await screen.findByText("Universidad Ejemplo"));
    const drawer = await screen.findByTestId("v2-card-drawer");
    // The server capped the list at its child limit; the heading must still say 4.321.
    expect(drawer.textContent).toContain("4.321");
    expect(drawer.textContent).toContain("Tipo sin clasificar");
  });

  it("surfaces a load failure instead of rendering an empty table as if it were data", async () => {
    vi.mocked(fetchV2Contacts).mockRejectedValue(new Error("boom"));
    render(<CrmV2Page />);
    expect(await screen.findByText(/Contactos/)).toBeTruthy();
    await waitFor(() => {
      expect(screen.queryByTestId("v2-contacts-table")).toBeNull();
    });
  });
});
