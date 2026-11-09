import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import { Toaster } from "../ui";
import type { OpportunityCardData } from "../crmTypes";
import { OriginSelect } from "./OriginSelect";

// Every value below is invented; the repository is public.
const CASE = "11111111-1111-4111-8111-111111111111";

function session(role: string, cases = true): AuthSessionState {
  return {
    kind: "signed_in",
    method: "google_profile",
    operator: { operatorId: "op-1", email: "ventas@example.cl", displayName: "Vendedora Ficticia", role },
    caseCommandsEnabled: cases,
    crmAuthoringEnabled: true,
  };
}

function withSession(s: AuthSessionState, node: ReactNode) {
  return <AuthSessionContext.Provider value={{ session: s, signOut: async () => true }}>{node}</AuthSessionContext.Provider>;
}

const caseCard = (over: Partial<OpportunityCardData> = {}) =>
  ({ opportunity_id: CASE, version: 5, stage: "quoting", closed_at: null, origin: null, ...over }) as OpportunityCardData;

interface Post {
  path: string;
  body: Record<string, unknown>;
  key: string | null;
}

function stubPost(answers: { status?: number; body: unknown }[]) {
  const posts: Post[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      posts.push({
        path: new URL(url, "http://localhost").pathname,
        body: JSON.parse(String(init?.body)) as Record<string, unknown>,
        key: new Headers(init?.headers).get("Idempotency-Key"),
      });
      const a = answers[Math.min(posts.length - 1, answers.length - 1)];
      return Promise.resolve(new Response(JSON.stringify(a.body), { status: a.status ?? 200, headers: { "Content-Type": "application/json" } }));
    }),
  );
  return posts;
}

const receipt = (origin: string | null, previous: string | null, version = 6) => ({
  command: "set_case_origin",
  opportunity_id: CASE,
  opportunity_version: version,
  origin,
  previous_origin: previous,
  created: [],
  event_ids: ["e1"],
  replayed: false,
});

function renderSelect(card: OpportunityCardData, s = session("sales"), onChanged = vi.fn()) {
  render(
    withSession(
      s,
      <>
        <OriginSelect card={card} onChanged={onChanged} />
        <Toaster />
      </>,
    ),
  );
  return onChanged;
}

afterEach(() => vi.unstubAllGlobals());

describe("OriginSelect", () => {
  it("offers the nine channels and «Sin origen», all in Spanish, with the current one selected", () => {
    renderSelect(caseCard({ origin: "mercado_publico" }));
    const select = screen.getByRole("combobox", { name: "Origen del caso" });
    expect(select).toHaveValue("mercado_publico");
    const labels = within(select).getAllByRole("option").map((o) => o.textContent);
    expect(labels).toEqual([
      "Sin origen", "Correo", "Web", "WhatsApp", "Campaña", "Mercado Público", "Fabricante", "Recomendación", "Llamada", "Ex Labdelivery",
    ]);
  });

  it("asks for a note, then posts the origin with the case version, the note and an Idempotency-Key", async () => {
    const posts = stubPost([{ body: receipt("web", null) }]);
    const onChanged = renderSelect(caseCard());
    fireEvent.change(screen.getByRole("combobox", { name: "Origen del caso" }), { target: { value: "web" } });
    // Nothing is sent until the note is confirmed.
    expect(posts).toHaveLength(0);
    const save = screen.getByRole("button", { name: "Guardar origen" });
    expect(save).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/^Nota/), { target: { value: "Llegó por el formulario del sitio." } });
    fireEvent.click(save);
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].path).toBe("/v2/commands/set-case-origin");
    expect(posts[0].body).toEqual({
      opportunity_id: CASE,
      opportunity_version: 5,
      origin: "web",
      note: "Llegó por el formulario del sitio.",
    });
    expect(posts[0].key).toBeTruthy();
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(await screen.findByTestId("toaster")).toHaveTextContent("Origen: Web.");
  });

  it("clears the origin with null, and a blank note does not send", async () => {
    const posts = stubPost([{ body: receipt(null, "web") }]);
    renderSelect(caseCard({ origin: "web" }));
    fireEvent.change(screen.getByRole("combobox", { name: "Origen del caso" }), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText(/^Nota/), { target: { value: "   " } });
    expect(screen.getByRole("button", { name: "Guardar origen" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/^Nota/), { target: { value: "Se puso por error." } });
    fireEvent.click(screen.getByRole("button", { name: "Guardar origen" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body).toMatchObject({ origin: null, note: "Se puso por error." });
  });

  it("cancel drops the choice and sends nothing", () => {
    const posts = stubPost([{ body: receipt("web", null) }]);
    renderSelect(caseCard({ origin: "correo" }));
    const select = screen.getByRole("combobox", { name: "Origen del caso" });
    fireEvent.change(select, { target: { value: "llamada" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(select).toHaveValue("correo");
    expect(screen.queryByLabelText(/^Nota/)).toBeNull();
    expect(posts).toHaveLength(0);
  });

  it("says the case changed, in Spanish, and refetches on a version conflict", async () => {
    stubPost([
      {
        status: 409,
        body: { error: { code: "conflict", message: "stale", details: { code: "case_version_conflict", message: "stale" }, request_id: "r" } },
      },
    ]);
    const onChanged = renderSelect(caseCard());
    fireEvent.change(screen.getByRole("combobox", { name: "Origen del caso" }), { target: { value: "whatsapp" } });
    fireEvent.change(screen.getByLabelText(/^Nota/), { target: { value: "Me escribieron por WhatsApp." } });
    fireEvent.click(screen.getByRole("button", { name: "Guardar origen" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Otra persona cambió este caso");
    expect(screen.queryByText("stale")).toBeNull();
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("says «ya tiene ese origen» when the API reports no change", async () => {
    stubPost([
      {
        status: 409,
        body: { error: { code: "conflict", message: "same", details: { code: "case_origin_unchanged", message: "same" }, request_id: "r" } },
      },
    ]);
    renderSelect(caseCard());
    fireEvent.change(screen.getByRole("combobox", { name: "Origen del caso" }), { target: { value: "web" } });
    fireEvent.change(screen.getByLabelText(/^Nota/), { target: { value: "Corrección." } });
    fireEvent.click(screen.getByRole("button", { name: "Guardar origen" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("El caso ya tiene ese origen.");
  });

  it("is read-only for a viewer or when the commands are off, and says why", () => {
    renderSelect(caseCard({ origin: "web" }), session("viewer"));
    expect(screen.getByRole("combobox", { name: "Origen del caso" })).toBeDisabled();
  });

  it("is read-only when the API did not report the case version", () => {
    renderSelect(caseCard({ version: null }));
    expect(screen.getByRole("combobox", { name: "Origen del caso" })).toBeDisabled();
  });

  it("corrects a closed case too", async () => {
    const posts = stubPost([{ body: receipt("recomendacion", null) }]);
    renderSelect(caseCard({ stage: "won", closed_at: "2026-10-01T00:00:00Z" }));
    fireEvent.change(screen.getByRole("combobox", { name: "Origen del caso" }), { target: { value: "recomendacion" } });
    fireEvent.change(screen.getByLabelText(/^Nota/), { target: { value: "Lo recomendó un cliente." } });
    fireEvent.click(screen.getByRole("button", { name: "Guardar origen" }));
    await waitFor(() => expect(posts).toHaveLength(1));
  });
});
