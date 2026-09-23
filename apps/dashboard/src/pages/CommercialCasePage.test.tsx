import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CommercialCasePage } from "./CommercialCasePage";

vi.mock("../api/v2Client", () => ({
  fetchV2Cases: vi.fn(),
  fetchV2CaseCard: vi.fn(),
}));

import { fetchV2CaseCard, fetchV2Cases } from "../api/v2Client";
import {
  card,
  evidence,
  interest,
  listedCase,
  organization,
} from "../lib/__fixtures__/commercialCase";

const page = <T,>(items: T[], total = items.length) => ({
  items,
  total,
  limit: 50,
  offset: 0,
});

afterEach(() => {
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

  it("still shows the opening command, blocked, with nothing chosen", async () => {
    vi.mocked(fetchV2Cases).mockResolvedValue(page([]));
    render(<CommercialCasePage />);

    await waitFor(() => {
      expect(
        screen.getByTestId("case-command-preview-open_commercial_case"),
      ).toBeTruthy();
    });
    const preview = screen.getByTestId("case-command-preview-open_commercial_case");
    expect(preview.getAttribute("data-availability")).toBe("blocked");
    const button = within(preview).getByRole("button", { name: "Abrir caso comercial" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(within(preview).getByText(/documento que causó el caso/)).toBeTruthy();
  });
});

describe("the case list", () => {
  it("says when nobody has named the institution that is asking", async () => {
    vi.mocked(fetchV2Cases).mockResolvedValue(page([listedCase()]));
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
    render(<CommercialCasePage />);

    await waitFor(() => {
      expect(screen.getByText("Pide: Universidad Ficticia del Sur")).toBeTruthy();
    });
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
    expect(screen.queryAllByTestId("case-row")).toHaveLength(0);
  });
});

describe("one open case", () => {
  const detailed = card({
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
    interests: [interest({ model_text: "Centrífuga ejemplo CX-0", quantity: 2, quantity_unit: "unidades" })],
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
    vi.mocked(fetchV2Cases).mockResolvedValue(page([listedCase()]));
    vi.mocked(fetchV2CaseCard).mockResolvedValue(detailed);
    render(<CommercialCasePage />);
    await waitFor(() => {
      expect(screen.getByText("Caso ficticio 1")).toBeTruthy();
    });
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    await waitFor(() => {
      expect(screen.getAllByTestId("case-organization").length).toBeGreaterThan(0);
    });
  }

  it("fetches the card only when the row is expanded", async () => {
    vi.mocked(fetchV2Cases).mockResolvedValue(page([listedCase()]));
    vi.mocked(fetchV2CaseCard).mockResolvedValue(detailed);
    render(<CommercialCasePage />);
    await waitFor(() => {
      expect(screen.getByText("Caso ficticio 1")).toBeTruthy();
    });
    expect(fetchV2CaseCard).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { expanded: false }));
    await waitFor(() => {
      expect(fetchV2CaseCard).toHaveBeenCalledWith("op-1");
    });
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

  it("renders all six commands, every one disabled and blocked", async () => {
    await openTheCase();
    for (const id of [
      "open_commercial_case",
      "link_case_evidence",
      "add_case_organization",
      "set_case_organization_role",
      "record_case_interest",
      "advance_case_stage",
    ]) {
      // `open_commercial_case` is rendered twice — once at page level, once on the card —
      // and both must be blocked, so this asserts over every instance rather than the first.
      const previews = screen.getAllByTestId(`case-command-preview-${id}`);
      expect(previews.length).toBeGreaterThan(0);
      for (const preview of previews) {
        expect(preview.getAttribute("data-availability")).toBe("blocked");
      }
    }
    // Every affordance on the screen, including the page-level opening one.
    const actions = screen.getAllByTestId("case-preview-action");
    expect(actions).toHaveLength(7);
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
    expect(screen.getAllByText(/el proxy no permite ningún POST bajo \/v2/).length).toBeGreaterThan(
      0,
    );
  });

  it("shows the quantity the interest recorded and no price anywhere", async () => {
    await openTheCase();
    expect(screen.getByText("2 unidades")).toBeTruthy();
    expect(screen.queryByText(/\$/)).toBeNull();
  });
});
