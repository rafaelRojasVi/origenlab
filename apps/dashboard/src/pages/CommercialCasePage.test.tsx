import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CommercialCasePage } from "./CommercialCasePage";

vi.mock("../api/v2Client", () => ({
  fetchV2Cases: vi.fn(),
  fetchV2CaseCard: vi.fn(),
  fetchV2QuotesToFollowUp: vi.fn(),
}));

import { fetchV2CaseCard, fetchV2Cases, fetchV2QuotesToFollowUp } from "../api/v2Client";
import {
  card,
  evidence,
  interest,
  listedCase,
  organization,
} from "../lib/__fixtures__/commercialCase";

/**
 * A deep link only resolves for the shape the dashboard-proxy allows, so the detail tests
 * need real UUIDs. The fixtures' short ids stay where they are: they exercise the list,
 * which never builds a URL from them until somebody clicks.
 */
const CASE_ID = "11111111-2222-4333-8444-555555555555";

const page = <T,>(items: T[], total = items.length) => ({
  items,
  total,
  limit: 20,
  offset: 0,
});

beforeEach(() => {
  window.location.hash = "";
  vi.mocked(fetchV2QuotesToFollowUp).mockResolvedValue(page([]));
});

afterEach(() => {
  window.location.hash = "";
  vi.resetAllMocks();
});

describe("the empty state, which is the real one", () => {
  it("says an empty case list is correct rather than broken", async () => {
    vi.mocked(fetchV2Cases).mockResolvedValue(page([]));
    render(<CommercialCasePage />);

    await waitFor(() => {
      expect(screen.getByTestId("v2-empty-state")).toBeTruthy();
    });
    expect(screen.getByText(/Ningún caso comercial todavía/)).toBeTruthy();
    expect(screen.getByText(/no una falla/)).toBeTruthy();
  });

  it("keeps the opening command blocked, and out of the operator's way", async () => {
    vi.mocked(fetchV2Cases).mockResolvedValue(page([]));
    render(<CommercialCasePage />);

    await waitFor(() => {
      expect(screen.getByTestId("case-command-preview-open_commercial_case")).toBeTruthy();
    });
    const preview = screen.getByTestId("case-command-preview-open_commercial_case");
    expect(preview.getAttribute("data-availability")).toBe("blocked");
    const button = within(preview).getByRole("button", { name: "Abrir caso comercial" });
    expect((button as HTMLButtonElement).disabled).toBe(true);

    // It lives inside the technical drawer, and that drawer is closed. A command nobody can
    // send is reference material, not an affordance competing with the case list.
    const drawer = screen.getByTestId("v2-technical-details") as HTMLDetailsElement;
    expect(drawer.open).toBe(false);
    expect(drawer.contains(preview)).toBe(true);
  });
});

describe("the case list", () => {
  it("says when nobody has named the institution that is asking", async () => {
    vi.mocked(fetchV2Cases).mockResolvedValue(page([listedCase()]));
    vi.mocked(fetchV2CaseCard).mockResolvedValue(card());
    render(<CommercialCasePage />);

    await waitFor(() => {
      expect(screen.getByText("Caso ficticio 1")).toBeTruthy();
    });
    expect(screen.getByText("Nadie ha dicho quién pide")).toBeTruthy();
  });

  it("names the institution when there is one", async () => {
    vi.mocked(fetchV2Cases).mockResolvedValue(
      page([
        listedCase({
          requesting_organization_id: "org-1",
          requesting_organization_name: "Universidad Ficticia del Sur",
          requesting_confirmation: "confirmed",
          organization_count: 1,
        }),
      ]),
    );
    vi.mocked(fetchV2CaseCard).mockResolvedValue(card());
    render(<CommercialCasePage />);

    await waitFor(() => {
      expect(screen.getByText("Universidad Ficticia del Sur")).toBeTruthy();
    });
  });

  it("summarizes what each case seeks, which needs the card and not the row", async () => {
    vi.mocked(fetchV2Cases).mockResolvedValue(page([listedCase()]));
    vi.mocked(fetchV2CaseCard).mockResolvedValue(
      card({
        interests: [interest({ model_text: "Centrífuga ejemplo CX-0" })],
        organizations: [
          organization({ role: "supplier", name: "Proveedor Ficticio Ltda." }),
        ],
      }),
    );
    render(<CommercialCasePage />);

    await waitFor(() => {
      expect(fetchV2CaseCard).toHaveBeenCalledWith("op-1");
    });
    const summary = await screen.findByTestId("case-summary-card");
    expect(within(summary).getByText("Centrífuga ejemplo CX-0")).toBeTruthy();
    expect(within(summary).getByText("Proveedor Ficticio Ltda.")).toBeTruthy();
    // Money never appears on a case: it lives on the quote revision alone.
    expect(within(summary).queryByText(/\$/)).toBeNull();
  });

  it("still draws a row whose card failed, from the list fields alone", async () => {
    vi.mocked(fetchV2Cases).mockResolvedValue(
      page([listedCase({ organization_count: 3, interest_count: 2 })]),
    );
    vi.mocked(fetchV2CaseCard).mockRejectedValue(new Error("boom"));
    render(<CommercialCasePage />);

    const summary = await screen.findByTestId("case-summary-card");
    expect(within(summary).getByText("Caso ficticio 1")).toBeTruthy();
    expect(within(summary).getByText("Nadie ha dicho quién pide")).toBeTruthy();

    // And it asserts no absence it never measured. The list row carries counts, not roles,
    // so "sin proveedor en el caso" would be the card inventing a fact it cannot see.
    expect(summary.textContent).toContain("3 institución(es)");
    expect(summary.textContent).not.toContain("Sin proveedor");
    expect(summary.textContent).not.toContain("Sin fabricante");
  });

  it("reports a failed load instead of rendering an empty list", async () => {
    vi.mocked(fetchV2Cases).mockRejectedValue(new Error("boom"));
    render(<CommercialCasePage />);

    // The error names the surface, so "Casos comerciales" now appears twice: once as the
    // page title and once in the failure. Neither the empty state nor a row is rendered —
    // "nothing loaded" must never be drawn as "there is nothing".
    await waitFor(() => {
      expect(screen.getAllByText(/Casos comerciales/).length).toBeGreaterThan(1);
    });
    expect(screen.queryByTestId("v2-empty-state")).toBeNull();
    expect(screen.queryAllByTestId("case-summary-card")).toHaveLength(0);
  });
});

describe("the case list filters", () => {
  const lastCall = () => {
    const calls = vi.mocked(fetchV2Cases).mock.calls;
    return calls[calls.length - 1][0];
  };

  it("sends the prospect preset as two stages, and each other filter", async () => {
    vi.mocked(fetchV2Cases).mockResolvedValue(page([listedCase()]));
    vi.mocked(fetchV2CaseCard).mockResolvedValue(card());
    render(<CommercialCasePage />);
    await screen.findByTestId("case-filters");

    fireEvent.change(screen.getByLabelText("Etapa"), { target: { value: "prospects" } });
    await waitFor(() => expect(lastCall()).toMatchObject({ stage: ["lead", "qualifying"] }));

    fireEvent.change(screen.getByLabelText("Cotización"), { target: { value: "none" } });
    fireEvent.change(screen.getByLabelText("Actividad reciente"), { target: { value: "90" } });
    fireEvent.change(screen.getByPlaceholderText("nombre"), { target: { value: "Austral" } });
    fireEvent.change(screen.getByPlaceholderText("modelo, producto…"), {
      target: { value: "centrífuga" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Filtrar" }));
    await waitFor(() =>
      expect(lastCall()).toMatchObject({
        stage: ["lead", "qualifying"],
        quoteState: "none",
        activeWithinDays: 90,
        organizationQ: "Austral",
        interestQ: "centrífuga",
      }),
    );
  });

  it("says nothing recorded matches rather than that no case exists", async () => {
    vi.mocked(fetchV2Cases).mockResolvedValue(page([]));
    render(<CommercialCasePage />);
    await screen.findByTestId("case-filters");
    fireEvent.change(screen.getByLabelText("Cotización"), { target: { value: "sent" } });
    expect(await screen.findByText("Ningún caso registrado cumple estos filtros")).toBeTruthy();
    expect(screen.getByText(/no significa que no exista/)).toBeTruthy();
  });

  it("reads every current part, its origin, its quote state and its last activity from the row", async () => {
    vi.mocked(fetchV2Cases).mockResolvedValue(
      page([
        listedCase({
          requesting_organization_id: "org-r",
          requesting_organization_name: "Universidad Ficticia",
          evidence_count: 2,
          participants: [
            { organization_id: "org-r", name: "Universidad Ficticia", role: "requesting_institution", confirmation: "confirmed" },
            { organization_id: "org-s", name: "Proveedor Ficticio", role: "supplier", confirmation: "confirmed" },
            { organization_id: "org-s", name: "Proveedor Ficticio", role: "manufacturer", confirmation: "confirmed" },
            { organization_id: "org-f", name: "Fondo Ficticio", role: "funder", confirmation: "confirmed" },
            { organization_id: "org-m", name: "Mencionada Ficticia", role: "mentioned", confirmation: "machine_proposed" },
          ],
          interest_labels: ["Centrífuga CX-1"],
          quote_count: 1,
          latest_quote_status: "draft",
          last_activity_at: "2026-09-20T10:00:00Z",
        }),
      ]),
    );
    vi.mocked(fetchV2CaseCard).mockRejectedValue(new Error("card not needed"));
    render(<CommercialCasePage />);
    const summary = await screen.findByTestId("case-summary-card");
    expect(summary.textContent).toContain("Universidad Ficticia");
    expect(summary.textContent).toContain("Centrífuga CX-1");
    expect(summary.textContent).toContain("Proveedor Ficticio");
    const others = within(summary).getByTestId("case-summary-other-roles");
    expect(others.textContent).toContain("Financia: Fondo Ficticio");
    expect(others.textContent).toContain("Mencionada: Mencionada Ficticia");
    expect(summary.textContent).toContain("2 documentos vinculados");
    expect(summary.textContent).toContain("1 cotización · última: Borrador");
    expect(summary.textContent).toContain("actividad registrada");
  });
});

describe("one open case", () => {
  const detailed = card({
    opportunity_id: CASE_ID,
    organizations: [
      organization({
        opportunity_organization_id: "oo-current",
        role: "requesting_institution",
        name: "Universidad Ficticia del Sur",
      }),
      organization({
        opportunity_organization_id: "oo-closed",
        role: "mentioned",
        name: "Fabricante Ficticio S.A.",
        is_current: false,
        valid_to: "2026-09-22",
      }),
    ],
    interests: [
      interest({ model_text: "Centrífuga ejemplo CX-0", quantity: 2, quantity_unit: "unidades" }),
    ],
    evidence: [
      evidence(),
      evidence({
        opportunity_evidence_id: "oe-2",
        relation: "contradicts",
        assertion_kind: "organization_name",
        assertion_value: "otra institución ficticia",
        subject_kind: "assertion",
      }),
    ],
  });

  async function openTheCase() {
    window.location.hash = `#/casos?id=${CASE_ID}`;
    vi.mocked(fetchV2Cases).mockResolvedValue(page([listedCase({ opportunity_id: CASE_ID })]));
    vi.mocked(fetchV2CaseCard).mockResolvedValue(detailed);
    render(<CommercialCasePage />);
    await waitFor(() => {
      expect(screen.getAllByTestId("case-organization").length).toBeGreaterThan(0);
    });
  }

  it("opens straight from the hash, without a click", async () => {
    await openTheCase();
    expect(fetchV2CaseCard).toHaveBeenCalledWith(CASE_ID);
    expect(screen.getByTestId("case-360-detail")).toBeTruthy();
  });

  it("shows the closed part row instead of hiding it", async () => {
    await openTheCase();
    const rows = screen.getAllByTestId("case-organization");
    expect(rows).toHaveLength(2);
    expect(within(rows[1]).getByText("Cerrada")).toBeTruthy();
  });

  it("shows the contradicting evidence and warns about it", async () => {
    await openTheCase();
    expect(screen.getByText("Lo contradice")).toBeTruthy();
    expect(within(screen.getByTestId("case-gaps")).getByText(/lo contradice/)).toBeTruthy();
  });

  it("renders all six commands, every one disabled and blocked, inside the closed drawer", async () => {
    await openTheCase();
    const drawer = screen.getByTestId("v2-technical-details") as HTMLDetailsElement;
    expect(drawer.open).toBe(false);

    for (const id of [
      "open_commercial_case",
      "link_case_evidence",
      "add_case_organization",
      "set_case_organization_role",
      "record_case_interest",
      "advance_case_stage",
    ]) {
      const preview = screen.getByTestId(`case-command-preview-${id}`);
      expect(preview.getAttribute("data-availability")).toBe("blocked");
      expect(drawer.contains(preview)).toBe(true);
    }

    const actions = screen.getAllByTestId("case-preview-action");
    expect(actions).toHaveLength(6);
    for (const action of actions) {
      expect((action as HTMLButtonElement).disabled).toBe(true);
    }
  });

  it("never renders a request body, because nothing is chosen", async () => {
    await openTheCase();
    expect(screen.queryByTestId("case-command-request")).toBeNull();
  });

  it("says why nothing can be sent, naming the proxy", async () => {
    await openTheCase();
    expect(
      screen.getAllByText(/el proxy no permite ningún POST bajo \/v2/).length,
    ).toBeGreaterThan(0);
  });

  it("shows the quantity the interest recorded and no price anywhere", async () => {
    await openTheCase();
    expect(screen.getByText("2 unidades")).toBeTruthy();
    expect(screen.queryByText(/\$/)).toBeNull();
  });

  it("serves the stage machine from the API rather than restating it", async () => {
    await openTheCase();
    // The card's own `stage_machine` is what is printed; nothing here holds a copy of the
    // allowed transitions.
    expect(screen.getByText(/Calificando/)).toBeTruthy();
  });
});
