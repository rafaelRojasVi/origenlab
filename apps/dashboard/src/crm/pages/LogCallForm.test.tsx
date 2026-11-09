import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { OpportunityCardData } from "../crmTypes";
import { Toaster } from "../ui";
import { LogCallForm } from "./LogCallForm";

// Every value below is invented; the repository is public.
const CASE = "11111111-1111-4111-8111-111111111111";

function card(over: Partial<OpportunityCardData> = {}): OpportunityCardData {
  return {
    opportunity_id: CASE,
    title: "Cotización 00001-26 — Instituto Ficticio",
    stage: "quoting",
    version: 5,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-02T00:00:00Z",
    closed_at: null,
    close_reason: null,
    organization: null,
    other_organizations: [],
    contact: null,
    quotes: [],
    quote_numbers: ["00001-26"],
    revision_count: 0,
    latest_revision: null,
    drive_folder: null,
    attention: [],
    status: "ok",
    next_action: { text: "x", source: "suggested", due_at: null },
    ...over,
  } as OpportunityCardData;
}

interface Post {
  path: string;
  body: Record<string, unknown>;
  key: string | null;
}

function stubPosts(respond: (post: Post, n: number) => Response = () => json({ ok: true, note_id: "n1", version: 1 })) {
  const posts: Post[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const headers = new Headers(init?.headers);
      const post: Post = {
        path: new URL(url, "http://localhost").pathname,
        body: init?.body ? JSON.parse(String(init.body)) : {},
        key: headers.get("Idempotency-Key"),
      };
      posts.push(post);
      return Promise.resolve(respond(post, posts.length));
    }),
  );
  return posts;
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function mount(props: Partial<{ canRemind: boolean; onDone: () => void; onCancel: () => void }> = {}) {
  const onDone = props.onDone ?? vi.fn();
  render(
    <>
      <LogCallForm card={card()} canRemind={props.canRemind ?? true} onDone={onDone} onCancel={props.onCancel ?? vi.fn()} />
      <Toaster />
    </>,
  );
  return { onDone };
}

const submit = () => screen.getByRole("button", { name: "Registrar llamada" });

afterEach(() => vi.unstubAllGlobals());

describe("LogCallForm", () => {
  it("cannot be submitted until an outcome is chosen", () => {
    const posts = stubPosts();
    mount();
    expect(submit()).toBeDisabled();
    fireEvent.click(submit());
    expect(posts).toEqual([]);
    fireEvent.change(screen.getByLabelText(/¿Cómo terminó la llamada\?/), { target: { value: "contesto" } });
    expect(submit()).toBeEnabled();
  });

  it("offers the five outcomes by their Spanish label", () => {
    stubPosts();
    mount();
    const select = screen.getByLabelText(/¿Cómo terminó la llamada\?/) as HTMLSelectElement;
    const labels = Array.from(select.options).map((o) => o.textContent);
    expect(labels).toEqual(expect.arrayContaining(["Contestó", "No contestó", "Buzón de voz", "Número equivocado", "Reunión agendada"]));
    expect(Array.from(select.options).some((o) => /contesto|no_contesto|numero_malo/.test(o.textContent ?? ""))).toBe(false);
  });

  it("posts one typed note: kind, outcome, trimmed next step, default body", async () => {
    const posts = stubPosts();
    const { onDone } = mount();
    fireEvent.change(screen.getByLabelText(/¿Cómo terminó la llamada\?/), { target: { value: "contesto" } });
    fireEvent.change(screen.getByLabelText("Siguiente paso"), { target: { value: "  Mandar la ficha técnica  " } });
    fireEvent.click(submit());
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    expect(posts).toHaveLength(1);
    expect(posts[0].path).toBe("/v2/commands/add-note");
    expect(posts[0].key).toBeTruthy();
    expect(posts[0].body).toEqual({
      subject_kind: "opportunity",
      subject_id: CASE,
      body: "Llamada: Contestó",
      note_kind: "llamada",
      call_outcome: "contesto",
      call_next_step: "Mandar la ficha técnica",
    });
  });

  it("omits a blank next step and sends what the operator wrote as the body", async () => {
    const posts = stubPosts();
    const { onDone } = mount();
    fireEvent.change(screen.getByLabelText(/¿Cómo terminó la llamada\?/), { target: { value: "buzon" } });
    fireEvent.change(screen.getByLabelText("Siguiente paso"), { target: { value: "   " } });
    fireEvent.change(screen.getByLabelText("Notas de la llamada"), { target: { value: "Dejé mensaje con mi número." } });
    fireEvent.click(submit());
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(posts[0].body).toEqual({
      subject_kind: "opportunity",
      subject_id: CASE,
      body: "Dejé mensaje con mi número.",
      note_kind: "llamada",
      call_outcome: "buzon",
    });
  });

  it("limits the next step to 500 characters", () => {
    stubPosts();
    mount();
    expect(screen.getByLabelText("Siguiente paso")).toHaveAttribute("maxlength", "500");
  });

  it("shows a 422 in Spanish, keeps the form open and never calls it done", async () => {
    stubPosts(() => json({ error: { code: "validation_error", message: "bad" } }, 422));
    const { onDone } = mount();
    fireEvent.change(screen.getByLabelText(/¿Cómo terminó la llamada\?/), { target: { value: "contesto" } });
    fireEvent.click(submit());
    expect(await screen.findByRole("alert")).toHaveTextContent("Faltan datos o no son válidos");
    expect(onDone).not.toHaveBeenCalled();
    expect(submit()).toBeEnabled();
  });

  it("retries with the same idempotency key", async () => {
    const posts = stubPosts((_p, n) => (n === 1 ? json({ error: { code: "x" } }, 500) : json({ ok: true, note_id: "n1", version: 1 })));
    const { onDone } = mount();
    fireEvent.change(screen.getByLabelText(/¿Cómo terminó la llamada\?/), { target: { value: "contesto" } });
    fireEvent.click(submit());
    await screen.findByRole("alert");
    fireEvent.click(submit());
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(posts[1].key).toBe(posts[0].key);
  });

  it("with a reminder, also creates the dated follow-up task", async () => {
    const posts = stubPosts((p) =>
      p.path === "/v2/commands/add-note" ? json({ ok: true, note_id: "n1", version: 1 }) : json({ ok: true, replayed: false }),
    );
    const { onDone } = mount();
    fireEvent.change(screen.getByLabelText(/¿Cómo terminó la llamada\?/), { target: { value: "no_contesto" } });
    fireEvent.click(screen.getByRole("checkbox", { name: /Recordarme volver a llamar/ }));
    fireEvent.click(screen.getByRole("button", { name: "En 3 días" }));
    fireEvent.click(submit());
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(posts.map((p) => p.path)).toEqual(["/v2/commands/add-note", "/v2/commands/create-task"]);
    expect(posts[1].body).toMatchObject({ opportunity_id: CASE, title: "Seguimiento de 00001-26" });
    expect(Date.parse(String(posts[1].body.due_at)) - Date.now()).toBeGreaterThan(2 * 86_400_000);
  });

  it("does not offer a reminder when the case takes none", () => {
    stubPosts();
    mount({ canRemind: false });
    expect(screen.queryByRole("checkbox", { name: /Recordarme volver a llamar/ })).toBeNull();
  });
});
