/**
 * «People first» on the contact drawer: other names, where each fact was learned (with «Quitar» and
 * «Confirmar» on what the machine proposed) and the «¿Es …?» guesses with «Sí» / «No».
 * Every name and address is invented.
 */
import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import { clearResourceCache } from "../useResource";
import { PersonDrawer } from "./PersonDrawer";

const PERSON = "0a000000-0000-4000-8000-0000000000a1";
const RECEIPT = "00000000-0000-4000-8000-0000000000c1";
const GUESS = "00000000-0000-4000-8000-0000000000d1:" + PERSON;

function body(over: Record<string, unknown> = {}) {
  return {
    person: { id: PERSON, display_name: "Pedro Soto", given_name: "Pedro", family_name: "Soto", title: null,
      status: "active", archived_at: null, archive_reason: null, confirmation: "operator_confirmed", version: 3,
      created_at: null, updated_at: null, merged_into_person_id: null },
    contact_points: [], affiliations: [], notes: [],
    references: { campaign_recipients: 0, opportunity_participants: 0, quotes: 0, evidence_assertions: 0, notes: 0 },
    removal: { allowed: false, reasons: [] },
    authoring: { enabled: true, may_author: true, may_archive: true },
    other_names: [{ id: "n1", name: "P. Soto Díaz", source: "signature", confirmation: "machine_proposed", machine: true }],
    provenance: [
      { kind: "name", id: "n1", label: "P. Soto Díaz", source_es: "firma del correo 3 oct 2026", machine: true, receipt_id: RECEIPT },
      { kind: "address", id: "a1", label: "psoto@ficticia.example.invalid", source_es: "lo propuso el sistema", machine: true, receipt_id: null },
      { kind: "affiliation", id: "f1", label: "Universidad Ficticia", source_es: "lo anotó una persona", machine: false, receipt_id: null },
    ],
    guesses: [{ guess_id: GUESS, address: "p.soto@otra.example.invalid", person_id: PERSON, person_name: "Pedro Soto",
      organization_name: "Universidad Ficticia", reasons: ["mismo apellido"], question_es: "¿Es Pedro Soto (Universidad Ficticia)?" }],
    ...over,
  };
}

const posts: { path: string; body: Record<string, unknown>; key: string | null }[] = [];
let failPath: string | null = null;
function stub(data = body()) {
  posts.length = 0;
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const path = new URL(url, "http://localhost").pathname;
    const json = (b: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(b), { status,
      headers: { "Content-Type": "application/json" } }));
    if (init?.method === "POST") {
      posts.push({ path, body: JSON.parse(String(init.body)), key: new Headers(init.headers).get("Idempotency-Key") });
      if (failPath === path) {
        return json({ error: { code: "conflict", message: "x", details: { code: "changed_since",
          message: "Alguien cambió este contacto después; deshazlo desde la ficha" } } }, 409);
      }
      return json({ replayed: false });
    }
    if (path === `/v2/workspace/people/${PERSON}`) return json(data);
    return Promise.resolve(new Response("{}", { status: 404 }));
  }));
}

function session(role = "sales"): AuthSessionState {
  return { kind: "signed_in", method: "google_profile",
    operator: { operatorId: "op", email: "v@example.cl", displayName: "Ventas", role },
    caseCommandsEnabled: true, crmAuthoringEnabled: true } as AuthSessionState;
}

function renderDrawer(mayAuthor = true) {
  render(<AuthSessionContext.Provider value={{ session: session(), signOut: async () => true }}>
    <PersonDrawer personId={PERSON} onClose={() => {}} mayAuthor={mayAuthor} />
  </AuthSessionContext.Provider>);
}

afterEach(() => {
  vi.unstubAllGlobals();
  clearResourceCache();
  failPath = null;
});

describe("PersonDrawer · people first", () => {
  it("lists other names and where each fact was learned, whatever the source says", async () => {
    stub();
    renderDrawer();
    const names = await screen.findByTestId("other-names");
    expect(names).toHaveTextContent("También conocido como");
    expect(names).toHaveTextContent("P. Soto Díaz");
    const rows = screen.getAllByTestId("provenance-row");
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent("firma del correo 3 oct 2026");
    expect(rows[1]).toHaveTextContent("lo propuso el sistema");
    expect(rows[2]).toHaveTextContent("lo anotó una persona");
    expect(within(rows[2]).queryByRole("button", { name: "Quitar" })).toBeNull();
    expect(within(rows[2]).queryByRole("button", { name: "Confirmar" })).toBeNull();
    expect(within(rows[1]).queryByRole("button", { name: "Quitar" })).toBeNull();
    expect(within(rows[1]).getByRole("button", { name: "Confirmar" })).toBeInTheDocument();
  });

  it("«Quitar» on a machine item undoes its receipt", async () => {
    stub();
    renderDrawer();
    const [first] = await screen.findAllByTestId("provenance-row");
    fireEvent.click(within(first).getByRole("button", { name: "Quitar" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].path).toBe("/v2/commands/remove-automatic-action");
    expect(posts[0].body).toEqual({ receipt_id: RECEIPT });
    expect(posts[0].key).toBeTruthy();
  });

  it("shows the API's Spanish message when «Quitar» is refused as changed since", async () => {
    stub();
    failPath = "/v2/commands/remove-automatic-action";
    renderDrawer();
    const [first] = await screen.findAllByTestId("provenance-row");
    fireEvent.click(within(first).getByRole("button", { name: "Quitar" }));
    expect(await screen.findByText(/Alguien cambió este contacto después/)).toBeInTheDocument();
  });

  it("«Confirmar» confirms the fact with its kind and id", async () => {
    stub();
    renderDrawer();
    const rows = await screen.findAllByTestId("provenance-row");
    fireEvent.click(within(rows[1]).getByRole("button", { name: "Confirmar" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].path).toBe("/v2/commands/confirm-person-fact");
    expect(posts[0].body).toMatchObject({ kind: "address", id: "a1" });
    expect(posts[0].key).toBeTruthy();
  });

  it("a guess asks «¿Es …?» and «Sí» posts the answer", async () => {
    stub();
    renderDrawer();
    const guess = await screen.findByTestId("person-guess");
    expect(guess).toHaveTextContent("¿Es Pedro Soto (Universidad Ficticia)?");
    fireEvent.click(within(guess).getByRole("button", { name: "Sí" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].path).toBe("/v2/commands/answer-person-guess");
    expect(posts[0].body).toMatchObject({ guess_id: GUESS, answer: "si" });
    expect(posts[0].key).toBeTruthy();
  });

  it("«No» posts answer no; without authoring there are no buttons", async () => {
    stub();
    renderDrawer();
    fireEvent.click(within(await screen.findByTestId("person-guess")).getByRole("button", { name: "No" }));
    await waitFor(() => expect(posts[0]?.body).toMatchObject({ answer: "no" }));
  });

  it("an older API without the new fields renders the drawer as before", async () => {
    const old = body();
    delete (old as Record<string, unknown>).other_names;
    delete (old as Record<string, unknown>).provenance;
    delete (old as Record<string, unknown>).guesses;
    stub(old as ReturnType<typeof body>);
    renderDrawer();
    expect(await screen.findByText("Datos")).toBeInTheDocument();
    expect(screen.queryByTestId("other-names")).toBeNull();
    expect(screen.queryByTestId("person-guess")).toBeNull();
  });

  it("a viewer sees the guess but no buttons", async () => {
    stub();
    renderDrawer(false);
    const rows = await screen.findAllByTestId("provenance-row");
    expect(within(rows[0]).queryByRole("button", { name: "Quitar" })).toBeNull();
    expect(within(screen.getByTestId("person-guess")).queryByRole("button", { name: "Sí" })).toBeNull();
  });
});
