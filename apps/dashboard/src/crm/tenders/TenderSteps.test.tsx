import "@testing-library/jest-dom";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { TenderPageData } from "./tenderApi";
import { splitList } from "./TenderReading";
import { TenderSteps, tenderSteps } from "./TenderSteps";

const prep = (phase: "read" | "fill", status: string, failure_code: string | null = null) => ({
  id: `${phase}-1`, phase, status, failure_code, result: {}, outputs: [],
});

function data(over: Record<string, unknown> = {}): TenderPageData {
  return {
    tender: { id: "t1", status: "preparando" },
    annexes: [{ document_id: "d1", filename: "bases.pdf" }],
    preparations: { read: null, fill: null },
    offer_quote: null,
    spend: { cap_usd: "60", reached: false },
    bidder_ready: false,
    ...over,
  } as unknown as TenderPageData;
}

const states = (d: TenderPageData) => tenderSteps(d).map((s) => s.state);

describe("tenderSteps", () => {
  it("with nothing uploaded, uploading is next and the rest wait", () => {
    expect(states(data({ annexes: [] }))).toEqual(["ahora", "pendiente", "pendiente", "pendiente", "pendiente", "pendiente"]);
  });

  it("with the anexos up, reading them is next", () => {
    expect(states(data())).toEqual(["hecho", "ahora", "pendiente", "pendiente", "pendiente", "pendiente"]);
  });

  it("a read under way is «en curso», not something to do", () => {
    expect(states(data({ preparations: { read: prep("read", "running"), fill: null } }))[1]).toBe("en_curso");
  });

  it("a failed read is «error» and points to its step instead of repeating the reason", () => {
    const steps = tenderSteps(data({ preparations: { read: prep("read", "failed", "request_failed"), fill: null } }));
    expect(steps[1].state).toBe("error");
    expect(steps[1].detail).toBe("No se pudo; mira el paso");
  });

  it("a quote not yet approved is still the next step: approving it is the operator's", () => {
    const d = data({
      preparations: { read: prep("read", "ready"), fill: null },
      offer_quote: { quote_id: "q", number: "01300-26", status: "draft" },
    });
    const oferta = tenderSteps(d)[2];
    expect(oferta.state).toBe("ahora");
    expect(oferta.detail).toBe("Cotización 01300-26: falta aprobar");
  });

  it("everything ready and marked «Postulada» is six of six", () => {
    const d = data({
      tender: { id: "t1", status: "postulada" },
      preparations: { read: prep("read", "ready"), fill: prep("fill", "ready") },
      offer_quote: { quote_id: "q", number: "01300-26", status: "approved" },
      bidder_ready: true,
    });
    expect(states(d).every((s) => s === "hecho")).toBe(true);
  });
});

describe("TenderSteps", () => {
  it("says the next step, links the bidder's data to Preferencias and counts what is done", () => {
    const d = data({
      preparations: { read: prep("read", "ready"), fill: null },
      offer_quote: { quote_id: "q", number: "01300-26", status: "approved" },
    });
    render(<TenderSteps steps={tenderSteps(d)} />);
    const nav = screen.getByRole("navigation", { name: "Pasos de la licitación" });
    expect(within(nav).getByText("3 de 6 listos")).toBeInTheDocument();
    expect(within(nav).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "3");
    expect(within(nav).getByText("Siguiente paso")).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Ir a Preferencias →" })).toHaveAttribute("href", "#/crm/cotizador/preferencias");
  });
});

describe("splitList", () => {
  it("turns the read's «Lead. - A - B» habit into a lead and bullets", () => {
    expect(splitList("Solo sobre 1.000 UTM. - Monto: 5% - Forma: boleta")).toEqual({
      lead: "Solo sobre 1.000 UTM.",
      bullets: ["Monto: 5%", "Forma: boleta"],
    });
    expect(splitList("- Precio: 40% - Plazo: 15%")).toEqual({ lead: "", bullets: ["Precio: 40%", "Plazo: 15%"] });
  });

  it("lists three or more «; »-separated parts, as the quantities come", () => {
    expect(splitList("L1 Friabilómetro: 1; L2 Test de Disolución: 1; L3 Balanzas: 10.")).toEqual({
      lead: "",
      bullets: ["L1 Friabilómetro: 1", "L2 Test de Disolución: 1", "L3 Balanzas: 10"],
    });
    expect(splitList("Rango 20–100 rpm; Control: Sí")).toEqual({ lead: "Rango 20–100 rpm; Control: Sí", bullets: [] });
  });

  it("leaves ranges, hyphenated words and a single dash alone", () => {
    for (const text of ["UV-Vis, 190 - 1100 nm", "Rango 20–100 rpm", "Dato - Otro"]) {
      expect(splitList(text)).toEqual({ lead: text, bullets: [] });
    }
  });
});
