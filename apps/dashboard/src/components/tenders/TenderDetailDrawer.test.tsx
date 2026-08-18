import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { LicitacionIntel } from "../../api/institutionIntel/types";
import { TenderDetailDrawer } from "./TenderDetailDrawer";

const getLicitacionIntel = vi.fn();

vi.mock("../../api/institutionIntel/adapter", () => ({
  institutionIntelAdapter: {
    getLicitacionIntel: (...args: unknown[]) => getLicitacionIntel(...args),
  },
}));

vi.mock("./TenderAnnexUploadPanel", () => ({
  TenderAnnexUploadPanel: ({
    onPersisted,
  }: {
    onPersisted?: () => void;
  }) => (
    <button
      type="button"
      data-testid="fake-annex-persist"
      onClick={onPersisted}
    >
      persist
    </button>
  ),
}));

const BASE_INTEL: LicitacionIntel = {
  tenderCode: "1057898-51-LP26",
  buyerDisplayName: "HOSPITAL REGIONAL DE TALCA",
  eligibilityStatus: "open_public",
  procurementMethodRaw: null,
  t1SourceKind: null,
  terms: {
    status: "available",
    data: [
      {
        fieldName: "payment_deadline_days",
        label: "payment_deadline_days",
        state: "explicit",
        value: 30,
        unit: "días",
        evidence: {
          excerpt: "Los pagos serán realizados dentro de los 30 días corridos",
          documentLabel: "Res 1 bases PP 09.pdf",
          locator: "página 36",
        },
      },
    ],
  },
  itemBudget: { status: "available_empty" },
  totalBudgetReconciled: false,
  recognitionDelta: { status: "not_available" },
  coverage: { status: "available", data: { documentsDiscovered: 11, documentsRead: 11, incompleteReasonCodes: [] } },
};

describe("TenderDetailDrawer", () => {
  it("shows an empty prompt when nothing is selected yet", () => {
    render(<TenderDetailDrawer tenderCode={null} />);
    screen.getByTestId("tender-detail-empty");
    expect(getLicitacionIntel).not.toHaveBeenCalled();
  });

  it("renders header identity + eligibility and the term grid with evidence disclosure for a selected tender, with exactly one detail request", async () => {
    getLicitacionIntel.mockReset();
    getLicitacionIntel.mockResolvedValue(BASE_INTEL);
    render(<TenderDetailDrawer tenderCode="1057898-51-LP26" />);

    await waitFor(() => {
      screen.getByText(/Ver evidencia/);
    });
    screen.getByText("HOSPITAL REGIONAL DE TALCA");
    screen.getByText("1057898-51-LP26");
    // The drawer header is the sole place eligibility is shown — the T1
    // commercial body (LicitacionIntelBody) renders no badge of its own, so
    // this must appear exactly once, not duplicated.
    expect(screen.getAllByText("Abierto / público")).toHaveLength(1);

    // Evidence excerpt + locator are reachable via the <details> disclosure.
    screen.getByText(/30 días corridos/);

    // Rendering used to also mount a self-fetching LicitacionIntelCard for
    // the body, doubling the request for one tender selection. The body is
    // now presentation-only (LicitacionIntelBody), so exactly one request
    // is made for this selection.
    expect(getLicitacionIntel).toHaveBeenCalledTimes(1);
  });

  it("renders a distinct 'not actionable' state (not the generic T1-unavailable message) when W1 returns 404 for the tender", async () => {
    getLicitacionIntel.mockReset();
    getLicitacionIntel.mockResolvedValue(null);
    render(<TenderDetailDrawer tenderCode="closed-tender" />);

    await waitFor(() => {
      screen.getByTestId("tender-detail-not-actionable");
    });
    screen.getByText(/ya no está entre las oportunidades accionables/);
    expect(screen.queryByTestId("tender-detail-error")).toBeNull();
    expect(screen.queryByTestId("tender-detail-loading")).toBeNull();
  });

  it("renders a distinct transport-error state (not a perpetual skeleton) when the request rejects", async () => {
    getLicitacionIntel.mockReset();
    getLicitacionIntel.mockRejectedValue(new Error("network down"));
    render(<TenderDetailDrawer tenderCode="1057898-51-LP26" />);

    await waitFor(() => {
      screen.getByTestId("tender-detail-error");
    });
    screen.getByText(/No se pudo cargar la información de esta licitación/);
    expect(screen.queryByTestId("tender-detail-loading")).toBeNull();
    expect(screen.queryByTestId("tender-detail-not-actionable")).toBeNull();
    expect(screen.queryByTestId("tender-detail-drawer")).toBeNull();
  });

  it("transport error and 404-not-actionable render visibly different text/state, not a shared generic message", async () => {
    getLicitacionIntel.mockReset();
    getLicitacionIntel.mockRejectedValueOnce(new Error("network down"));
    const { unmount } = render(<TenderDetailDrawer tenderCode="1057898-51-LP26" />);
    await waitFor(() => screen.getByTestId("tender-detail-error"));
    const errorText = screen.getByTestId("tender-detail-error").textContent;
    unmount();

    getLicitacionIntel.mockReset();
    getLicitacionIntel.mockResolvedValueOnce(null);
    render(<TenderDetailDrawer tenderCode="closed-tender" />);
    await waitFor(() => screen.getByTestId("tender-detail-not-actionable"));
    const notFoundText = screen.getByTestId("tender-detail-not-actionable").textContent;

    expect(errorText).not.toEqual(notFoundText);
  });

  it("renders T1-not-published as its own honest state, distinct from both transport error and 404-not-actionable", async () => {
    getLicitacionIntel.mockReset();
    getLicitacionIntel.mockResolvedValue({
      ...BASE_INTEL,
      terms: { status: "not_available" },
      itemBudget: { status: "not_available" },
      coverage: { status: "not_available" },
    } satisfies LicitacionIntel);
    render(<TenderDetailDrawer tenderCode="1057898-51-LP26" />);

    await waitFor(() => {
      screen.getByTestId("tender-detail-drawer");
    });
    screen.getByText(/T1 todavía no publica términos comerciales/);
    expect(screen.queryByTestId("tender-detail-error")).toBeNull();
    expect(screen.queryByTestId("tender-detail-not-actionable")).toBeNull();
  });

  it("renders coverage as available_incomplete honestly, without hiding the partial term data", async () => {
    getLicitacionIntel.mockReset();
    getLicitacionIntel.mockResolvedValue({
      ...BASE_INTEL,
      terms: { status: "available_incomplete", reasonCodes: ["some_attachments_unread"], partial: BASE_INTEL.terms.status === "available" ? BASE_INTEL.terms.data : [] },
      coverage: {
        status: "available_incomplete",
        reasonCodes: ["some_attachments_unread"],
        partial: { documentsDiscovered: 11, documentsRead: 8, incompleteReasonCodes: ["some_attachments_unread"] },
      },
    } satisfies LicitacionIntel);
    render(<TenderDetailDrawer tenderCode="1057898-51-LP26" />);

    await waitFor(() => {
      expect(screen.getAllByText(/Lectura incompleta/).length).toBeGreaterThan(0);
    });
    // Incomplete coverage must not claim absence is genuine — it must warn
    // that unread evidence may still hold additional terms.
    screen.getByTestId("coverage-incomplete-note");
    expect(screen.queryByTestId("coverage-complete-claim")).toBeNull();
  });

  it("refetches tender detail exactly once after an annex dossier is persisted", async () => {
    getLicitacionIntel.mockReset();
    getLicitacionIntel.mockResolvedValue(BASE_INTEL);

    render(
      <TenderDetailDrawer tenderCode="1057898-51-LP26" />,
    );

    await waitFor(() =>
      expect(getLicitacionIntel).toHaveBeenCalledTimes(1),
    );

    fireEvent.click(screen.getByTestId("fake-annex-persist"));

    await waitFor(() =>
      expect(getLicitacionIntel).toHaveBeenCalledTimes(2),
    );

    expect(getLicitacionIntel).toHaveBeenNthCalledWith(
      2,
      "1057898-51-LP26",
    );
  });

  it("never renders a contact/outreach action", async () => {
    getLicitacionIntel.mockReset();
    getLicitacionIntel.mockResolvedValue(BASE_INTEL);
    render(<TenderDetailDrawer tenderCode="1057898-51-LP26" />);

    await waitFor(() => {
      screen.getByTestId("tender-detail-drawer");
    });
    expect(screen.queryByRole("button", { name: /contact/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /contactar/i })).toBeNull();
    expect(screen.queryByText(/outreach/i)).toBeNull();
  });
});
