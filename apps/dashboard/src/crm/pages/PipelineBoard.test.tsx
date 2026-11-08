import "@testing-library/jest-dom";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import type { OpportunityCardData, RevisionCard } from "../crmTypes";
import { Toaster } from "../ui";
import { clearResourceCache } from "../useResource";
import { PipelinePage } from "./PipelinePage";
import { stagePath } from "../caseCommands";
import { activityTimestamp, ageBucket, boardColumnOf, boardStatusLine, pausedUntil } from "../stage";
import { moveRefusal } from "./CaseMove";
import { Board, cardMoney, columnMoney, sortCards } from "./PipelineBoard";

// Every value below is invented; the repository is public.
const NOW = new Date("2026-10-06T12:00:00Z");

function rev(sentAt: string, over: Partial<RevisionCard> = {}): RevisionCard {
  return {
    revision_id: `rev-${sentAt}`,
    revision_no: 1,
    status: "sent",
    origin: "historical_import",
    sent_at: sentAt,
    superseded_by_revision_no: null,
    is_active: true,
    document: null,
    gmail: { source_record_id: "s", message_id: "m", thread_id: "t", url: "https://mail.example.cl/m", subject: "x" },
    drive: null,
    quote_number: "01239-26",
    ...over,
  };
}

let n = 0;
function card(over: Partial<OpportunityCardData> = {}, sentAt: string | null = "2026-09-20T12:00:00Z"): OpportunityCardData {
  n += 1;
  const latest = sentAt ? rev(sentAt) : null;
  return {
    opportunity_id: `case-${n}`,
    title: `Caso ficticio ${n}`,
    stage: "quoting",
    version: 1,
    created_at: null,
    updated_at: null,
    closed_at: null,
    close_reason: null,
    organization: { organization_id: `org-${n}`, name: `Universidad Ficticia ${n}`, confirmation: "confirmed", version: 1 },
    other_organizations: [],
    contact: { source: "crm_participant", name: "Persona Ficticia", address: null, others: 0 },
    quotes: latest
      ? [{ quote_id: `q-${n}`, quote_number: "01239-26", number_origin: "printed_historical", revisions: [latest] }]
      : [],
    quote_numbers: latest ? ["01239-26"] : [],
    revision_count: latest ? 1 : 0,
    latest_revision: latest,
    drive_folder: null,
    attention: [],
    status: "pending",
    next_action: {
      text: "Hacer seguimiento de 01239-26 (enviada 20-09-2026) con el laboratorio de química, que pidió plazo de entrega",
      source: "suggested",
      due_at: null,
    },
    ...over,
  };
}

describe("board helpers", () => {
  it("groups a touch by its age, finer this month, and a missing date apart", () => {
    expect(ageBucket("2026-10-06T08:00:00Z", NOW)).toBe("hoy");
    expect(ageBucket("2026-10-01T00:00:00Z", NOW)).toBe("semana");
    expect(ageBucket("2026-09-25T00:00:00Z", NOW)).toBe("semana2");
    expect(ageBucket("2026-09-10T00:00:00Z", NOW)).toBe("mes");
    expect(ageBucket("2026-08-01T00:00:00Z", NOW)).toBe("d90");
    expect(ageBucket("2026-05-01T00:00:00Z", NOW)).toBe("d365");
    expect(ageBucket("2025-12-01T00:00:00Z", NOW)).toBe("d365");
    expect(ageBucket("2024-01-01T00:00:00Z", NOW)).toBe("older");
    expect(ageBucket(null, NOW)).toBe("none");
    expect(ageBucket("no es una fecha", NOW)).toBe("none");
  });

  it("says one thing per card: the blocker, or the gaps", () => {
    expect(boardStatusLine(card())).toEqual({ text: "sin decidir · sin Drive", tone: "warn" });
    expect(boardStatusLine(card({ contact: null }, null))).toEqual({ text: "sin cotización · sin contacto", tone: "warn" });
    expect(
      boardStatusLine(card({ attention: [{ code: "canonical_undetermined", label: "Hay más de una revisión vigente", blocking: true }] })),
    ).toEqual({ text: "Hay más de una revisión vigente", tone: "bad" });
    const done = card({ drive_folder: { source: "archive_ledger", folder_id: "f", url: "https://drive.example.cl/f" }, status: "ok" });
    expect(boardStatusLine(done)).toEqual({ text: "sin decidir · al día", tone: "neutral" });
  });
});

describe("Tablero", () => {
  it("counts every column and leads each card with the institution, the contact and the quote — one clock, on the status line", () => {
    const lead = card({ stage: "lead" }, null);
    const quoting = card();
    render(<Board cards={[lead, quoting]} onOpen={() => undefined} now={NOW} />);
    expect(screen.getByTestId("board-count-solicitada")).toHaveTextContent("1");
    expect(screen.getByTestId("board-count-enviada")).toHaveTextContent("1");
    for (const key of ["conversacion", "pausa", "ganada", "perdida"]) {
      expect(screen.getByTestId(`board-count-${key}`)).toHaveTextContent("0");
    }
    const b = screen.getByTestId(`board-card-${quoting.opportunity_id}`);
    expect(within(b).getByRole("button", { name: /^Universidad Ficticia \d+$/ })).toBeInTheDocument();
    expect(b).toHaveTextContent("Persona Ficticia");
    expect(within(b).queryByTestId("board-age")).toBeNull(); // no second «last movement» clock
    expect(b).toHaveTextContent("01239-26 · 20 sept");
    expect(within(b).getByTestId("board-status-line")).toHaveTextContent("Sin respuesta · 16 d");
    expect(within(b).getByRole("link", { name: /Correo de la cotización/ })).toHaveAttribute("href", "https://mail.example.cl/m");
    expect(b).toHaveTextContent("sin Drive");
    expect(b).not.toHaveTextContent("histórico");
  });

  it("says when the client replied after the quote, and leads with a readable name for a domain", () => {
    const c = card({
      organization: { organization_id: "o", name: "ejemplo.cl", confirmation: "machine_proposed", version: 1 },
      last_contact: { outbound: null, inbound: { at: "2026-10-02T15:00:00Z", subject: "Re: cotización", url: null } },
    });
    c.latest_revision = rev("2026-09-20T12:00:00Z", { document: { sha256: "a", filename: "CN01239-Ana Ficticia – Laboratorio Ejemplo-UP400St.pdf" } });
    render(<Board cards={[c]} onOpen={() => undefined} now={NOW} />);
    const b = screen.getByTestId(`board-card-${c.opportunity_id}`);
    expect(within(b).getByRole("button", { name: "Laboratorio Ejemplo" })).toHaveAttribute("title", "En el CRM: ejemplo.cl");
    expect(b).toHaveTextContent("UP400St");
    expect(within(b).getByTestId("board-status-line")).toHaveTextContent(/^Respondió 02 oct · te toca$/);
  });

  it("shows the whole suggestion as the card's tooltip and opens the case", () => {
    const c = card();
    const onOpen = vi.fn();
    render(<Board cards={[c]} onOpen={onOpen} now={NOW} />);
    const b = screen.getByTestId(`board-card-${c.opportunity_id}`);
    expect(b).toHaveAttribute("title", `Sugerencia: ${c.next_action.text}`);
    fireEvent.click(b);
    expect(onOpen).toHaveBeenCalledWith(c.opportunity_id);
    // A link on the card opens its target, not the drawer.
    onOpen.mockClear();
    fireEvent.click(within(b).getByRole("link", { name: /Correo/ }));
    expect(onOpen).not.toHaveBeenCalled();
  });

  it("scrolls each column on its own", () => {
    render(<Board cards={[card()]} onOpen={() => undefined} now={NOW} />);
    expect(screen.getByTestId("board-column-enviada").className).toMatch(/overflow-y-auto/);
    expect(screen.getByTestId("board-column-enviada").className).toMatch(/max-h-/);
  });

  it("shows every card of a long column, newest first, with a thin divider per age band", () => {
    // «Conversación» bands by the last touch; «Enviada» has its own two bands (below).
    const talking = { stage: "negotiating" };
    const recent = Array.from({ length: 3 }, () => card(talking, "2026-09-25T12:00:00Z"));
    const answered = { ...talking, last_contact: { inbound: { at: "2025-01-10T14:00:00Z", subject: "Re: cotización", url: null }, outbound: null } };
    const old = Array.from({ length: 8 }, () => card(answered, "2025-01-10T12:00:00Z"));
    const none = card(talking, null);
    render(<Board cards={[...old, none, ...recent]} onOpen={() => undefined} now={NOW} />);
    const column = screen.getByTestId("board-column-conversacion");
    expect(screen.getByTestId("board-count-conversacion")).toHaveTextContent("12");
    expect(within(column).getAllByRole("article")).toHaveLength(12);
    expect(within(column).queryAllByRole("group")).toHaveLength(0);
    expect(
      within(column)
        .getAllByText(/Semana pasada|Más de un año/)
        .map((e) => e.textContent),
    ).toEqual(["Semana pasada", "Más de un año"]);
  });

  it("bands and the status line run on the same clock: a follow-up email moves a card up and counts the silence from it", () => {
    const silent = card({}, "2026-07-01T12:00:00Z");
    const followed = card({ last_contact: { outbound: { at: "2026-10-04T10:00:00Z", subject: "Seguimiento", url: null }, inbound: null } }, "2026-07-01T12:00:00Z");
    const ids = (xs: OpportunityCardData[]) => xs.map((x) => x.opportunity_id);
    expect(ids(sortCards([silent, followed], "recent", NOW))).toEqual(ids([followed, silent]));
    expect(ageBucket(new Date(activityTimestamp(followed)!).toISOString(), NOW)).toBe("semana");
    expect(ageBucket(new Date(activityTimestamp(silent)!).toISOString(), NOW)).toBe("d365");
    // An imported case created last week with a July quote sits with July, not with last week.
    const imported = card({ created_at: "2026-10-01T12:00:00Z" }, "2026-07-01T12:00:00Z");
    expect(ageBucket(new Date(activityTimestamp(imported)!).toISOString(), NOW)).toBe("d365");
    render(<Board cards={[silent, followed]} onOpen={() => undefined} now={NOW} />);
    const f = screen.getByTestId(`board-card-${followed.opportunity_id}`);
    expect(within(f).getByTestId("board-status-line")).toHaveTextContent("Seguimiento 04 oct · 2 d sin respuesta");
    const s = screen.getByTestId(`board-card-${silent.opportunity_id}`);
    expect(within(s).getByTestId("board-status-line")).toHaveTextContent("Sin respuesta · 97 d");
  });

  it("a note is a reminder, not a contact: it never moves the clock", () => {
    const noted = card({ last_note: { created_at: "2026-10-04T10:00:00Z", body: "Mandar correo esta semana", author: "Ana" } }, "2026-07-01T12:00:00Z");
    render(<Board cards={[noted]} onOpen={() => undefined} now={NOW} />);
    const n = screen.getByTestId(`board-card-${noted.opportunity_id}`);
    expect(within(n).getByTestId("board-status-line")).toHaveTextContent("Sin respuesta · 97 d");
  });

  it("a note never hides the client's email: after a reply the card still says «te toca»", () => {
    const c = card({
      last_contact: { outbound: { at: "2026-09-28T12:00:00Z", subject: "Seguimiento", url: null }, inbound: { at: "2026-10-02T15:00:00Z", subject: "Re", url: null } },
      last_note: { created_at: "2026-10-03T09:00:00Z", body: "visto", author: null },
    });
    render(<Board cards={[c]} onOpen={() => undefined} now={NOW} />);
    const b = screen.getByTestId(`board-card-${c.opportunity_id}`);
    expect(within(b).getByTestId("board-status-line")).toHaveTextContent(/^Respondió 02 oct · te toca$/);
  });

  it("sorts by date either way, by who replied, or by name", () => {
    const a = card({}, "2026-09-01T12:00:00Z");
    const b = card(
      { last_contact: { outbound: null, inbound: { at: "2026-10-01T12:00:00Z", subject: null, url: null } } },
      "2026-08-01T12:00:00Z",
    );
    const c = card({}, "2026-10-01T12:00:00Z");
    const ids = (xs: OpportunityCardData[]) => xs.map((x) => x.opportunity_id);
    // A customer reply is new case activity even when the PDF was sent months ago.
    expect(ids(sortCards([a, b, c], "recent", NOW))).toEqual(ids([b, c, a]));
    expect(ids(sortCards([a, b, c], "oldest", NOW))).toEqual(ids([a, b, c]));
    expect(ids(sortCards([a, b, c], "replied", NOW))[0]).toBe(b.opportunity_id);
  });

  it("a record edit is not contact: a case moved yesterday with an old quote sorts by the quote, a followed-up one by the follow-up", () => {
    const moved = card({
      title: "Caso movido ayer",
      stage: "negotiating",
      updated_at: "2026-10-05T19:00:00Z",
      last_contact: { inbound: null, outbound: null },
    }, "2026-05-01T12:00:00Z");
    const followed = card({
      title: "Caso con seguimiento",
      stage: "negotiating",
      updated_at: "2026-09-26T19:00:00Z",
      last_contact: { inbound: null, outbound: { at: "2026-09-30T12:00:00Z", subject: "Seguimiento", url: null } },
    }, "2026-09-25T12:00:00Z");
    const sorted = sortCards([moved, followed], "recent", NOW);
    expect(sorted.map((c) => c.opportunity_id)).toEqual([followed.opportunity_id, moved.opportunity_id]);
    render(<Board cards={[moved, followed]} onOpen={() => undefined} now={NOW} />);
    const col = screen.getByTestId("board-column-conversacion");
    expect(within(col).getAllByRole("article")[0])
      .toBe(within(col).getByTestId(`board-card-${followed.opportunity_id}`));
  });

  it("keeps cases with unknown activity dates last even under oldest", () => {
    const noDate = card({ created_at: null, updated_at: null }, null);
    const known = card({ created_at: "2026-10-05T12:00:00Z" }, null);
    expect(sortCards([noDate, known], "recent", NOW)[0]).toBe(known);
    expect(sortCards([noDate, known], "oldest", NOW)[0]).toBe(known);
  });

  it("puts a case with a later open task under «En pausa», and a due one back in its column", () => {
    const later = card({
      open_tasks: [{ task_id: "t1", title: "Retomar: Esperando la orden de compra", due_at: "2026-11-02T12:00:00Z", version: 1, owner: null }],
      next_action: { text: "Retomar: Esperando la orden de compra", source: "task", due_at: "2026-11-02T12:00:00Z" },
    });
    const due = card({
      open_tasks: [{ task_id: "t2", title: "Llamar", due_at: "2026-10-01T12:00:00Z", version: 1, owner: null }],
    });
    render(<Board cards={[later, due]} onOpen={() => undefined} now={NOW} />);
    expect(screen.getByTestId("board-count-pausa")).toHaveTextContent("1");
    expect(screen.getByTestId("board-count-enviada")).toHaveTextContent("1");
    const b = screen.getByTestId(`board-card-${later.opportunity_id}`);
    expect(within(b).getByTestId("board-status-line")).toHaveTextContent("Hasta 02 nov 2026 · Retomar: Esperando la orden de compra");
    expect(b).toHaveAttribute("title", "Próxima tarea: Retomar: Esperando la orden de compra");
  });

  it("is read-only without onMove, and reports a drop on another column with it", () => {
    const c = card();
    const { unmount } = render(<Board cards={[c]} onOpen={() => undefined} now={NOW} />);
    expect(screen.getByTestId(`board-card-${c.opportunity_id}`)).toHaveAttribute("draggable", "false");
    unmount();

    const onMove = vi.fn();
    render(<Board cards={[c]} onOpen={() => undefined} onMove={onMove} now={NOW} />);
    const b = screen.getByTestId(`board-card-${c.opportunity_id}`);
    expect(b).toHaveAttribute("draggable", "true");
    const data: Record<string, string> = {};
    const dataTransfer = {
      setData: (k: string, v: string) => {
        data[k] = v;
      },
      getData: (k: string) => data[k] ?? "",
      effectAllowed: "",
      dropEffect: "",
    };
    fireEvent.dragStart(b, { dataTransfer });
    // Its own column takes no drop.
    fireEvent.drop(screen.getByRole("region", { name: "Enviada" }), { dataTransfer });
    expect(onMove).not.toHaveBeenCalled();
    fireEvent.dragStart(b, { dataTransfer });
    const target = screen.getByRole("region", { name: "Perdida" });
    fireEvent.dragOver(target, { dataTransfer });
    expect(target).toHaveAttribute("data-drop-target", "true");
    fireEvent.drop(target, { dataTransfer });
    expect(onMove).toHaveBeenCalledWith(c, "perdida");
  });
});

describe("states", () => {
  it("walks the stage table one step at a time, never through «won» or a closed stage", () => {
    expect(stagePath("quoting", "negotiating")).toEqual(["negotiating"]);
    expect(stagePath("lead", "quoting")).toEqual(["qualifying", "qualified", "quoting"]);
    expect(stagePath("quoting", "lead")).toEqual(["qualified", "qualifying", "lead"]);
    expect(stagePath("negotiating", "lost")).toEqual(["lost"]);
    expect(stagePath("quoting", "quoting")).toEqual([]);
    expect(stagePath("quoting", "won")).toBeNull();
    // A lost or abandoned case reopens in one move, straight to an open stage (2026-10-10).
    expect(stagePath("lost", "lead")).toEqual(["lead"]);
    expect(stagePath("abandoned", "negotiating")).toEqual(["negotiating"]);
    expect(stagePath("lost", "abandoned")).toBeNull();
    expect(stagePath("lost", "won")).toBeNull();
    expect(stagePath("won", "negotiating")).toBeNull();
  });

  it("refuses to move a won case or a case an undo discarded, and a closed case only reopens to an open column", () => {
    const lost = card({ stage: "lost", closed_at: "2026-10-01T00:00:00Z", close_reason: "precio", version: 3,
      organization: { organization_id: "o", name: "Uni", confirmation: "confirmed" }, requesting_institution_confirmation: "confirmed" });
    expect(moveRefusal(lost, "conversacion", NOW)).toBeNull();
    expect(moveRefusal(lost, "solicitada", NOW)).toBeNull();
    expect(moveRefusal(lost, "pausa", NOW)).toMatch(/se reabre/);
    expect(moveRefusal({ ...lost, close_reason: "discarded_by_correction" }, "solicitada", NOW)).toMatch(/descartó/);
    expect(moveRefusal({ ...lost, stage: "won" }, "conversacion", NOW)).toMatch(/ganado/);
  });

  it("pauses only an open case, and only until its earliest task", () => {
    const task = { task_id: "t", title: "Retomar", due_at: "2026-11-02T12:00:00Z", version: 1, owner: null };
    expect(pausedUntil(card({ open_tasks: [task] }), NOW)).toBe("2026-11-02T12:00:00Z");
    expect(pausedUntil(card({ open_tasks: [task], stage: "lost", closed_at: "2026-10-01T00:00:00Z" }), NOW)).toBeNull();
    expect(pausedUntil(card({ open_tasks: [{ ...task, due_at: "2026-10-06T11:00:00Z" }] }), NOW)).toBeNull();
    expect(boardColumnOf(card({ stage: "qualifying" }), NOW)).toBe("solicitada");
    expect(boardColumnOf(card({ stage: "abandoned", closed_at: "2026-10-01T00:00:00Z" }), NOW)).toBe("perdida");
    expect(boardColumnOf(card({ open_tasks: [task] }), NOW)).toBe("pausa");
  });

  it("keeps closed cases compact, and writes the year only when it is not this one", () => {
    const won = card({ stage: "won", closed_at: "2026-10-01T00:00:00Z" }, "2025-11-03T12:00:00Z");
    render(<Board cards={[won]} onOpen={() => undefined} now={NOW} />);
    const b = screen.getByTestId(`board-card-${won.opportunity_id}`);
    expect(b).toHaveTextContent("01239-26 · 3 nov 2025");
    expect(b).not.toHaveTextContent("Persona Ficticia");
    expect(within(b).queryByRole("link")).not.toBeInTheDocument();
    expect(within(b).getByTestId("board-status-line")).toHaveTextContent("Ganada");
  });

  it("shows what each quote is for, open or closed", () => {
    const open = card();
    (open.latest_revision as RevisionCard).gmail = { source_record_id: "s", message_id: "m", thread_id: "t", url: "https://mail.example.cl/m", subject: "Cotización pipetas pasteur" };
    const lost = card({ stage: "abandoned", closed_at: "2026-10-01T00:00:00Z", close_reason: "Sin respuesta" });
    (lost.latest_revision as RevisionCard).document = { sha256: "b".repeat(64), filename: "CN01239-Persona Ficticia - Universidad Ficticia-UP400St.pdf" };
    render(<Board cards={[open, lost]} onOpen={() => undefined} now={NOW} />);
    expect(within(screen.getByTestId(`board-card-${open.opportunity_id}`)).getByTestId("board-product")).toHaveTextContent("Pipetas pasteur");
    expect(within(screen.getByTestId(`board-card-${lost.opportunity_id}`)).getByTestId("board-product")).toHaveTextContent("UP400St");
  });
});


describe("money per column", () => {
  it("adds the CLP neto of a column and counts cases whose quotes were never read", () => {
    const read = card({ money: [{ currency: "CLP", net_total: "1000000" }] });
    const partly = card({ money: [{ currency: "CLP", net_total: "250000" }, { currency: "unread", net_total: "1" }] });
    const unread = card({ money: [{ currency: "unread", net_total: "1" }] });
    const noQuote = card({ quotes: [], money: [] }, null);
    expect(columnMoney([read, partly, unread, noQuote])).toEqual({ clp: 1250000, unread: 2 });
    render(<Board cards={[read, partly]} onOpen={() => undefined} now={NOW} />);
    expect(screen.getByTestId("board-money-enviada")).toHaveTextContent("$ 1.250.000 neto + 1 sin leer");
    expect(screen.getByTestId("board-money-solicitada")).toHaveTextContent("$ 0 neto");
    expect(screen.getByRole("heading", { name: "Solicitada / En estudio" })).toBeInTheDocument();
  });
});

describe("Board · long columns", () => {
  it("draws a long column 40 cards at a time, counting and totalling all of them", () => {
    let seen: IntersectionObserverCallback = () => undefined;
    vi.stubGlobal("IntersectionObserver", class {
      constructor(cb: IntersectionObserverCallback) { seen = cb; }
      observe() {}
      disconnect() {}
    });
    try {
      const lost = Array.from({ length: 100 }, () =>
        card({ stage: "lost", closed_at: "2026-10-02T12:00:00Z", close_reason: "Sin respuesta" }));
      render(<Board cards={lost} onOpen={() => undefined} now={NOW} />);
      const column = screen.getByTestId("board-column-perdida");
      expect(screen.getByTestId("board-count-perdida")).toHaveTextContent("100");
      expect(within(column).getAllByTestId(/^board-card-/)).toHaveLength(40);
      fireEvent.click(within(column).getByRole("button", { name: "Mostrar más (60)" }));
      expect(within(column).getAllByTestId(/^board-card-/)).toHaveLength(80);
      // Scrolling the end into view draws the rest.
      act(() => seen([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver));
      expect(within(column).getAllByTestId(/^board-card-/)).toHaveLength(100);
      expect(within(column).queryByRole("button", { name: /Mostrar más/ })).not.toBeInTheDocument();
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe("Tablero · board rules (Task 8)", () => {
  const silentCard = () => {
    const c = card({}, "2026-09-14T12:00:00Z"); // 22 whole days before NOW, nobody answered
    c.latest_revision = rev("2026-09-14T12:00:00Z", { origin: "authored" });
    c.quotes[0].revisions = [c.latest_revision];
    return c;
  };

  it("Silenciosas column holds the 22-day card with «Seguimiento N de 3» and «¿Marcar perdida?»", () => {
    const silent = silentCard();
    const onLose = vi.fn();
    render(<Board cards={[silent, card()]} onOpen={() => undefined} onMove={() => undefined} onLose={onLose} now={NOW} />);
    const column = screen.getByTestId("board-column-silenciosas");
    expect(screen.getByTestId("board-count-silenciosas")).toHaveTextContent("1");
    const b = within(column).getByTestId(`board-card-${silent.opportunity_id}`);
    expect(within(b).getByTestId("board-followup-step")).toHaveTextContent("Seguimiento 2 de 3");
    const lose = within(b).getByRole("button", { name: `¿Marcar perdida? ${silent.organization?.name}` });
    expect(lose).toHaveTextContent("¿Marcar perdida?");
    expect(lose.className).toContain("min-h-11");
    fireEvent.click(lose);
    expect(onLose).toHaveBeenCalledWith(silent);
  });

  it("offers no «¿Marcar perdida?» on a read-only board, and an empty Silenciosas says how it fills", () => {
    const silent = silentCard();
    const { unmount } = render(<Board cards={[silent]} onOpen={() => undefined} onLose={() => undefined} now={NOW} />);
    expect(screen.queryByRole("button", { name: /^¿Marcar perdida\?/ })).toBeNull();
    unmount();
    render(<Board cards={[card()]} onOpen={() => undefined} now={NOW} />);
    expect(screen.getByTestId("board-column-silenciosas")).toHaveTextContent("Ninguna cotización lleva más de 21 días sin respuesta");
  });

  it("Enviada shows «0–7 días» and «8–21 días» dividers when sorted by date", () => {
    const fresh = card({}, "2026-10-03T12:00:00Z"); // 3 days
    const older = card({}, "2026-09-24T12:00:00Z"); // 12 days
    // Day 30, but the client answered: not silent, it sits under «8–21 días» (controller ruling).
    const answered = card({ last_contact: { inbound: { at: "2026-09-10T14:00:00Z", subject: "Re", url: null }, outbound: null } }, "2026-09-06T12:00:00Z");
    const { unmount } = render(<Board cards={[answered, older, fresh]} onOpen={() => undefined} now={NOW} />);
    const column = screen.getByTestId("board-column-enviada");
    expect(within(column).getAllByTestId(/^board-band-/).map((e) => e.textContent)).toEqual(["0–7 días", "8–21 días"]);
    const order = within(column).getAllByTestId(/^board-(band|card)-/).map((e) => e.getAttribute("data-testid"));
    expect(order).toEqual([
      "board-band-d0_7",
      `board-card-${fresh.opportunity_id}`,
      "board-band-d8_21",
      `board-card-${older.opportunity_id}`,
      `board-card-${answered.opportunity_id}`,
    ]);
    unmount();
    render(<Board cards={[answered, older, fresh]} onOpen={() => undefined} sort="oldest" now={NOW} />);
    expect(within(screen.getByTestId("board-column-enviada")).getAllByTestId(/^board-band-/).map((e) => e.textContent)).toEqual([
      "8–21 días",
      "0–7 días",
    ]);
  });

  it("a draft-only Enviada card (nothing delivered yet) sits under «0–7 días», not «8–21 días»", () => {
    const draft = card({}, "2026-09-01T12:00:00Z");
    draft.latest_revision = rev("2026-09-01T12:00:00Z", { status: "draft", sent_at: null, origin: "authored" });
    draft.quotes[0].revisions = [draft.latest_revision];
    const older = card({}, "2026-09-24T12:00:00Z"); // 12 days
    render(<Board cards={[older, draft]} onOpen={() => undefined} now={NOW} />);
    const column = screen.getByTestId("board-column-enviada");
    const order = within(column).getAllByTestId(/^board-(band|card)-/).map((e) => e.getAttribute("data-testid"));
    expect(order).toEqual([
      "board-band-d0_7",
      `board-card-${draft.opportunity_id}`,
      "board-band-d8_21",
      `board-card-${older.opportunity_id}`,
    ]);
  });

  it("no Enviada dividers when sorted by name", () => {
    render(<Board cards={[card({}, "2026-10-03T12:00:00Z"), card({}, "2026-09-24T12:00:00Z")]} onOpen={() => undefined} sort="name" now={NOW} />);
    expect(within(screen.getByTestId("board-column-enviada")).queryAllByTestId(/^board-band-/)).toHaveLength(0);
  });

  it("card shows CN01318, «rev 2» and only the current neto", () => {
    const c = card({
      money: [
        { currency: "CLP", net_total: "1180000", quote_number: "CN01318", revision_no: 2, quote_count: 2 },
        { currency: "CLP", net_total: "990000", quote_number: "CN01317", revision_no: 1 },
      ],
      quote_numbers: ["CN01318", "CN01317"],
    });
    expect(cardMoney(c)).toEqual({ text: "$ 1.180.000", cn: "CN01318", rev: 2 });
    render(<Board cards={[c]} onOpen={() => undefined} now={NOW} />);
    const b = screen.getByTestId(`board-card-${c.opportunity_id}`);
    expect(b).toHaveTextContent("CN01318");
    expect(within(b).getByTestId("board-rev")).toHaveTextContent("rev 2");
    expect(within(b).getByTestId("board-money")).toHaveTextContent("$ 1.180.000");
    expect(b).not.toHaveTextContent("990.000");
  });

  it("an unread quote reads «sin leer»; a case with no money shows no amount", () => {
    expect(cardMoney(card({ money: [{ currency: "unread", net_total: "1", quote_number: "CN01319", revision_no: 1 }] }))).toEqual({
      text: "sin leer",
      cn: "CN01319",
      rev: 1,
    });
    expect(cardMoney(card({ money: [] }))).toEqual({ text: "", cn: "01239-26", rev: null });
  });

  it("an old loss is not on the board", () => {
    const old = card({ stage: "lost", closed_at: "2026-09-20T12:00:00Z", close_reason: "Precio" });
    const recent = card({ stage: "abandoned", closed_at: "2026-10-02T12:00:00Z", close_reason: "Sin respuesta" });
    render(<Board cards={[old, recent]} onOpen={() => undefined} now={NOW} />);
    expect(screen.getByTestId("board-count-perdida")).toHaveTextContent("1");
    expect(screen.queryByTestId(`board-card-${old.opportunity_id}`)).toBeNull();
    expect(screen.getByTestId(`board-card-${recent.opportunity_id}`)).toBeInTheDocument();
  });

  it("Ganada keeps a won case from last year", () => {
    const won = card({ stage: "won", closed_at: "2025-08-01T12:00:00Z" }, "2025-07-01T12:00:00Z");
    render(<Board cards={[won]} onOpen={() => undefined} now={NOW} />);
    expect(within(screen.getByTestId("board-column-ganada")).getByTestId(`board-card-${won.opportunity_id}`)).toBeInTheDocument();
  });
});

describe("Pipeline page · Silenciosas (Task 8)", () => {
  const DAY = 86_400_000;
  const ago = (ms: number) => new Date(Date.now() - ms).toISOString();

  function silentNow(): OpportunityCardData {
    const r = rev(ago(22 * DAY), { origin: "authored", revision_id: "rev-silent" });
    return card({
      opportunity_id: "44444444-4444-4444-8444-444444444444",
      organization: { organization_id: "org-s", name: "Laboratorio Callado", confirmation: "confirmed", version: 1 },
      requesting_institution_confirmation: "confirmed",
      quotes: [{ quote_id: "q-s", quote_number: "01239-26", number_origin: "printed_historical", revisions: [r] }],
      latest_revision: r,
      version: 3,
    });
  }

  function renderPage(items: OpportunityCardData[]) {
    window.location.hash = "#/ventas/pipeline";
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const path = new URL(url, "http://localhost").pathname;
      if (path === "/v2/workspace/pipeline") {
        return Promise.resolve(new Response(JSON.stringify({ items, total: items.length, drive_configured: true }),
          { status: 200, headers: { "Content-Type": "application/json" } }));
      }
      return Promise.resolve(new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "Content-Type": "application/json" } }));
    }));
    const session: AuthSessionState = {
      kind: "signed_in",
      method: "google_profile",
      operator: { operatorId: "op-1", email: "ventas@example.cl", displayName: "Ventas", role: "sales" },
      caseCommandsEnabled: true,
      crmAuthoringEnabled: true,
    };
    render(
      <AuthSessionContext.Provider value={{ session, signOut: async () => true }}>
        <PipelinePage />
        <Toaster />
      </AuthSessionContext.Provider>,
    );
  }

  afterEach(() => {
    vi.unstubAllGlobals();
    clearResourceCache();
    window.location.hash = "";
  });

  it("is headed «Pipeline» with «Tablero · Tarjetas · Cerradas»", async () => {
    renderPage([silentNow()]);
    expect(await screen.findByRole("heading", { level: 1, name: "Pipeline" })).toBeInTheDocument();
    const vista = screen.getByRole("group", { name: "Vista" });
    expect(within(vista).getAllByRole("button").map((b) => b.textContent)).toEqual(["Tablero", "Tarjetas", "Cerradas"]);
  });

  it("¿Marcar perdida? opens the lost reasons for that case", async () => {
    renderPage([silentNow()]);
    const b = await screen.findByTestId("board-card-44444444-4444-4444-8444-444444444444");
    fireEvent.click(within(b).getByRole("button", { name: /^¿Marcar perdida\?/ }));
    const dialog = await screen.findByRole("dialog", { name: "Mover a «Perdida»" });
    expect(dialog).toHaveTextContent("Laboratorio Callado");
    expect(within(dialog).getByRole("button", { name: "Sin respuesta" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Precio" })).toBeInTheDocument();
  });

  it("drop on Silenciosas shows the refusal toast", async () => {
    const fresh = card({
      opportunity_id: "55555555-5555-4555-8555-555555555555",
      organization: { organization_id: "org-f", name: "Planta Fresca", confirmation: "confirmed", version: 1 },
    }, ago(2 * DAY));
    renderPage([fresh]);
    const b = await screen.findByTestId("board-card-55555555-5555-4555-8555-555555555555");
    const data: Record<string, string> = {};
    const dataTransfer = {
      setData: (k: string, v: string) => { data[k] = v; },
      getData: (k: string) => data[k] ?? "",
      effectAllowed: "",
      dropEffect: "",
    };
    fireEvent.dragStart(b, { dataTransfer });
    const target = screen.getByRole("region", { name: "Silenciosas" });
    fireEvent.dragOver(target, { dataTransfer });
    fireEvent.drop(target, { dataTransfer });
    expect(await screen.findByText("Silenciosas se llena sola: un caso entra a los 21 días sin respuesta.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
