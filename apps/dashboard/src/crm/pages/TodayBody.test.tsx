import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import type { OpportunityCardData, RevisionCard } from "../crmTypes";
import { followUpsDue, organizationsToConfirm, overdueTasks, repliesToAnswer, tasksDue } from "../today";
import { forgetCreatedQuotes } from "../quotes/openOrCreateQuote";
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
  it("leaves a closing reply («gracias, te aviso», mail rule R18) out of «Responder» and counts the follow-up from it", () => {
    const closing = (at: string) => ({ outbound: null, inbound: { at, subject: null, url: null, no_reply: true } });
    // Quote on 20 Sept, «gracias, lo evaluamos» on 3 Oct: nothing to answer; the 3-day follow-up is due on the 6th.
    const thanked = card({ last_contact: closing("2026-10-03T12:00:00Z") }, "2026-09-20T12:00:00Z");
    expect(repliesToAnswer([thanked], NOW)).toEqual([]);
    expect(followUpsDue([thanked], NOW).map((f) => [f.card.opportunity_id, f.days, f.rhythm])).toEqual([
      [thanked.opportunity_id, 3, "primero"],
    ]);
    // A won case's «acuso recibo»: off the list, and no follow-up on a won case.
    const won = card({ stage: "won", closed_at: "2026-10-01T12:00:00Z", last_contact: closing("2026-10-03T12:00:00Z") }, "2026-09-20T12:00:00Z");
    expect(repliesToAnswer([won], NOW)).toEqual([]);
    expect(followUpsDue([won], NOW)).toEqual([]);
  });

  it("takes tasks due by tonight, overdue first, and leaves later ones out", () => {
    const c = card({ open_tasks: [task("a", "2026-10-06T20:00:00Z"), task("b", "2026-10-04T12:00:00Z"), task("c", "2026-10-09T12:00:00Z")] });
    expect(tasksDue([c], NOW).map((t) => [t.task.task_id, t.overdueDays])).toEqual([
      ["b", 2],
      ["a", 0],
    ]);
  });

  it("«Vencidas»: one's own from 3 days overdue, anyone's from 7, most overdue first, follow-ups never", () => {
    const mine = { ...task("mine3", "2026-10-03T12:00:00Z"), owner: "Ventas" };
    const mine2 = { ...task("mine2", "2026-10-04T12:00:00Z"), owner: "Ventas" };
    const theirs5 = { ...task("theirs5", "2026-10-01T12:00:00Z"), owner: "Tatiana" };
    const theirs9 = { ...task("theirs9", "2026-09-27T12:00:00Z"), owner: "Tatiana" };
    const fu = { ...task("fu", "2026-09-20T12:00:00Z"), title: "Seguimiento de 01239-26", owner: "Ventas" };
    const c = card({ open_tasks: [mine, mine2, theirs5, theirs9, fu] });
    expect(overdueTasks([c], NOW, "Ventas").map((t) => [t.task.task_id, t.overdueDays, t.escalated])).toEqual([
      ["theirs9", 9, true],
      ["mine3", 3, false],
    ]);
    // Without a profile name, only the escalated ones; a closed case's tasks never.
    expect(overdueTasks([c], NOW, null).map((t) => t.task.task_id)).toEqual(["theirs9"]);
    expect(overdueTasks([card({ open_tasks: [theirs9], stage: "lost", closed_at: "2026-10-01T00:00:00Z" })], NOW, "Ventas")).toEqual([]);
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

  it("shows a client who writes on a won case under «Te toca responder», with «Atendido» as the only write: a note on the case", async () => {
    // Each case its own email: one email shared by two cases is a single row (see the test below).
    let n = 0;
    const inbound = (at: string) => ({ outbound: null, inbound: { at, subject: null, url: `https://mail.example.cl/pay-${++n}` } });
    // Won on 6 Oct; the client sent the payment on the 8th and asks for delivery dates.
    const paid = card({ stage: "won", closed_at: "2026-10-06T12:00:00Z", last_contact: inbound("2026-10-08T16:30:00Z") }, "2026-09-30T12:00:00Z");
    // Won on 6 Oct after the client's last email: nothing new to answer.
    const settled = card({ stage: "won", closed_at: "2026-10-06T12:00:00Z", last_contact: inbound("2026-10-05T16:30:00Z") }, "2026-09-30T12:00:00Z");
    // A lost case's client writing back is a new request, not a reply to answer here.
    const lost = card({ stage: "lost", closed_at: "2026-10-06T12:00:00Z", last_contact: inbound("2026-10-08T16:30:00Z") }, "2026-09-30T12:00:00Z");
    expect(repliesToAnswer([paid, settled, lost], NOW).map((r) => r.card.opportunity_id)).toEqual([paid.opportunity_id]);
    expect(followUpsDue([paid], NOW)).toEqual([]);

    // Answered by phone, noted on the 9th: off the list. The client's next email puts it back.
    const noted = card(
      { stage: "won", closed_at: "2026-10-06T12:00:00Z", last_contact: inbound("2026-10-08T16:30:00Z"), last_note: { created_at: "2026-10-09T10:00:00Z", body: "Atendido", author: null } },
      "2026-09-30T12:00:00Z",
    );
    const notedBefore = card(
      { stage: "won", closed_at: "2026-10-06T12:00:00Z", last_contact: inbound("2026-10-08T16:30:00Z"), last_note: { created_at: "2026-10-07T10:00:00Z", body: "Entrega acordada", author: null } },
      "2026-09-30T12:00:00Z",
    );
    // An internal note on an OPEN case never hides the client's email: only a task answers it there.
    const openNoted = card(
      { last_contact: inbound("2026-10-08T16:30:00Z"), last_note: { created_at: "2026-10-09T10:00:00Z", body: "Llamé, no contestó", author: null } },
      "2026-09-30T12:00:00Z",
    );
    expect(repliesToAnswer([noted, notedBefore, openNoted], NOW).map((r) => r.card.opportunity_id)).toEqual([
      notedBefore.opportunity_id,
      openNoted.opportunity_id,
    ]);

    const calls = stub();
    const onChanged = renderToday([paid]);
    const row = screen.getByText(/Escribió tras ganar/).closest("div")!.parentElement!;
    expect(within(row).getByRole("link", { name: /Responder en Gmail/ })).toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: /No requiere respuesta/ })).toBeNull();
    expect(within(row).queryByRole("button", { name: /Pasar a Conversación/ })).toBeNull();
    fireEvent.click(within(row).getByRole("button", { name: "Más opciones" }));
    fireEvent.click(within(row).getByRole("menuitem", { name: "Atendido" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    const note = calls.find((c) => c.path === "/v2/commands/add-note");
    expect(note?.body).toMatchObject({ subject_kind: "opportunity", subject_id: paid.opportunity_id });
    expect(String(note?.body?.body)).toContain("08 oct 2026");
    expect(calls.filter((c) => c.path.startsWith("/v2/commands/")).map((c) => c.path)).toEqual(["/v2/commands/add-note"]);
  });

  it("puts a case whose «Seguimiento …» reminder is due in the follow-ups on its day, never among the other tasks", () => {
    const fu = (id: string, due: string) => ({ task_id: id, title: "Seguimiento de 01239-26", due_at: due, version: 1, owner: null });
    const fresh = card({ open_tasks: [fu("f1", "2026-10-06T12:00:00Z")] }, "2026-10-05T12:00:00Z");
    const old = card({ stage: "quoting", open_tasks: [fu("f2", "2026-10-04T12:00:00Z")] }, "2026-08-27T12:00:00Z");
    const later = card({ open_tasks: [fu("f3", "2026-10-20T12:00:00Z")] }, "2026-08-27T12:00:00Z");
    const resume = card({ open_tasks: [task("r", "2026-10-06T12:00:00Z")] }, "2026-08-27T12:00:00Z");
    const out = followUpsDue([fresh, old, later, resume], NOW);
    // A reminder the operator set shows on its day, even on a quote sent yesterday; one for a
    // later day keeps its case off the list until then.
    expect(out.map((f) => [f.card.opportunity_id, f.rhythm, f.task?.task.task_id])).toEqual([
      [old.opportunity_id, "cerrar", "f2"],
      [fresh.opportunity_id, "primero", "f1"],
    ]);
    // Without a reminder, a quote sent yesterday is not chased yet.
    expect(followUpsDue([card({}, "2026-10-05T12:00:00Z")], NOW)).toEqual([]);
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
  forgetCreatedQuotes();
});

describe("«Tareas» on the page", () => {
  it("is one list: overdue first with the owner on escalated rows, then the rest due today", () => {
    // Each task on a SEPARATE case so the dedup-by-opportunity_id keeps all four rows.
    // Order is by waitMs desc: theirs8 (8 d), theirs4 (4 d, plain DueTask), mine4 (3 d, mine), today (0).
    const mine = { ...task("mine4", "2026-10-03T12:00:00Z"), owner: "Ventas" };
    const theirs8 = { ...task("theirs8", "2026-09-28T12:00:00Z"), owner: "Tatiana" };
    const theirs4 = { ...task("theirs4", "2026-10-02T12:00:00Z"), owner: "Tatiana" };
    const todayTask = { ...task("today", "2026-10-06T20:00:00Z"), owner: "Ventas" };
    renderToday([
      card({ open_tasks: [theirs8] }, "2026-10-05T12:00:00Z"),
      card({ open_tasks: [mine] }, "2026-10-05T12:00:00Z"),
      card({ open_tasks: [theirs4] }, "2026-10-05T12:00:00Z"),
      card({ open_tasks: [todayTask] }, "2026-10-05T12:00:00Z"),
    ]);
    const panel = screen.getByTestId("today-para-ti");
    expect(panel).toHaveTextContent("Tareas");
    expect(within(panel).getAllByTestId(/^today-task-/).map((e) => e.getAttribute("data-testid"))).toEqual([
      "today-task-theirs8", "today-task-theirs4", "today-task-mine4", "today-task-today",
    ]);
    expect(within(within(panel).getByTestId("today-task-theirs8")).getByTestId("today-owner-label")).toHaveTextContent("de Tatiana");
    expect(within(within(panel).getByTestId("today-task-mine4")).queryByTestId("today-owner-label")).toBeNull();
    // Actions (Hecho, +1 semana) are in the ⋯ menu, not direct buttons.
    expect(screen.queryByRole("button", { name: "Hecho" })).toBeNull();
    // Old section headings are gone.
    expect(screen.queryByText("Otras tareas de hoy")).toBeNull();
    expect(screen.queryByText("Vencidas")).toBeNull();
  });
});

describe("Hoy actions", () => {
  it("«Responder» opens the client's email in a side panel, Gmail one click away", async () => {
    const c = card({
      last_contact: { outbound: null, inbound: { at: "2026-10-05T12:00:00Z", subject: "Consulta plazo", url: "https://mail.google.com/mail/u/0/#all/18f0a1b2c3d4" } },
    });
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      calls.push(new URL(url, "http://localhost").pathname);
      const body = { message_id: "18f0a1b2c3d4", subject: "Consulta plazo", from: "Ana Ficticia", to: ["Ventas"], cc: [],
        sent_at: "2026-10-05T12:00:00Z", text: "¿Cuál es el plazo de entrega?", quoted: null, truncated: false, attachments: [] };
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
    }));
    renderToday([c]);
    const row = screen.getByTestId(`today-reply-${c.opportunity_id}`);
    expect(within(row).queryByRole("link", { name: /Responder en Gmail/ })).toBeNull();
    fireEvent.click(within(row).getByRole("button", { name: "Responder" }));
    const panel = await screen.findByRole("dialog");
    expect(await within(panel).findByText("¿Cuál es el plazo de entrega?")).toBeInTheDocument();
    expect(calls).toContain(`/v2/workspace/opportunities/${c.opportunity_id}/messages/18f0a1b2c3d4`);
    expect(within(panel).getByRole("link", { name: "Responder en Gmail ↗" }).getAttribute("href")).toContain("#all/18f0a1b2c3d4");
    fireEvent.click(within(panel).getByRole("button", { name: "Cerrar" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("never repeats the sidebar's «Datos» link", () => {
    stub();
    renderToday([card()], "admin");
    expect(screen.queryByRole("button", { name: /Estado del sistema|Acciones del correo|estado de los datos/ })).not.toBeInTheDocument();
  });

  it("shows a blocked case a client wrote on once, in «Te toca responder», with «Resolver»", () => {
    stub();
    const blocked = card({
      stage: "lead", organization: null, quotes: [], quote_numbers: [], latest_revision: null, status: "blocked",
      attention: [{ code: "case_without_requester", label: "Caso sin institución solicitante", blocking: true }],
      last_contact: { outbound: null, inbound: { at: "2026-10-05T12:00:00Z", subject: null, url: null } },
    } as Partial<OpportunityCardData>);
    const quiet = card({
      stage: "lead", organization: null, quotes: [], quote_numbers: [], latest_revision: null, status: "blocked", title: "Caso callado",
      attention: [{ code: "case_without_requester", label: "Caso sin institución solicitante", blocking: true }],
    } as Partial<OpportunityCardData>);
    renderToday([blocked, quiet], "sales");
    // Only the blocked case with a reply appears in the queue; the silent one is in housekeeping.
    expect(screen.getAllByText("Caso sin institución solicitante")).toHaveLength(1);
    const paraTi = screen.getByTestId("today-para-ti") as HTMLElement;
    expect(within(paraTi).getByText(/Escribió/)).toBeInTheDocument();
    expect(within(paraTi).getByRole("button", { name: "Resolver" })).toBeInTheDocument();
    expect(within(paraTi).queryByRole("button", { name: "No requiere respuesta" })).not.toBeInTheDocument();
  });

  it("a cleared row stays gone for this session even when the reload still returns it", async () => {
    stub();
    const replied = card(
      { stage: "quoting", version: 4, last_contact: { outbound: null, inbound: { at: "2026-10-02T12:00:00Z", subject: null, url: null } } },
      "2026-09-01T12:00:00Z",
    );
    const tree = (items: OpportunityCardData[]) => (
      <AuthSessionContext.Provider value={{ session: session("sales"), signOut: async () => true }}>
        <TodayBody items={items} navigate={() => undefined} onChanged={() => undefined} now={NOW} />
        <Toaster />
      </AuthSessionContext.Provider>
    );
    const { rerender } = render(tree([replied]));
    const replyRow = screen.getByTestId(`today-reply-${replied.opportunity_id}`);
    fireEvent.click(within(replyRow).getByRole("button", { name: "Más opciones" }));
    fireEvent.click(within(replyRow).getByRole("menuitem", { name: "Pasar a Conversación" }));
    await waitFor(() => expect(screen.queryByText(/Respondió/)).not.toBeInTheDocument());
    // goneIds keeps the cleared item hidden for this session even if the reload still returns it.
    rerender(tree([{ ...replied, stage: "negotiating", version: 5 }]));
    expect(screen.queryByText(/Respondió/)).not.toBeInTheDocument();
  });

  it("a done task leaves at once, before any reload, and a refused one stays", async () => {
    // Two separate cases so both rows appear (the queue dedupes by opportunity_id).
    const c1 = card({ open_tasks: [task("t1", "2026-10-06T12:00:00Z", 2)] });
    const c2 = card({ open_tasks: [task("t2", "2026-10-05T12:00:00Z", 1)] });
    stub();
    const onChanged = renderToday([c1, c2]);
    const row = screen.getByTestId("today-task-t1");
    fireEvent.click(within(row).getByRole("button", { name: "Más opciones" }));
    fireEvent.click(within(row).getByRole("menuitem", { name: "Hecho" }));
    // goneIds removes the row immediately once action succeeds (no flicker on reload).
    await waitFor(() => expect(screen.queryByTestId("today-task-t1")).not.toBeInTheDocument());
    expect(onChanged).toHaveBeenCalled();

    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response(
      JSON.stringify({ error: { code: "conflict", details: { code: "stale_version" } } }), { status: 409 }))));
    const kept = screen.getByTestId("today-task-t2");
    fireEvent.click(within(kept).getByRole("button", { name: "Más opciones" }));
    fireEvent.click(within(kept).getByRole("menuitem", { name: "Hecho" }));
    expect(await screen.findByTestId("toaster")).toBeInTheDocument();
    expect(screen.getByTestId("today-task-t2")).toBeInTheDocument();
  });

  it("completes a task, and postpones another a week by writing it again and cancelling it", async () => {
    // Two separate cases so both rows appear; actions are in the ⋯ menu.
    const calls = stub();
    const c1 = card({ open_tasks: [task("t1", "2026-10-06T12:00:00Z", 2)] });
    const c2 = card({ open_tasks: [task("t2", "2026-10-05T12:00:00Z", 1)] });
    const onChanged = renderToday([c1, c2]);
    const t1row = screen.getByTestId("today-task-t1");
    fireEvent.click(within(t1row).getByRole("button", { name: "Más opciones" }));
    fireEvent.click(within(t1row).getByRole("menuitem", { name: "Hecho" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(calls.find((x) => x.path === "/v2/commands/complete-task")?.body).toEqual({ task_id: "t1", task_version: 2, note: "Hecho desde «Hoy»." });

    const t2row = screen.getByTestId("today-task-t2");
    fireEvent.click(within(t2row).getByRole("button", { name: "Más opciones" }));
    fireEvent.click(within(t2row).getByRole("menuitem", { name: "+1 semana" }));
    expect(await screen.findByTestId("toaster")).toHaveTextContent(/Pospuesta al 13 oct 2026/);
    const posts = calls.filter((x) => x.path.startsWith("/v2/commands/")).map((x) => x.path);
    expect(posts).toEqual(["/v2/commands/complete-task", "/v2/commands/create-task", "/v2/commands/cancel-task"]);
    expect(calls.find((x) => x.path === "/v2/commands/create-task")?.body).toMatchObject({
      opportunity_id: c2.opportunity_id,
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
    const replyRow = screen.getByTestId(`today-reply-${c.opportunity_id}`);
    fireEvent.click(within(replyRow).getByRole("button", { name: "Más opciones" }));
    fireEvent.click(within(replyRow).getByRole("menuitem", { name: "Pasar a Conversación" }));
    expect(await screen.findByTestId("toaster")).toHaveTextContent("Pasó a «Conversación».");
    expect(calls.find((x) => x.path === "/v2/commands/advance-case-stage")?.body).toMatchObject({
      opportunity_id: c.opportunity_id,
      opportunity_version: 4,
      stage: "negotiating",
    });
  });

  it("shows one client email that answers two cases once, naming both, and «No requiere respuesta» settles both", async () => {
    const calls = stub();
    const mail = { outbound: null, inbound: { at: "2026-10-03T12:00:00Z", subject: "Re", url: "https://mail.example.cl/same" } };
    const a = card({ opportunity_id: "case-a", title: "Cotización 1176-26 — Cliente Uno", stage: "quoting", last_contact: mail });
    const b = card({ opportunity_id: "case-b", title: "Cliente Uno — cotización 01254-26", stage: "quoting", last_contact: mail });
    renderToday([a, b]);
    expect(screen.getAllByRole("link", { name: /Responder en Gmail/ })).toHaveLength(1);
    expect(screen.getByText(/También en/)).toHaveTextContent("Cliente Uno — cotización 01254-26");
    const primaryRow = screen.getByTestId(`today-reply-${a.opportunity_id}`);
    fireEvent.click(within(primaryRow).getByRole("button", { name: "Más opciones" }));
    fireEvent.click(within(primaryRow).getByRole("menuitem", { name: "No requiere respuesta" }));
    expect(await screen.findByTestId("toaster")).toHaveTextContent("Listo.");
    const tasks = calls.filter((x) => x.path === "/v2/commands/create-task").map((x) => x.body?.opportunity_id);
    expect(tasks.sort()).toEqual(["case-a", "case-b"]);
  });

  it("takes a «gracias, le aviso» off «Te toca responder» with a follow-up a week out", async () => {
    const calls = stub();
    const c = card({
      stage: "quoting",
      last_contact: { outbound: null, inbound: { at: "2026-10-03T12:00:00Z", subject: "Re", url: "https://mail.example.cl/in" } },
    });
    const onChanged = renderToday([c]);
    const replyRow = screen.getByTestId(`today-reply-${c.opportunity_id}`);
    fireEvent.click(within(replyRow).getByRole("button", { name: "Más opciones" }));
    fireEvent.click(within(replyRow).getByRole("menuitem", { name: "No requiere respuesta" }));
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
    // Exactly 30 days ago so isFollowUpDueToday passes (days === 30).
    const c = card({}, "2026-09-06T12:00:00Z");
    renderToday([c]);
    const row = screen.getByTestId(`today-followup-${c.opportunity_id}`);
    fireEvent.click(within(row).getByRole("button", { name: "Más opciones" }));
    fireEvent.click(within(row).getByRole("menuitem", { name: "Cerrar sin respuesta" }));
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
    // No today-rhythm-cerrar wrapper in the unified queue — the row is directly in today-para-ti.
    const row = screen.getByTestId("today-task-s1");
    expect(row).toHaveTextContent("Balanzas Ohaus");
    expect(within(row).getByRole("link", { name: /Escribir/ })).toHaveAttribute(
      "href",
      "https://mail.example.cl/m",
    );
    // Nothing else due: the old separate «Tareas» panel is not drawn.
    expect(screen.queryByTestId("today-tasks")).not.toBeInTheDocument();
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

  it("a note is a reminder, not a contact: the case stays on its clock and shows the note under it", () => {
    stub();
    // Exactly 30 days ago so isFollowUpDueToday passes (days === 30).
    const c = card({ last_note: { created_at: "2026-10-05T10:00:00Z", body: "Mandar correo esta semana", author: "Rafael" } }, "2026-09-06T12:00:00Z");
    renderToday([c]);
    const row = screen.getByTestId(`today-followup-${c.opportunity_id}`);
    // The day pill shows «30 d» for 30 days waiting; no prose text.
    expect(within(row).getByTestId("day-pill")).toHaveTextContent("30 d");
    expect(within(row).getByTestId("today-followup-note")).toHaveTextContent("Nota: Mandar correo esta semana · Rafael, 05 oct 2026");
  });

  it("a case your reminder brought back says so; one on its own clock does not", () => {
    stub();
    const fu = { task_id: "rem", title: "Seguimiento de 00001-26", due_at: "2026-10-06T09:00:00Z", version: 1, owner: "Rafael" };
    const reminded = card({ open_tasks: [fu] }, "2026-09-01T12:00:00Z");
    // Exactly 30 days ago so isFollowUpDueToday passes without a task (days === 30).
    const own = card({}, "2026-09-06T12:00:00Z");
    renderToday([reminded, own]);
    expect(within(screen.getByTestId("today-task-rem")).getByTestId("today-reminder-chip")).toHaveTextContent("Tu recordatorio");
    expect(within(screen.getByTestId(`today-followup-${own.opportunity_id}`)).queryByTestId("today-reminder-chip")).toBeNull();
  });

  it("puts off an unscheduled follow-up a week by writing it a «Seguimiento …» task", async () => {
    const calls = stub();
    // Exactly 3 days ago so isFollowUpDueToday passes (days === 3 = first threshold).
    const c = card({}, "2026-10-03T12:00:00Z");
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

describe("«Para ti» queue", () => {
  const inbound = (at: string, url = `https://mail.example.cl/${at}`) =>
    ({ last_contact: { outbound: null, inbound: { at, subject: "Consulta", url } } }) as Partial<OpportunityCardData>;

  it("puts clients waiting on us first, the oldest request on top, then follow-ups", async () => {
    const { buildParaTiQueue } = await import("../today");
    const fresh = card(inbound("2026-10-06T13:00:00Z"), "2026-10-01T12:00:00Z");
    const old = card(inbound("2026-09-28T12:00:00Z"), "2026-09-25T12:00:00Z");
    const silent14 = card({}, "2026-09-22T15:00:00Z");
    const order = buildParaTiQueue([silent14, old, fresh], NOW, null).map((q) => [q.kind, q.opportunity_id]);
    // «old» has been waiting 8 days, «fresh» only 2 hours — oldest first within each section.
    expect(order).toEqual([["reply", old.opportunity_id], ["reply", fresh.opportunity_id], ["followup", silent14.opportunity_id]]);
  });

  it("one email that answers two cases is one row: the other case takes no follow-up row", async () => {
    const { buildParaTiQueue } = await import("../today");
    const same = inbound("2026-10-06T10:00:00Z", "https://mail.example.cl/una-respuesta");
    const a = card(same, "2026-09-22T15:00:00Z");
    const b = card(same, "2026-09-22T15:00:00Z");
    const rows = buildParaTiQueue([a, b], NOW, null);
    expect(rows).toHaveLength(1);
    expect(rows[0].kind).toBe("reply");
  });

  it("the header count includes the forwarded requests waiting", async () => {
    const { paraTiCount } = await import("../today");
    const c = card(inbound("2026-10-06T13:00:00Z"), "2026-10-01T12:00:00Z");
    expect(paraTiCount([c], NOW, null)).toBe(1);
    expect(paraTiCount([c], NOW, null, 2)).toBe(3);
  });

  it("puts a pre-quote case with no revision into «Cotizar» (rank 0) before any reply", async () => {
    const { buildParaTiQueue } = await import("../today");
    // A case at «qualifying» with no sent quote, with an inbound from yesterday.
    const yesterday = new Date(NOW.getTime() - 86_400_000).toISOString();
    const solicitud = card(
      { ...inbound(yesterday), stage: "qualifying", latest_revision: null, quote_numbers: [], quotes: [] },
    );
    // A reply from 10 minutes ago — would normally rank first, but cotizar beats it.
    const tenMinAgo = new Date(NOW.getTime() - 600_000).toISOString();
    const reply = card(inbound(tenMinAgo));
    const order = buildParaTiQueue([reply, solicitud], NOW, null).map((q) => q.kind);
    expect(order[0]).toBe("cotizar");
    expect(order[1]).toBe("reply");
  });

  it("todayHeaderCounts counts cotizar, responder and seguimientos separately", async () => {
    const { todayHeaderCounts } = await import("../today");
    const yesterday = new Date(NOW.getTime() - 86_400_000).toISOString();
    const solicitud = card(
      { ...inbound(yesterday), stage: "qualifying", latest_revision: null, quote_numbers: [], quotes: [] },
    );
    const tenMinAgo = new Date(NOW.getTime() - 600_000).toISOString();
    const reply = card(inbound(tenMinAgo));
    const silent14 = card({}, "2026-09-22T15:00:00Z"); // 14 days → follow-up
    const counts = todayHeaderCounts([solicitud, reply, silent14], NOW, null);
    expect(counts.cotizar).toBe(1);
    expect(counts.responder).toBe(1);
    expect(counts.seguimientos).toBe(1);
  });
});

describe("«Cotizar» in Hoy opens the Cotizador", () => {
  const solicitud = () =>
    card({
      stage: "qualifying", latest_revision: null, quote_numbers: [], quotes: [],
      last_contact: { outbound: null, inbound: { at: "2026-10-05T12:00:00Z", subject: "Solicitud de equipo", url: null } },
    });

  function renderWith(items: OpportunityCardData[], role: string) {
    const navigate = vi.fn();
    render(
      <AuthSessionContext.Provider value={{ session: session(role), signOut: async () => true }}>
        <TodayBody items={items} navigate={navigate} onChanged={() => undefined} now={NOW} />
        <Toaster />
      </AuthSessionContext.Provider>,
    );
    return navigate;
  }

  it("sales: creates the quote and opens it", async () => {
    const c = solicitud();
    const calls: { path: string; method: string }[] = [];
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const path = new URL(url, "http://localhost").pathname;
      calls.push({ path, method: init?.method ?? "GET" });
      const body = path === "/v2/commands/create-quote"
        ? { ok: true, quote_id: "99999999-9999-4999-8999-999999999999", quote_number: "00042-26", revision_id: "r", version: 1 }
        : { items: [], total: 0 };
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
    }));
    const navigate = renderWith([c], "sales");
    const row = await screen.findByTestId(`today-cotizar-${c.opportunity_id}`);
    fireEvent.click(within(row).getByRole("button", { name: "Cotizar" }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("cotizador", "99999999-9999-4999-8999-999999999999"));
    expect(calls.filter((x) => x.method === "POST").map((x) => x.path)).toEqual(["/v2/commands/create-quote"]);
  });

  it("a first quote still a draft at «Cotizando» is «Cotizar», never «Responder» to the request", async () => {
    const draftRev = { ...rev("2026-10-05T12:00:00Z"), revision_id: "r-draft2", status: "draft", origin: "authored", sent_at: null, quote_number: "00043-26" };
    const c = card({
      stage: "quoting", latest_revision: draftRev, quote_numbers: ["00043-26"],
      quotes: [{ quote_id: "77777777-7777-4777-8777-777777777777", quote_number: "00043-26", number_origin: "minted", revisions: [draftRev] }],
      last_contact: { outbound: null, inbound: { at: "2026-10-05T12:00:00Z", subject: "Solicitud de equipo", url: null } },
    });
    const { buildParaTiQueue } = await import("../today");
    const { conversation } = await import("../caseDisplay");
    expect(buildParaTiQueue([c], NOW, null).map((q) => q.kind)).toEqual(["cotizar"]);
    expect(conversation(c, NOW)).toMatchObject({ kind: "none", text: "Cotización en preparación" });
  });

  it("a case whose quote is still a draft stays in «Cotizar»: «Continuar cotización» opens the draft, no POST", async () => {
    const draftRev = { ...rev("2026-10-05T12:00:00Z"), revision_id: "r-draft", status: "draft", origin: "authored", sent_at: null, quote_number: "00042-26" };
    const c = card({
      stage: "qualifying", latest_revision: draftRev, quote_numbers: ["00042-26"],
      quotes: [{ quote_id: "88888888-8888-4888-8888-888888888888", quote_number: "00042-26", number_origin: "minted", revisions: [draftRev] }],
      last_contact: { outbound: null, inbound: { at: "2026-10-05T12:00:00Z", subject: "Solicitud de equipo", url: null } },
    });
    const sentRev = { ...draftRev, revision_id: "r-sent", status: "sent", sent_at: "2026-10-05T13:00:00Z" };
    const sent = card({ stage: "qualifying", latest_revision: sentRev, quotes: [{ quote_id: "q-s", quote_number: "00042-26", number_origin: "minted", revisions: [sentRev] }] });
    const { buildParaTiQueue } = await import("../today");
    expect(buildParaTiQueue([c, sent], NOW, null).filter((q) => q.kind === "cotizar").map((q) => q.opportunity_id)).toEqual([c.opportunity_id]);
    const calls = stub();
    const navigate = renderWith([c], "sales");
    const row = await screen.findByTestId(`today-cotizar-${c.opportunity_id}`);
    fireEvent.click(within(row).getByRole("button", { name: "Continuar cotización" }));
    expect(navigate).toHaveBeenCalledWith("cotizador", "88888888-8888-4888-8888-888888888888");
    expect(calls.filter((x) => x.path.startsWith("/v2/commands/"))).toEqual([]);
  });

  it("a viewer keeps the old behaviour: the case opens, nothing is created", async () => {
    const c = solicitud();
    const calls = stub();
    const navigate = renderWith([c], "viewer");
    const row = await screen.findByTestId(`today-cotizar-${c.opportunity_id}`);
    fireEvent.click(within(row).getByRole("button", { name: "Cotizar" }));
    expect(navigate).toHaveBeenCalledWith("oportunidades", c.opportunity_id);
    expect(calls.filter((x) => x.path.startsWith("/v2/commands/"))).toEqual([]);
  });

  it("a refusal is said in a toast and nothing opens", async () => {
    const c = solicitud();
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const path = new URL(url, "http://localhost").pathname;
      return Promise.resolve(path === "/v2/commands/create-quote"
        ? new Response(JSON.stringify({ detail: { code: "quote_numbering_not_configured", message: "Falta configurar la numeración de cotizaciones (parámetro quote_number_seed)." } }), { status: 409, headers: { "Content-Type": "application/json" } })
        : new Response(JSON.stringify({ items: [], total: 0 }), { status: 200, headers: { "Content-Type": "application/json" } }));
    }));
    const navigate = renderWith([c], "admin");
    const row = await screen.findByTestId(`today-cotizar-${c.opportunity_id}`);
    fireEvent.click(within(row).getByRole("button", { name: "Cotizar" }));
    expect(await screen.findByText(/^Falta configurar la numeración de cotizaciones/)).toBeInTheDocument();
    expect(navigate).not.toHaveBeenCalledWith("cotizador", expect.anything());
  });
});

describe("«Licitación cierra mañana»", () => {
  const TENDER = "55555555-5555-4555-8555-555555555555";
  const closing = [{ id: TENDER, code: "1234567-12-LE26", title: "Compra de balanzas de ejemplo", closes_at: "2026-10-07T18:00:00Z" }];

  function renderTenders(tendersClosing: typeof closing | undefined, items: OpportunityCardData[] = []) {
    const navigate = vi.fn();
    render(
      <AuthSessionContext.Provider value={{ session: session("sales"), signOut: async () => true }}>
        <TodayBody items={items} navigate={navigate} onChanged={() => undefined} now={NOW} tendersClosing={tendersClosing} />
      </AuthSessionContext.Provider>,
    );
    return navigate;
  }

  it("shows one line per tender that closes tomorrow, linking to its page", async () => {
    stub();
    const navigate = renderTenders(closing);
    const link = await screen.findByRole("link", { name: "Licitación cierra mañana: Compra de balanzas de ejemplo" });
    expect(link).toHaveAttribute("href", `#/crm/licitaciones/${TENDER}`);
    fireEvent.click(link);
    expect(navigate).toHaveBeenCalledWith("licitaciones", TENDER);
  });

  it("shows nothing when no tender closes tomorrow or the API sends none", async () => {
    stub();
    renderTenders([]);
    await screen.findByText("No hay nada que hacer ahora mismo.");
    expect(screen.queryByText(/Licitación cierra mañana/)).toBeNull();
  });

  it("a case never turns into a tender line: only tenders_closing does", async () => {
    stub();
    renderTenders(undefined, [card({ title: "Licitación 1234567-12-LE26 — Compra" })]);
    await screen.findByText("No hay nada que hacer ahora mismo.");
    expect(screen.queryByText(/Licitación cierra mañana/)).toBeNull();
  });
});
