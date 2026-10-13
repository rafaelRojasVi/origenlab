import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";

vi.mock("../crmApi", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  fetchCaseMailDocuments: vi.fn(async () => ({
    opportunity_id: "caso-1",
    messages: [{ source_record_id: "s-1", subject: null, sent_at: "2026-10-13T14:04:00+00:00",
      documents: [{ sha256: "a".repeat(64), filename: "CN09876-Instituto Ficticio (IF).pdf", cn_tokens: ["CN09876"], recorded: null }] }],
  })),
}));

import { CompleteFromMail } from "./CompleteFromMail";

const card = { opportunity_id: "caso-1", version: 3, stage: "lead", closed_at: null, quotes: [] } as never;

function renderIt(onChanged = vi.fn()) {
  const session = { kind: "signed_in", method: "google_session", caseCommandsEnabled: true,
    operator: { operatorId: "op-1", email: "op@ejemplo.invalid", displayName: "Operadora", role: "sales" } } as AuthSessionState;
  return render(
    <AuthSessionContext.Provider value={{ session, signOut: async () => true }}>
      <CompleteFromMail card={card} onChanged={onChanged} />
    </AuthSessionContext.Provider>,
  );
}

describe("Completar con la cotización del correo · case panel", () => {
  it("offers it when the case has a sent quote PDF and no quote, prefilled from the PDF name", async () => {
    renderIt();
    fireEvent.click(await screen.findByRole("button", { name: "Completar con la cotización 09876-26" }));
    expect(screen.getByLabelText("Institución")).toHaveValue("Instituto Ficticio (IF)");
  });

  it("is not offered on a case that already has a quote", async () => {
    const session = { kind: "signed_in", method: "google_session", caseCommandsEnabled: true,
      operator: { operatorId: "op-1", email: "op@ejemplo.invalid", displayName: "Operadora", role: "sales" } } as AuthSessionState;
    render(
      <AuthSessionContext.Provider value={{ session, signOut: async () => true }}>
        <CompleteFromMail card={{ ...(card as object), quotes: [{}] } as never} onChanged={vi.fn()} />
      </AuthSessionContext.Provider>,
    );
    await waitFor(() => expect(screen.queryByRole("button", { name: /Completar con/ })).not.toBeInTheDocument());
  });
});
