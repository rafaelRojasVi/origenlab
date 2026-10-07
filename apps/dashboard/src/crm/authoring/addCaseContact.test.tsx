/**
 * «Agregar como persona del CRM»: a case's quote recipient becomes a participant of the case —
 * linking the CRM person who already holds the address, or creating that person first.
 */
import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AddCaseContactDialog } from "./AddCaseContactDialog";

const CASE = "c998cff2-d1c7-4a16-a2c5-3bb7ea80d899";
const UNI = "11111111-1111-4111-8111-111111111111";

function stub(personExists: boolean) {
  const calls: { path: string; body: Record<string, unknown> }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, "http://localhost");
      const body = init?.body ? JSON.parse(String(init.body)) : {};
      calls.push({ path: url.pathname, body });
      const json = (b: unknown, status = 200) =>
        Promise.resolve(new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
      if (url.pathname === "/v2/commands/add-case-participant" && body.email && !personExists) {
        return json({ detail: { code: "no_crm_person_for_address", message: "create the person first" } }, 404);
      }
      return json({ ok: true, replayed: false, idempotency_key: "k", command_receipt_id: "r", person_id: "p-new", version: 1 });
    }),
  );
  return calls;
}

function renderDialog(onDone = vi.fn()) {
  render(
    <AddCaseContactDialog
      opportunityId={CASE}
      opportunityVersion={4}
      address="Persona Ejemplo <persona@ejemplo.invalid>"
      organization={{ organization_id: UNI, name: "Universidad Ficticia" }}
      quoteNumber="011024AII-26"
      onDone={onDone}
      onCancel={() => undefined}
    />,
  );
  return onDone;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("AddCaseContactDialog", () => {
  it("links the CRM person who already holds the address, and creates nothing", async () => {
    const calls = stub(true);
    const onDone = renderDialog();
    fireEvent.click(screen.getByRole("button", { name: "Agregar al caso" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledWith({ created: false, name: "Persona Ejemplo" }));
    expect(calls).toHaveLength(1);
    expect(calls[0]).toMatchObject({
      path: "/v2/commands/add-case-participant",
      body: { opportunity_id: CASE, opportunity_version: 4, email: "persona@ejemplo.invalid", role: "quote_recipient" },
    });
  });

  it("creates the person with the address and the case's institution, then links them by id", async () => {
    const calls = stub(false);
    const onDone = renderDialog();
    fireEvent.click(screen.getByRole("button", { name: "Agregar al caso" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledWith({ created: true, name: "Persona Ejemplo" }));
    expect(calls.map((c) => c.path)).toEqual([
      "/v2/commands/add-case-participant",
      "/v2/commands/create-person",
      "/v2/commands/add-case-participant",
    ]);
    expect(calls[1].body).toMatchObject({
      display_name: "Persona Ejemplo", email: "persona@ejemplo.invalid", organization_id: UNI,
    });
    expect(calls[2].body).toMatchObject({ person_id: "p-new", opportunity_id: CASE });
    expect(calls[2].body).not.toHaveProperty("email");
  });
});
