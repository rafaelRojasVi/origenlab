/** «Cliente / Para / Lo que pidió». Every value is invented. */
import "@testing-library/jest-dom";
import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { ClientSection } from "./ClientSection";
import { draftProblems } from "./draftEditor";
import type { QuoteDraft, QuoteWorkspace } from "./quoteApi";
import { draftFixture, workspaceFixture } from "./quoteFixtures";

function ws(over: Partial<QuoteWorkspace["case"]> = {}): QuoteWorkspace {
  const base = workspaceFixture();
  return {
    ...base,
    case: {
      ...base.case,
      contacts: [
        { participant_id: "pa-1", person_id: null, contact_point_id: null, name: "Ana Prueba", email: "ana@ejemplo.cl", source: "participant" },
        { participant_id: null, person_id: "pe-2", contact_point_id: null, name: null, email: "compras@ejemplo.cl", source: "thread" },
      ],
      ...over,
    },
  };
}

function setup(workspace = ws(), initial: QuoteDraft = draftFixture(), disabled = false) {
  const latest: { draft: QuoteDraft } = { draft: initial };
  function Harness() {
    const [draft, setDraft] = useState(initial);
    latest.draft = draft;
    return (
      <ClientSection
        workspace={workspace}
        draft={draft}
        invalid={draftProblems(draft)}
        disabled={disabled}
        edit={(fn) => setDraft((d) => fn(d))}
      />
    );
  }
  render(<Harness />);
  return latest;
}

describe("ClientSection", () => {
  it("shows the institution and says when its RUT is missing", () => {
    setup();
    expect(screen.getByText("Institución de prueba")).toBeInTheDocument();
    expect(screen.getByText("RUT pendiente")).toBeInTheDocument();
  });

  it("shows the RUT when the institution has one", () => {
    setup(ws({ institution: { id: "i-1", name: "Institución de prueba", rut: "11.111.111-1" } }));
    expect(screen.getByText("RUT 11.111.111-1")).toBeInTheDocument();
  });

  it("lists the case's contacts and sends exactly one id as the pick", () => {
    const latest = setup();
    const select = screen.getByLabelText("Para");
    expect(screen.getByRole("option", { name: "Ana Prueba <ana@ejemplo.cl>" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "compras@ejemplo.cl" })).toBeInTheDocument();
    fireEvent.change(select, { target: { value: "1" } });
    expect(latest.draft.recipient).toEqual({ person_id: "pe-2" });
    fireEvent.change(select, { target: { value: "0" } });
    expect(latest.draft.recipient).toEqual({ participant_id: "pa-1" });
  });

  it("selects the saved recipient", () => {
    setup(ws(), { ...draftFixture(), recipient: { participant_id: "pa-1" } });
    expect(screen.getByLabelText("Para")).toHaveValue("0");
  });

  it("takes another address with an optional name, and says when it is not valid", () => {
    const latest = setup();
    fireEvent.change(screen.getByLabelText("Para"), { target: { value: "other" } });
    const email = screen.getByLabelText("Correo");
    fireEvent.change(email, { target: { value: "otra@" } });
    expect(latest.draft.recipient).toEqual({ email: "otra@", name: null });
    expect(screen.getByText("El correo del destinatario no es válido.")).toBeInTheDocument();
    fireEvent.change(email, { target: { value: "otra@ejemplo.cl" } });
    fireEvent.change(screen.getByLabelText("Nombre (opcional)"), { target: { value: "Otra Persona" } });
    expect(latest.draft.recipient).toEqual({ email: "otra@ejemplo.cl", name: "Otra Persona" });
    expect(screen.queryByText("El correo del destinatario no es válido.")).not.toBeInTheDocument();
  });

  it("opens a typed address that is not a contact as «Otro correo…»", () => {
    setup(ws(), { ...draftFixture(), recipient: { email: "suelto@ejemplo.cl", name: "Suelto" } });
    expect(screen.getByLabelText("Para")).toHaveValue("other");
    expect(screen.getByLabelText("Correo")).toHaveValue("suelto@ejemplo.cl");
  });

  it("clears the recipient", () => {
    const latest = setup(ws(), { ...draftFixture(), recipient: { participant_id: "pa-1" } });
    fireEvent.change(screen.getByLabelText("Para"), { target: { value: "" } });
    expect(latest.draft.recipient).toBeNull();
  });

  it("shows what the client asked for, and nothing when there is no text", () => {
    setup(ws({ lo_que_pidio: "Un agitador para el laboratorio de prueba." }));
    expect(screen.getByText("Lo que pidió")).toBeInTheDocument();
    expect(screen.getByText("Un agitador para el laboratorio de prueba.")).toBeInTheDocument();
  });

  it("hides «Lo que pidió» when the case has none", () => {
    setup();
    expect(screen.queryByText("Lo que pidió")).not.toBeInTheDocument();
  });

  it("locks the pick while the editor is frozen", () => {
    setup(ws(), draftFixture(), true);
    expect(screen.getByLabelText("Para")).toBeDisabled();
  });
});
