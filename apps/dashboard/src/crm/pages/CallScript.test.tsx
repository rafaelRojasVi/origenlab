import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { OpportunityCardData } from "../crmTypes";
import type { SegRow } from "../seguimiento";
import { CallScript } from "./CallScript";

// Every value below is invented; the repository is public.
const NOW = new Date("2026-11-18T15:00:00Z");

function row(card: Partial<OpportunityCardData>, over: Partial<SegRow> = {}): SegRow {
  return {
    card: {
      opportunity_id: "o1",
      title: "Cotización CN09876",
      organization: { organization_id: "org", name: "Instituto Ficticio", confirmation: "confirmed", version: 1 },
      contact: { source: "crm_participant", name: "Persona Uno <persona@ejemplo.invalid>", address: null, others: 0 },
      quote_numbers: ["CN09876"],
      latest_revision: null,
      quotes: [],
      ...card,
    } as OpportunityCardData,
    sentAt: "2026-11-10T15:00:00Z",
    ...over,
  } as SegRow;
}

describe("CallScript", () => {
  it("fills contact, quote, date and who calls", () => {
    render(<CallScript row={row({})} me="Rafa" now={NOW} onClose={() => undefined} />);
    expect(screen.getByTestId("guion-say")).toHaveTextContent(
      "Hola, ¿hablo con Persona Uno? Le habla Rafa, de ventas de OrigenLab. Le llamo por la cotización CN09876 de los equipos que le enviamos el 10 nov. ¿Alcanzó a revisarla? ¿Cómo va eso?",
    );
    expect(screen.getByText(/«Está caro»/)).toBeInTheDocument();
  });

  it("asks who answers when the case has no contact name", () => {
    render(<CallScript row={row({ contact: null })} me="Rafa" now={NOW} onClose={() => undefined} />);
    expect(screen.getByTestId("guion-say")).toHaveTextContent("Hola, ¿con quién hablo?");
  });

  it("names the technical contact from the profile, else the team", () => {
    const { unmount } = render(<CallScript row={row({})} me="Rafa" now={NOW} technicalName="Persona Técnica" onClose={() => undefined} />);
    expect(screen.getByText(/revisar opciones con Persona Técnica\./)).toBeInTheDocument();
    expect(screen.getByText(/Persona Técnica le responde hoy/)).toBeInTheDocument();
    unmount();
    render(<CallScript row={row({})} me="Rafa" now={NOW} onClose={() => undefined} />);
    expect(screen.getByText(/con el equipo técnico\./)).toBeInTheDocument();
    expect(screen.queryByText(/Tatiana/)).toBeNull();
  });
});
