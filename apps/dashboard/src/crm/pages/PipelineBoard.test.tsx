import "@testing-library/jest-dom";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { OpportunityCardData, RevisionCard } from "../crmTypes";
import { stagePath } from "../caseCommands";
import { ageBucket, boardColumnOf, boardStatusLine, pausedUntil } from "../stage";
import { Board, GROUP_AFTER } from "./PipelineBoard";

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
  it("groups a revision by its age, newest first, and a missing date apart", () => {
    expect(ageBucket("2026-10-01T00:00:00Z", NOW)).toBe("d30");
    expect(ageBucket("2026-08-01T00:00:00Z", NOW)).toBe("d90");
    expect(ageBucket("2026-05-01T00:00:00Z", NOW)).toBe("d180");
    expect(ageBucket("2025-12-01T00:00:00Z", NOW)).toBe("d365");
    expect(ageBucket("2024-01-01T00:00:00Z", NOW)).toBe("older");
    expect(ageBucket(null, NOW)).toBe("none");
    expect(ageBucket("no es una fecha", NOW)).toBe("none");
  });

  it("says one thing per card: the blocker, or the gaps", () => {
    expect(boardStatusLine(card())).toEqual({ text: "histórico · sin Drive", tone: "warn" });
    expect(boardStatusLine(card({ contact: null }, null))).toEqual({ text: "sin cotización · sin contacto", tone: "warn" });
    expect(
      boardStatusLine(card({ attention: [{ code: "canonical_undetermined", label: "Hay más de una revisión vigente", blocking: true }] })),
    ).toEqual({ text: "Hay más de una revisión vigente", tone: "bad" });
    const done = card({ drive_folder: { source: "archive_ledger", folder_id: "f", url: "https://drive.example.cl/f" }, status: "ok" });
    expect(boardStatusLine(done)).toEqual({ text: "histórico · al día", tone: "neutral" });
  });
});

describe("Tablero", () => {
  it("counts every column and leads each card with the client and the quote number", () => {
    const lead = card({ stage: "lead" }, null);
    const quoting = card();
    render(<Board cards={[lead, quoting]} onOpen={() => undefined} now={NOW} />);
    expect(screen.getByTestId("board-count-solicitada")).toHaveTextContent("1");
    expect(screen.getByTestId("board-count-enviada")).toHaveTextContent("1");
    for (const key of ["estudio", "conversacion", "pausa", "ganada", "perdida"]) {
      expect(screen.getByTestId(`board-count-${key}`)).toHaveTextContent("0");
    }
    const b = screen.getByTestId(`board-card-${quoting.opportunity_id}`);
    expect(b.textContent).toMatch(/^Universidad Ficticia \d+01239-26 · r1 · 20 sept 2026histórico · sin Drive$/);
    // No repeated chips on a board card: one status line, nothing else.
    expect(within(b).getAllByTestId("board-status-line")).toHaveLength(1);
    expect(b).not.toHaveTextContent("Enviada · histórico");
    expect(b).not.toHaveTextContent("Pendiente");
    expect(b).not.toHaveTextContent("Drive: falta");
  });

  it("shows the whole suggestion as the card's tooltip and opens the case", () => {
    const c = card();
    const onOpen = vi.fn();
    render(<Board cards={[c]} onOpen={onOpen} now={NOW} />);
    const b = screen.getByTestId(`board-card-${c.opportunity_id}`);
    expect(b).toHaveAttribute("title", `Sugerencia: ${c.next_action.text}`);
    fireEvent.click(b);
    expect(onOpen).toHaveBeenCalledWith(c.opportunity_id);
  });

  it("scrolls each column on its own", () => {
    render(<Board cards={[card()]} onOpen={() => undefined} now={NOW} />);
    expect(screen.getByTestId("board-column-enviada").className).toMatch(/overflow-y-auto/);
    expect(screen.getByTestId("board-column-enviada").className).toMatch(/max-h-/);
  });

  it("groups a long column by the age of the last revision, newest group open", () => {
    const recent = Array.from({ length: 3 }, () => card({}, "2026-09-25T12:00:00Z"));
    const old = Array.from({ length: GROUP_AFTER }, () => card({}, "2025-01-10T12:00:00Z"));
    const none = card({}, null);
    render(<Board cards={[...old, none, ...recent]} onOpen={() => undefined} now={NOW} />);
    expect(screen.getByTestId("board-count-enviada")).toHaveTextContent(String(3 + GROUP_AFTER + 1));
    const groups = within(screen.getByTestId("board-column-enviada")).getAllByRole("group");
    expect(groups.map((g) => g.getAttribute("data-testid"))).toEqual(["board-age-d30", "board-age-older", "board-age-none"]);
    expect(groups.map((g) => g.hasAttribute("open"))).toEqual([true, false, false]);
    expect(screen.getByTestId("board-age-d30")).toHaveTextContent("Últimos 30 días3");
    expect(screen.getByTestId("board-age-older")).toHaveTextContent(`Más de un año${GROUP_AFTER}`);
  });

  it("does not group a short column", () => {
    render(<Board cards={[card(), card({}, "2024-01-01T00:00:00Z")]} onOpen={() => undefined} now={NOW} />);
    expect(within(screen.getByTestId("board-column-enviada")).queryAllByRole("group")).toHaveLength(0);
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
    expect(stagePath("lost", "lead")).toBeNull();
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
});

