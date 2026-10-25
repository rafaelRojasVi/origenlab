import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { SubjectMemory } from "../crmApi";
import { CaseMemoryCard } from "./CaseMemory";

const MEM: SubjectMemory = {
  summary: "El cliente pidió una balanza y espera la cotización.",
  facts: [
    { k: "institution", v: "Instituto Ficticio" },
    { k: "person", v: "Ana Demo, responde a la cotización CN09876" },
    { k: "other", v: "Necesita entrega antes de fin de mes" },
    { k: "product", v: "Balanza analítica 220 g" },
  ],
  open_questions: ["¿Cuál es el presupuesto?"],
  next_step: { action: "responder", why: "Hay que confirmar el modelo.", confidence: "high" },
  version: 1,
  model: "claude-test",
  written_at: "2026-10-20T14:00:00Z",
};

describe("CaseMemoryCard", () => {
  it("leads with the next step, the summary and the open questions", () => {
    render(<CaseMemoryCard mem={MEM} />);
    expect(screen.getByTestId("case-memory-next-step").textContent).toContain("La IA sugiere: Responder");
    expect(screen.getByTestId("case-memory-summary").textContent).toContain("espera la cotización");
    expect(screen.getByText("¿Cuál es el presupuesto?")).toBeTruthy();
    expect(screen.getByText(/Leído por IA/)).toBeTruthy();
  });

  it("folds the facts away, grouped, without repeating the institution", () => {
    render(<CaseMemoryCard mem={MEM} />);
    expect(screen.queryByTestId("case-memory-facts")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Ver los 3 datos" }));
    const facts = screen.getByTestId("case-memory-facts");
    expect(facts.textContent).toContain("Personas");
    expect(facts.textContent).toContain("Detalles");
    expect(facts.textContent).not.toContain("Instituto Ficticio");
    expect(facts.textContent!.indexOf("Productos")).toBeLessThan(facts.textContent!.indexOf("Detalles"));
  });

  it("shows no next step when the model says there is nothing to do", () => {
    render(<CaseMemoryCard mem={{ ...MEM, next_step: { action: "nada", why: "", confidence: "low" } }} />);
    expect(screen.queryByTestId("case-memory-next-step")).toBeNull();
  });
});
