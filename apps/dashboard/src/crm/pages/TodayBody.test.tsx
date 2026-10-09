import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import type { OpportunityCardData, RevisionCard } from "../crmTypes";
import { followUpsDue, organizationsToConfirm, repliesToAnswer, tasksDue } from "../today";
import { Toaster } from "../ui";
import { clearResourceCache } from "../useResource";
import { TodayBody } from "./TodayBody";

// Every value below is invented; the repository is public.
const NOW = new Date("2026-10-06T15:00:00Z");

function rev(sentAt: string): RevisionCard {
  return {
    revision_id: `r-${sentAt}`,
    revision_no: 1,
    status: "sent",
    origin: "historical_import",
    sent_at: sentAt,
    superseded_by_revision_no: null,
    is_active: true,
    document: null,
    gmail: { source_record_id: "s", message_id: "m", thread_id: "t", url: "https://mail.example.cl/m", subject: "Cotización" },
    drive: null,
    quote_number: "01239-26",
  };
}

let n = 0;
function card(over: Partial<OpportunityCardData> = {}, sentAt = "2026-09-20T12:00:00Z"): OpportunityCardData {
  n += 1;
  const latest = rev(sentAt);
  return {
    opportunity_id: `00000000-0000-4000-8000-00000000000${n}`,
    title: `Caso ${n}`,
    stage: "negotiating",
    version: 4,
    created_at: null,
    updated_at: null,
    closed_at: null,
    close_reason: null,
    organization: { organization_id: `org-${n}`, name: `Instituto Ficticio ${n}`, confirmation: "confirmed", version: 2 },
    other_organizations: [],
    contact: null,
    quotes: [{ quote_id: `q-${n}`, quote_number: "01239-26", number_origin: "printed_historical", revisions: [latest] }],
    quote_numbers: ["01239-26"],
    revision_count: 1,
    latest_revision: latest,
    drive_folder: null,
    attention: [],
    status: "ok",
    next_action: { text: "", source: "suggested", due_at: null },
    ...over,
  };
}

const task = (id: string, due: string, version = 1) => ({ task_id: id, title: `Tarea ${id}`, due_at: due, version, owner: null });

describe("today lists", () => {
  it("takes tasks due by tonight, overdue first, and leaves later ones out", () => {
    const c = card({ open_tasks: [task("a", "2026-10-06T20:00:00Z"), task("b", "2026-10-04T12:00:00Z"), task("c", "2026-10-09T12:00:00Z")] });
    expect(tasksDue([c], NOW).map((t) => [t.task.task_id, t.overdueDays])).toEqual([
      ["b", 2],
      ["a", 0],
    ]);
  });

  it("puts each silent case on the 3 · 14 · 30 rhythm, never a planned, replied or historical one", () => {
    const d5 = card({}, "2026-10-01T12:00:00Z");
    const d20 = card({}, "2026-09-16T12:00:00Z");
    const d40 = card({}, "2026-08-27T12:00:00Z");
    const fresh = card({}, "2026-10-05T12:00:00Z");
    const planned = card({ open_tasks: [task("p", "2026-10-20T12:00:00Z")] }, "2026-08-01T12:00:00Z");
    const replied = card(
      { last_contact: { outbound: null, inbound: { at: "2026-10-02T12:00:00Z", subject: null, url: null } } },
      "2026-09-01T12:00:00Z",
    );
    const historical = card({ stage: "quoting" }, "2026-07-01T12:00:00Z");
    const out = followUpsDue([d5, d20, d40, fresh, planned, replied, historical], NOW);
    expect(out.map((f) => [f.card.opportunity_id, f.rhythm])).toEqual([
      [d40.opportunity_id, "cerrar"],
      [d20.opportunity_id, "segundo"],
      [d5.opportunity_id, "primero"],
    ]);
    expect(repliesToAnswer([d5, replied], NOW).map((r) => r.card.opportunity_id)).toEqual([replied.opportunity_id]);
  });

  it("leaves a reply out of «Te toca responder» once a task was scheduled after it, until the client writes again", () => {
    const inbound = (at: string) => ({ outbound: null, inbound: { at, subject: null, url: null } });
    const scheduled = (created: string) => ({ ...task("p", "2026-10-26T12:00:00Z"), created_at: created });
    // «Gracias, le aviso» (30 sept), then «En pausa» / «No requiere respuesta» (2 oct): answered.
    const answered = card({ open_tasks: [scheduled("2026-10-02T12:00:00Z")], last_contact: inbound("2026-09-30T12:00:00Z") }, "2026-09-25T12:00:00Z");
    // The client wrote again after the pause: a new question, back on the list.
    const again = card({ open_tasks: [scheduled("2026-10-02T12:00:00Z")], last_contact: inbound("2026-10-04T12:00:00Z") }, "2026-09-25T12:00:00Z");
    // A task scheduled before the client's email does not answer it, even if it is due later.
    const before = card({ open_tasks: [scheduled("2026-09-26T12:00:00Z")], last_contact: inbound("2026-09-30T12:00:00Z") }, "2026-09-25T12:00:00Z");
    // An older API without `created_at`: a case «En pausa» counts as answered.
    const olderApi = card({ open_tasks: [task("q", "2026-10-26T12:00:00Z")], last_contact: inbound("2026-09-30T12:00:00Z") }, "2026-09-25T12:00:00Z");
    expect(repliesToAnswer([answered, again, before, olderApi], NOW).map((r) => r.card.opportunity_id)).toEqual([
      again.opportunity_id,
      before.opportunity_id,
    ]);
  });

  it("puts a case whose «Seguimiento …» task is due in the follow-ups from day 3, never among the other tasks", () => {
    const fu = (id: string, due: string) => ({ task_id: id, title: "Seguimiento de 01239-26", due_at: due, version: 1, owner: null });
    const fresh = card({ open_tasks: [fu("f1", "2026-10-06T12:00:00Z")] }, "2026-10-05T12:00:00Z");
    const old = card({ stage: "quoting", open_tasks: [fu("f2", "2026-10-04T12:00:00Z")] }, "2026-08-27T12:00:00Z");
    const later = card({ open_tasks: [fu("f3", "2026-10-20T12:00:00Z")] }, "2026-08-27T12:00:00Z");
    const resume = card({ open_tasks: [task("r", "2026-10-06T12:00:00Z")] }, "2026-08-27T12:00:00Z");
    const out = followUpsDue([fresh, old, later, resume], NOW);
    // The quote sent yesterday is not chased yet, task or not.
    expect(out.map((f) => [f.card.opportunity_id, f.rhythm, f.task?.task.task_id])).toEqual([[old.opportunity_id, "cerrar", "f2"]]);
    expect(tasksDue([fresh, old, later, resume], NOW).map((t) => t.task.task_id)).toEqual(["r"]);
  });

  it("counts a follow-up task as done once OrigenLab writes on the thread that day, without «Hecho»", () => {
    const fu = { task_id: "w", title: "Seguimiento de 01239-26", due_at: "2026-10-06T12:00:00Z", version: 1, owner: null };
    const written = card(
      { open_tasks: [fu], last_contact: { outbound: { at: "2026-10-06T13:30:00Z", subject: "Re", url: null }, inbound: null } },
      "2026-08-27T12:00:00Z",
    );
    const notYet = card(
      { open_tasks: [{ ...fu, task_id: "n" }], last_contact: { outbound: { at: "2026-09-10T13:30:00Z", subject: "Re", url: null }, inbound: null } },
      "2026-08-27T12:00:00Z",
    );
    const out = followUpsDue([written, notYet], NOW);
    expect(out.map((f) => f.card.opportunity_id)).toEqual([notYet.opportunity_id]);
    // Three days after that email, the case is back on the rhythm by itself.
    const later = new Date("2026-10-09T15:00:00Z");
    expect(followUpsDue([written], later).map((f) => [f.rhythm, f.task])).toEqual([["primero", null]]);
  });

  it("groups the machine-proposed institutions of open cases", () => {
    const org = { organization_id: "o-1", name: "ejemplo.cl", confirmation: "machine_proposed", version: 3 };
    const out = organizationsToConfirm([card({ organization: org }), card({ organization: org }), card()]);
    expect(out).toHaveLength(1);
    expect(out[0]).toMatchObject({ organization_id: "o-1", version: 3 });
    expect(out[0].cases).toHaveLength(2);
  });
});

function session(role: string): AuthSessionState {
  return {
    kind: "signed_in",
    method: "google_profile",
    operator: { operatorId: "op-1", email: "ventas@example.cl", displayName: "Ventas", role },
    caseCommandsEnabled: true,
    crmAuthoringEnabled: true,
  };
}

interface Call {
  path: string;
  body: Record<string, unknown> | null;
}

function stub(): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const path = new URL(url, "http://localhost").pathname;
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null;
      calls.push({ path, body });
      const json = (b: unknown) =>
        Promise.resolve(new Response(JSON.stringify(b), { status: 200, headers: { "Content-Type": "application/json" } }));
      if (path === "/v2/workspace/person-suggestions") return json({ items: [], total: 0 });
      return json({ command: "x", opportunity_id: "o", opportunity_version: 5, command_receipt_id: "rcpt-1", replayed: false });
    }),
  );
  return calls;
}

function renderToday(items: OpportunityCardData[], role = "sales") {
  const onChanged = vi.fn();
  render(
    <AuthSessionContext.Provider value={{ session: session(role), signOut: async () => true }}>
      <TodayBody items={items} navigate={() => undefined} onChanged={onChanged} now={NOW} />
      <Toaster />
    </AuthSessionContext.Provider>,
  );
  return onChanged;
}

afterEach(() => {
  vi.unstubAllGlobals();
  clearResourceCache();
});

describe("Hoy actions", () => {
  it("completes a task, and postpones another a week by writing it again and cancelling it", async () => {
    const calls = stub();
    const c = card({ open_tasks: [task("t1", "2026-10-06T12:00:00Z", 2), task("t2", "2026-10-05T12:00:00Z", 1)] });
    const onChanged = renderToday([c]);
    fireEvent.click(within(screen.getByTestId("today-task-t1")).getByRole("button", { name: "Hecho" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(calls.find((x) => x.path === "/v2/commands/complete-task")?.body).toEqual({ task_id: "t1", task_version: 2, note: "Hecho desde «Hoy»." });

    fireEvent.click(within(screen.getByTestId("today-task-t2")).getByRole("button", { name: "+1 semana" }));
    expect(await screen.findByTestId("toaster")).toHaveTextContent(/Pospuesta al 13 oct 2026/);
    const posts = calls.filter((x) => x.path.startsWith("/v2/commands/")).map((x) => x.path);
    expect(posts).toEqual(["/v2/commands/complete-task", "/v2/commands/create-task", "/v2/commands/cancel-task"]);
    expect(calls.find((x) => x.path === "/v2/commands/create-task")?.body).toMatchObject({
      opportunity_id: c.opportunity_id,
      title: "Tarea t2",
      due_at: "2026-10-13T15:00:00.000Z",
    });
  });

  it("moves a replied case still «Enviada» to «Conversación» in one click", async () => {
    const calls = stub();
    const c = card({
      stage: "quoting",
      last_contact: { outbound: null, inbound: { at: "2026-10-03T12:00:00Z", subject: "Re", url: "https://mail.example.cl/in" } },
    });
    renderToday([c]);
    fireEvent.click(screen.getByRole("button", { name: "Pasar a Conversación" }));
    expect(await screen.findByTestId("toaster")).toHaveTextContent("Pasó a «Conversación».");
    expect(calls.find((x) => x.path === "/v2/commands/advance-case-stage")?.body).toMatchObject({
      opportunity_id: c.opportunity_id,
      opportunity_version: 4,
      stage: "negotiating",
    });
  });

  it("takes a «gracias, le aviso» off «Te toca responder» with a follow-up a week out", async () => {
    const calls = stub();
    const c = card({
      stage: "quoting",
      last_contact: { outbound: null, inbound: { at: "2026-10-03T12:00:00Z", subject: "Re", url: "https://mail.example.cl/in" } },
    });
    const onChanged = renderToday([c]);
    fireEvent.click(screen.getByRole("button", { name: "No requiere respuesta" }));
    expect(await screen.findByTestId("toaster")).toHaveTextContent("Listo. Te lo recuerdo el");
    expect(calls.find((x) => x.path === "/v2/commands/create-task")?.body).toMatchObject({
      opportunity_id: c.opportunity_id,
      title: `Seguimiento de ${c.latest_revision?.quote_number}`,
      due_at: "2026-10-13T15:00:00.000Z",
    });
    expect(onChanged).toHaveBeenCalled();
  });

  it("closes a 30-day silent case as «Sin respuesta» from the rhythm", async () => {
    const calls = stub();
    const c = card({}, "2026-08-20T12:00:00Z");
    renderToday([c]);
    const rhythm = screen.getByTestId("today-rhythm-cerrar");
    fireEvent.click(within(rhythm).getByRole("button", { name: "Más opciones" }));
    fireEvent.click(within(rhythm).getByRole("menuitem", { name: "Cerrar sin respuesta" }));
    const modal = screen.getByRole("dialog", { name: "Cerrar sin respuesta" });
    expect(within(modal).getByRole("button", { name: "Sin respuesta" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(within(modal).getByRole("button", { name: "Marcar perdida" }));
    expect(await screen.findByTestId("toaster")).toHaveTextContent("Caso cerrado como perdido.");
    expect(calls.find((x) => x.path === "/v2/commands/advance-case-stage")?.body).toMatchObject({
      stage: "abandoned",
      close_reason: "Sin respuesta",
    });
  });

  it("shows a scheduled follow-up with what the quote is for, one reply button and the task behind «⋯»", async () => {
    const calls = stub();
    const c = card({ open_tasks: [{ task_id: "s1", title: "Seguimiento de 01239-26", due_at: "2026-10-06T12:00:00Z", version: 3, owner: null }] }, "2026-09-01T12:00:00Z");
    (c.latest_revision as RevisionCard).gmail = { source_record_id: "s", message_id: "m", thread_id: "t", url: "https://mail.example.cl/m", subject: "Cotización Balanzas Ohaus" };
    const onChanged = renderToday([c]);
    const row = within(screen.getByTestId("today-rhythm-cerrar")).getByTestId("today-task-s1");
    expect(within(row).getByTestId("today-product")).toHaveTextContent("Balanzas Ohaus");
    expect(within(row).getByRole("link", { name: /Responder en Gmail/ })).toHaveAttribute(
      "href",
      "https://mail.example.cl/m",
    );
    expect(screen.getByTestId("today-no-tasks")).toBeInTheDocument();
    fireEvent.click(within(row).getByRole("button", { name: "Más opciones" }));
    fireEvent.click(within(row).getByRole("menuitem", { name: "Ya le escribí" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(calls.find((x) => x.path === "/v2/commands/complete-task")?.body).toEqual({ task_id: "s1", task_version: 3, note: "Hecho desde «Hoy»." });
  });

  it("shows the lists to a viewer without any button that writes", () => {
    stub();
    renderToday([card({ open_tasks: [task("v", "2026-10-06T12:00:00Z")] }, "2026-08-20T12:00:00Z")], "viewer");
    expect(screen.getByTestId("today-task-v")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Hecho" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cerrar sin respuesta" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Más opciones" })).not.toBeInTheDocument();
  });

  it("puts off an unscheduled follow-up a week by writing it a «Seguimiento …» task", async () => {
    const calls = stub();
    const c = card({}, "2026-09-25T12:00:00Z");
    renderToday([c]);
    const row = screen.getByTestId(`today-followup-${c.opportunity_id}`);
    fireEvent.click(within(row).getByRole("button", { name: "Más opciones" }));
    expect(within(row).queryByRole("menuitem", { name: "Ya le escribí" })).not.toBeInTheDocument();
    fireEvent.click(within(row).getByRole("menuitem", { name: "Recordar en 1 semana" }));
    expect(await screen.findByTestId("toaster")).toHaveTextContent(/Te lo recuerdo el 13 oct 2026/);
    expect(calls.filter((x) => x.path.startsWith("/v2/commands/")).map((x) => x.path)).toEqual(["/v2/commands/create-task"]);
    expect(calls.find((x) => x.path === "/v2/commands/create-task")?.body).toMatchObject({
      opportunity_id: c.opportunity_id,
      title: "Seguimiento de 01239-26",
      due_at: "2026-10-13T15:00:00.000Z",
    });
  });
});
