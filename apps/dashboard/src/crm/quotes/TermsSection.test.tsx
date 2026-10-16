/**
 * «Condiciones»: the fixed conditions of the quote, the note for the client and any extra ones.
 * Every value is invented.
 */
import "@testing-library/jest-dom";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { draftProblems } from "./draftEditor";
import type { QuoteDraft } from "./quoteApi";
import { draftFixture } from "./quoteFixtures";
import { TermsSection } from "./TermsSection";

function setup(initial: QuoteDraft = draftFixture(), disabled = false) {
  const latest = { draft: initial };
  function Harness() {
    const [d, setD] = useState(initial);
    latest.draft = d;
    return <TermsSection draft={d} invalid={draftProblems(d)} disabled={disabled} edit={(fn) => setD((prev) => fn(prev))} />;
  }
  render(<Harness />);
  return latest;
}

const withTerms = (terms: Partial<QuoteDraft["terms"]>): QuoteDraft => ({
  ...draftFixture(),
  terms: { terms_extra: [], ...terms },
});

describe("TermsSection", () => {
  it("shows every fixed condition, labelled, with what the draft holds", () => {
    setup(withTerms({ delivery_terms: "6 a 8 semanas", validity_days: 30, client_notes_es: "Incluye capacitación." }));
    expect(screen.getByRole("heading", { level: 2, name: "Condiciones" })).toBeInTheDocument();
    expect(screen.getByLabelText("Plazo de entrega")).toHaveValue("6 a 8 semanas");
    expect(screen.getByLabelText("Validez (días)")).toHaveValue("30");
    for (const label of ["Forma de pago", "Garantía", "Lugar de entrega", "Instalación y capacitación"]) {
      expect(screen.getByLabelText(label)).toHaveValue("");
    }
    expect(screen.getByLabelText("Nota para el cliente (sale en el PDF)")).toHaveValue("Incluye capacitación.");
  });

  it("edits the conditions; an emptied one goes back to none", () => {
    const latest = setup(withTerms({ payment_terms: "Contado" }));
    fireEvent.change(screen.getByLabelText("Plazo de entrega"), { target: { value: "4 semanas" } });
    fireEvent.change(screen.getByLabelText("Nota para el cliente (sale en el PDF)"), { target: { value: "Gracias." } });
    fireEvent.change(screen.getByLabelText("Forma de pago"), { target: { value: "" } });
    expect(latest.draft.terms).toMatchObject({ delivery_terms: "4 semanas", client_notes_es: "Gracias.", payment_terms: null });
  });

  it("reads the validity as whole days, and says when it is not", () => {
    const latest = setup();
    const days = screen.getByLabelText("Validez (días)");
    fireEvent.change(days, { target: { value: "45" } });
    expect(latest.draft.terms.validity_days).toBe(45);
    fireEvent.change(days, { target: { value: "400" } });
    expect(screen.getByText("La validez debe ser de 1 a 365 días.")).toBeInTheDocument();
    fireEvent.change(days, { target: { value: "3x" } });
    expect(days).toHaveValue("3x");
    expect(screen.getByText("Escribe la validez en días, con un número entero.")).toBeInTheDocument();
    fireEvent.change(days, { target: { value: "" } });
    expect(latest.draft.terms.validity_days).toBeNull();
  });

  it("marks a bracketed placeholder to be completed before approving", () => {
    setup(withTerms({ payment_terms: "[CONDICIÓN DE PAGO]", warranty_terms_es: "12 meses" }));
    const pay = screen.getByLabelText("Forma de pago");
    expect(pay).toHaveAccessibleDescription("Completa este texto antes de aprobar.");
    expect(screen.getByLabelText("Garantía")).not.toHaveAccessibleDescription();
  });

  it("adds, edits and removes an extra condition, asking for what is missing", () => {
    const latest = setup();
    fireEvent.click(screen.getByRole("button", { name: "+ Agregar otra condición" }));
    const row = screen.getByLabelText("Nombre de la condición 1").closest("li") as HTMLElement;
    expect(within(row).getByText("Escribe el nombre de la condición.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Nombre de la condición 1"), { target: { value: "Embalaje" } });
    fireEvent.change(screen.getByLabelText("Texto de la condición 1"), { target: { value: "Caja de madera" } });
    expect(latest.draft.terms.terms_extra).toEqual([expect.objectContaining({ label_es: "Embalaje", text_es: "Caja de madera" })]);
    expect(within(row).queryByText(/Escribe/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Quitar la condición 1" }));
    expect(latest.draft.terms.terms_extra).toEqual([]);
  });

  it("shows a text that is too long next to its field", () => {
    setup(withTerms({ delivery_place_es: "x".repeat(1001) }));
    expect(screen.getByLabelText("Lugar de entrega")).toHaveAccessibleDescription("Este texto es demasiado largo (máximo 1000).");
  });

  it("is read-only when the draft cannot be edited", () => {
    setup(withTerms({ terms_extra: [{ label_es: "Embalaje", text_es: "Caja" }] }), true);
    expect(screen.getByLabelText("Plazo de entrega")).toBeDisabled();
    expect(screen.getByLabelText("Texto de la condición 1")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "+ Agregar otra condición" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Quitar la condición 1" })).not.toBeInTheDocument();
  });
});
