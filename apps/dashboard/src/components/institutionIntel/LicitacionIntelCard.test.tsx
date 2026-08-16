import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { LicitacionIntel } from "../../api/institutionIntel/types";
import { LicitacionIntelCard } from "./LicitacionIntelCard";

const getLicitacionIntel = vi.fn();

vi.mock("../../api/institutionIntel/adapter", () => ({
  institutionIntelAdapter: {
    getLicitacionIntel: (...args: unknown[]) => getLicitacionIntel(...args),
  },
}));

const OPEN_TENDER_INTEL: LicitacionIntel = {
  tenderCode: "1057898-51-LP26",
  buyerDisplayName: "HOSPITAL REGIONAL DE TALCA",
  eligibilityStatus: "open_public",
  procurementMethodRaw: null,
  terms: {
    status: "available",
    data: [
      {
        fieldName: "faithful_performance_guarantee_percent",
        label: "faithful_performance_guarantee_percent",
        state: "explicit",
        value: 5,
        unit: "%",
        evidence: { excerpt: "Garantía de Fiel Cumplimiento equivalente al 5%", documentLabel: "bases.pdf", locator: "página 7" },
      },
    ],
  },
  itemBudget: {
    status: "available",
    data: [
      { itemLabel: "Centrífuga refrigerada", quantity: 1, amount: 6200000 },
      { itemLabel: "Kit de reactivos asociado", quantity: 3, amount: null },
    ],
  },
  totalBudgetReconciled: false,
  // T1 does not publish a recognition-delta field on the wire — the real
  // adapter always maps this to not_available; never fabricated.
  recognitionDelta: { status: "not_available" },
  coverage: { status: "available", data: { documentsDiscovered: 11, documentsRead: 11, incompleteReasonCodes: [] } },
};

describe("LicitacionIntelCard", () => {
  it("renders eligibility, term facts with state, and item budget (found + honest not-itemized)", async () => {
    getLicitacionIntel.mockReset();
    getLicitacionIntel.mockResolvedValue(OPEN_TENDER_INTEL);
    render(<LicitacionIntelCard tenderCode="1057898-51-LP26" />);

    await waitFor(() => {
      screen.getByText("Abierto / público");
    });

    screen.getByText("Confirmado");
    screen.getByText("Centrífuga refrigerada");
    screen.getByText("No desglosado — incluido en el total");
  });

  it("omits the entire 'Reconocimiento por anexo' section when recognitionDelta.status is not_available — T1 does not publish that field yet, and this must not be misread as 'no annex evidence was read' (that's coverage's job)", async () => {
    getLicitacionIntel.mockReset();
    getLicitacionIntel.mockResolvedValue(OPEN_TENDER_INTEL);
    render(<LicitacionIntelCard tenderCode="1057898-51-LP26" />);

    await waitFor(() => {
      screen.getByText("Confirmado");
    });
    expect(screen.queryByText("Reconocimiento por anexo")).toBeNull();
    expect(screen.queryByText(/anexo/i, { selector: "h3" })).toBeNull();
  });

  it("renders the 'Reconocimiento por anexo' section when recognitionDelta is genuinely available", async () => {
    getLicitacionIntel.mockReset();
    getLicitacionIntel.mockResolvedValue({
      ...OPEN_TENDER_INTEL,
      recognitionDelta: {
        status: "available",
        data: {
          changeClass: "equipment_added",
          baselineCategories: ["balance"],
          augmentedCategories: ["balance", "centrifuge"],
          addedCategories: ["centrifuge"],
          confidenceBand: "high",
        },
      },
    } satisfies LicitacionIntel);
    render(<LicitacionIntelCard tenderCode="1057898-51-LP26" />);

    await waitFor(() => {
      screen.getByText("Reconocimiento por anexo");
    });
    screen.getByText("Anexo agregó equipo");
  });

  it("shows a not-available state for a tender with no intel yet", async () => {
    getLicitacionIntel.mockReset();
    getLicitacionIntel.mockResolvedValue(null);
    render(<LicitacionIntelCard tenderCode="unknown-tender" />);
    await waitFor(() => {
      screen.getByText(/Inteligencia de anexos no disponible/);
    });
  });

  it("shows the complete-coverage 'genuinely absent' claim only when coverage.status is available (is_complete)", async () => {
    getLicitacionIntel.mockReset();
    getLicitacionIntel.mockResolvedValue(OPEN_TENDER_INTEL);
    render(<LicitacionIntelCard tenderCode="1057898-51-LP26" />);

    await waitFor(() => {
      screen.getByTestId("coverage-complete-claim");
    });
    // "is_complete" only means every discovered document was processed — not
    // that every semantically-absent term is proven genuinely absent from
    // the text (deterministic pattern matching can simply fail to match).
    // The old wording overclaimed the latter; this asserts the honest
    // replacement, and that the old overclaiming phrase is gone.
    screen.getByText(/no fueron confirmados por los patrones soportados por T1/);
    expect(screen.queryByText(/genuinamente no está especificado/)).toBeNull();
    expect(screen.queryByTestId("coverage-incomplete-note")).toBeNull();
  });

  it("does not claim genuine absence of terms when coverage is available_incomplete — warns unread evidence may hold more", async () => {
    getLicitacionIntel.mockReset();
    getLicitacionIntel.mockResolvedValue({
      ...OPEN_TENDER_INTEL,
      coverage: {
        status: "available_incomplete",
        reasonCodes: ["attachment_download_incomplete"],
        partial: { documentsDiscovered: 11, documentsRead: 6, incompleteReasonCodes: ["attachment_download_incomplete"] },
      },
    } satisfies LicitacionIntel);
    render(<LicitacionIntelCard tenderCode="1057898-51-LP26" />);

    await waitFor(() => {
      screen.getByTestId("coverage-incomplete-note");
    });
    expect(screen.queryByTestId("coverage-complete-claim")).toBeNull();
    expect(screen.queryByText(/genuinamente no está especificado/)).toBeNull();
  });

  it("localizes the raw English wire unit tokens instead of showing them verbatim (8 meses, not 8 months)", async () => {
    getLicitacionIntel.mockReset();
    getLicitacionIntel.mockResolvedValue({
      ...OPEN_TENDER_INTEL,
      terms: {
        status: "available",
        data: [
          {
            fieldName: "contract_duration_months",
            label: "Duración del contrato",
            state: "explicit",
            value: 8,
            unit: "months",
          },
        ],
      },
    } satisfies LicitacionIntel);
    render(<LicitacionIntelCard tenderCode="1057898-51-LP26" />);

    await waitFor(() => {
      screen.getByText("8 meses");
    });
    expect(screen.queryByText(/8 months/)).toBeNull();
  });

  it("localizes 'percent' to '%' instead of showing it verbatim (5 %, not 5 percent)", async () => {
    getLicitacionIntel.mockReset();
    getLicitacionIntel.mockResolvedValue({
      ...OPEN_TENDER_INTEL,
      terms: {
        status: "available",
        data: [
          {
            fieldName: "faithful_performance_guarantee_percent",
            label: "Garantía de fiel cumplimiento",
            state: "explicit",
            value: 5,
            unit: "percent",
          },
        ],
      },
    } satisfies LicitacionIntel);
    render(<LicitacionIntelCard tenderCode="1057898-51-LP26" />);

    await waitFor(() => {
      screen.getByText("5 %");
    });
    expect(screen.queryByText(/5 percent/)).toBeNull();
  });

  it("localizes 'days' to 'días' for a plain (non-structured) day-count fact (90 días, not 90 days)", async () => {
    getLicitacionIntel.mockReset();
    getLicitacionIntel.mockResolvedValue({
      ...OPEN_TENDER_INTEL,
      terms: {
        status: "available",
        data: [
          {
            fieldName: "offer_validity_days",
            label: "Vigencia de la oferta",
            state: "explicit",
            value: 90,
            unit: "days",
          },
        ],
      },
    } satisfies LicitacionIntel);
    render(<LicitacionIntelCard tenderCode="1057898-51-LP26" />);

    await waitFor(() => {
      screen.getByText("90 días");
    });
    expect(screen.queryByText(/90 days/)).toBeNull();
  });

  it("renders a structured maximum_delivery_days value with day_basis 'business' as '30 días hábiles'", async () => {
    getLicitacionIntel.mockReset();
    getLicitacionIntel.mockResolvedValue({
      ...OPEN_TENDER_INTEL,
      terms: {
        status: "available",
        data: [
          {
            fieldName: "maximum_delivery_days",
            label: "Plazo máximo de entrega",
            state: "explicit",
            value: { days: 30, day_basis: "business" },
            unit: "days",
          },
        ],
      },
    } satisfies LicitacionIntel);
    render(<LicitacionIntelCard tenderCode="1057898-51-LP26" />);

    await waitFor(() => {
      screen.getByText("30 días hábiles");
    });
  });

  it("renders a structured maximum_delivery_days value with day_basis 'calendar' as '30 días corridos'", async () => {
    getLicitacionIntel.mockReset();
    getLicitacionIntel.mockResolvedValue({
      ...OPEN_TENDER_INTEL,
      terms: {
        status: "available",
        data: [
          {
            fieldName: "maximum_delivery_days",
            label: "Plazo máximo de entrega",
            state: "explicit",
            value: { days: 30, day_basis: "calendar" },
            unit: "days",
          },
        ],
      },
    } satisfies LicitacionIntel);
    render(<LicitacionIntelCard tenderCode="1057898-51-LP26" />);

    await waitFor(() => {
      screen.getByText("30 días corridos");
    });
  });

  it("never renders a stringified object ([object Object]) for a malformed/unrecognized structured value — fails closed to '—'", async () => {
    getLicitacionIntel.mockReset();
    getLicitacionIntel.mockResolvedValue({
      ...OPEN_TENDER_INTEL,
      terms: {
        status: "available",
        data: [
          {
            fieldName: "maximum_delivery_days",
            label: "Plazo máximo de entrega",
            state: "explicit",
            // An unrecognized day_basis (real extractor value the UI has no
            // confirmed phrasing for) must fail closed rather than guess.
            value: { days: 30, day_basis: "some_unrecognized_basis" },
            unit: "days",
          },
        ],
      },
    } satisfies LicitacionIntel);
    render(<LicitacionIntelCard tenderCode="1057898-51-LP26" />);

    await waitFor(() => {
      screen.getByText("Plazo máximo de entrega");
    });
    expect(screen.queryByText(/\[object Object\]/)).toBeNull();
    expect(screen.getByText("—")).toBeTruthy();
  });

  it("does not claim a total is known in the item-budget empty state when total_budget itself is unknown (e.g. Antofagasta case)", async () => {
    getLicitacionIntel.mockReset();
    getLicitacionIntel.mockResolvedValue({
      ...OPEN_TENDER_INTEL,
      terms: {
        status: "available",
        data: [
          {
            fieldName: "total_budget",
            label: "Presupuesto total",
            state: "unknown",
            value: null,
          },
        ],
      },
      itemBudget: { status: "available_empty" },
    } satisfies LicitacionIntel);
    render(<LicitacionIntelCard tenderCode="1057898-51-LP26" />);

    await waitFor(() => {
      screen.getByText("T1 no encontró un desglose de presupuesto por producto.");
    });
    expect(screen.queryByText(/solo se conoce el total de la licitación/)).toBeNull();
  });

  it("does claim a total is known in the item-budget empty state when total_budget is genuinely explicit/derived", async () => {
    getLicitacionIntel.mockReset();
    getLicitacionIntel.mockResolvedValue({
      ...OPEN_TENDER_INTEL,
      terms: {
        status: "available",
        data: [
          {
            fieldName: "total_budget",
            label: "Presupuesto total",
            state: "explicit",
            value: 12000000,
          },
        ],
      },
      itemBudget: { status: "available_empty" },
    } satisfies LicitacionIntel);
    render(<LicitacionIntelCard tenderCode="1057898-51-LP26" />);

    await waitFor(() => {
      screen.getByText(/solo se conoce el total de la licitación/);
    });
    expect(screen.queryByText("T1 no encontró un desglose de presupuesto por producto.")).toBeNull();
  });
});
