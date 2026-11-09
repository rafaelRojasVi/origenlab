import { createElement, type ReactNode } from "react";
import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { AuthSessionState } from "../api/authClient";
import { AuthSessionContext } from "../context/AuthSessionContext";
import type { OpportunityCardData, RevisionCard } from "./crmTypes";
import {
  buildParaTiQueue,
  callTouch,
  filterQueue,
  followUpsDue,
  followUpStep,
  HOME_FILTER_KINDS,
  RHYTHM,
  stepForDays,
  todayHeaderCounts,
  useHomeFilter,
  type QueueItem,
} from "./today";

// Every value below is invented; the repository is public.
const NOW = new Date("2026-10-06T15:00:00Z");
const DAY = 86_400_000;
const ago = (days: number, extraMs = 0) => new Date(NOW.getTime() - days * DAY - extraMs).toISOString();

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
    gmail: null,
    drive: null,
    quote_number: "01239-26",
  };
}

let n = 0;
function card(over: Partial<OpportunityCardData> = {}, sentAt: string | null = ago(20)): OpportunityCardData {
  n += 1;
  const latest = sentAt ? rev(sentAt) : null;
  return {
    opportunity_id: `00000000-0000-4000-8000-0000000000${String(n).padStart(2, "0")}`,
    title: `Caso ${n}`,
    stage: "negotiating",
    version: 1,
    created_at: null,
    updated_at: null,
    closed_at: null,
    close_reason: null,
    organization: { organization_id: `org-${n}`, name: `Instituto Ficticio ${n}`, confirmation: "confirmed", version: 1 },
    other_organizations: [],
    contact: null,
    quotes: [],
    quote_numbers: ["01239-26"],
    revision_count: latest ? 1 : 0,
    latest_revision: latest,
    drive_folder: null,
    attention: [],
    status: "ok",
    next_action: { text: "", source: "suggested", due_at: null },
    ...over,
  };
}

/** One of each kind: a request to quote, a client reply, a task due today and a 14-day follow-up. */
function mixed(): QueueItem[] {
  const cotizar = card({ stage: "qualifying", latest_revision: null, quote_numbers: [], created_at: ago(1) }, null);
  const reply = card({ last_contact: { outbound: null, inbound: { at: ago(0, 3_600_000), subject: "Consulta", url: "https://mail.example.cl/r" } } }, ago(5));
  const tarea = card({ open_tasks: [{ task_id: "t1", title: "Llamar al laboratorio", due_at: ago(0, -3_600_000), version: 1, owner: null }] }, ago(1));
  const followup = card({}, ago(15)); // day 15 = 4th threshold, so isFollowUpDueToday passes
  return buildParaTiQueue([followup, tarea, reply, cotizar], NOW, null);
}

describe("home filter", () => {
  it("filterQueue keeps only follow-ups and tasks for llamadas", () => {
    expect(filterQueue(mixed(), "llamadas").map((q) => q.kind)).toEqual(["followup", "task"]);
  });

  it("cotizar hides follow-ups", () => {
    expect(filterQueue(mixed(), "cotizar").map((q) => q.kind)).toEqual(["cotizar", "reply", "task"]);
  });

  it("todo keeps everything", () => {
    const q = mixed();
    expect(filterQueue(q, "todo")).toEqual(q);
    expect([...HOME_FILTER_KINDS.todo].sort()).toEqual(["cotizar", "decidir", "followup", "licitacion", "reply", "task"]);
    expect(HOME_FILTER_KINDS.llamadas.has("decidir")).toBe(false);
    expect(HOME_FILTER_KINDS.cotizar.has("decidir")).toBe(true);
    expect(HOME_FILTER_KINDS.llamadas.has("licitacion")).toBe(false);
  });

  it("unknown or missing home_filter reads as todo", () => {
    const signedIn = (profile: unknown): AuthSessionState =>
      ({
        kind: "signed_in",
        method: "google_profile",
        operator: { operatorId: "op-1", email: "equipo@ejemplo.invalid", displayName: "Equipo", role: "sales" },
        profile,
      }) as AuthSessionState;
    const read = (session: AuthSessionState) =>
      renderHook(() => useHomeFilter(), {
        wrapper: ({ children }: { children: ReactNode }) =>
          createElement(AuthSessionContext.Provider, { value: { session, signOut: async () => true } }, children),
      }).result.current;
    expect(read(signedIn(null))).toBe("todo");
    expect(read(signedIn({ id: "p", displayName: "Ana", roleLabel: "", homeFilter: "zzz" }))).toBe("todo");
    expect(read(signedIn({ id: "p", displayName: "Ana", roleLabel: "" }))).toBe("todo");
    expect(read({ kind: "loading" })).toBe("todo");
    expect(read(signedIn({ id: "p", displayName: "Ana", roleLabel: "", homeFilter: "llamadas" }))).toBe("llamadas");
  });

  it("followUpStep maps 3/7/10/15 days to 1/2/3/4", () => {
    const cards = [card({}, ago(3)), card({}, ago(7)), card({}, ago(10)), card({}, ago(15))];
    expect(followUpsDue(cards, NOW).map((f) => [f.days, followUpStep(f)])).toEqual([[15, 4], [10, 3], [7, 2], [3, 1]]);
  });

  it("the header counts follow the view", () => {
    const pedido = card({ stage: "qualifying", latest_revision: null, quote_numbers: [], created_at: ago(1) }, null);
    const silent = card({}, ago(15)); // day 15 = 4th threshold
    expect(todayHeaderCounts([pedido, silent], NOW, null, "llamadas")).toEqual({ cotizar: 0, responder: 0, seguimientos: 1 });
    expect(todayHeaderCounts([pedido, silent], NOW, null, "cotizar")).toEqual({ cotizar: 1, responder: 0, seguimientos: 0 });
    expect(todayHeaderCounts([pedido, silent], NOW, null)).toEqual({ cotizar: 1, responder: 0, seguimientos: 1 });
  });

  it("ranks Llamar before Tareas", () => {
    expect(mixed().map((q) => q.kind)).toEqual(["cotizar", "reply", "followup", "task"]);
  });
});

describe("a call is a follow-up touch (owner ruling 2026-11-08)", () => {
  it("a call that reached the client («contestó» or «reunión») restarts the clock; other outcomes do not", () => {
    const contesto = card({ last_call: { at: ago(3), outcome: "contesto" } }, ago(20));
    const reunion = card({ last_call: { at: ago(3), outcome: "reunion" } }, ago(20));
    const buzon = card({ last_call: { at: ago(3), outcome: "buzon" } }, ago(20));
    const days = (c: OpportunityCardData) => followUpsDue([c], NOW).map((f) => f.days);
    expect(days(contesto)).toEqual([3]);
    expect(days(reunion)).toEqual([3]);
    expect(days(buzon)).toEqual([20]);
  });

  it("last contact is the later of our last email and that call", () => {
    const outbound = (at: string) => ({ outbound: { at, subject: null, url: null }, inbound: null });
    const emailLater = card({ last_contact: outbound(ago(4)), last_call: { at: ago(10), outcome: "contesto" } }, ago(20));
    const callLater = card({ last_contact: outbound(ago(10)), last_call: { at: ago(5), outcome: "contesto" } }, ago(20));
    // A call before the quote went out is not a follow-up of it.
    const callBefore = card({ last_call: { at: ago(25), outcome: "contesto" } }, ago(20));
    expect(followUpsDue([emailLater], NOW).map((f) => f.days)).toEqual([4]);
    expect(followUpsDue([callLater], NOW).map((f) => f.days)).toEqual([5]);
    expect(followUpsDue([callBefore], NOW).map((f) => f.days)).toEqual([20]);
  });
});

describe("callTouch (Task 4 review fix)", () => {
  it("an unsent newest revision has no clock: an older answered call is not a touch", () => {
    const draft: RevisionCard = { ...rev(ago(10)), status: "draft", origin: "authored", sent_at: null, revision_no: 2 };
    const c = card({ latest_revision: draft, last_call: { at: ago(2), outcome: "contesto" } });
    expect(callTouch(c)).toBeNull();
    expect(callTouch(card({ last_call: { at: ago(2), outcome: "contesto" } }))).toBe(ago(2));
  });
});

describe("stepForDays (final review: one rhythm for the board card and the drawer chip)", () => {
  it("is built on RHYTHM[].from, at the boundaries", () => {
    expect(stepForDays(0)).toBe(1);
    expect(stepForDays(RHYTHM[1].from - 1)).toBe(1);
    expect(stepForDays(RHYTHM[1].from)).toBe(2);
    expect(stepForDays(RHYTHM[2].from - 1)).toBe(2);
    expect(stepForDays(RHYTHM[2].from)).toBe(3);
    expect(stepForDays(RHYTHM[3].from - 1)).toBe(3);
    expect(stepForDays(RHYTHM[3].from)).toBe(4);
    expect(stepForDays(400)).toBe(4);
    expect([RHYTHM[1].from, RHYTHM[2].from, RHYTHM[3].from]).toEqual([7, 10, 15]);
  });

  it("agrees with followUpStep on the step followUpsDue assigns", () => {
    for (const d of [3, 6, 7, 9, 10, 14, 15, 29, 30, 45]) {
      const [f] = followUpsDue([card({}, ago(d))], NOW);
      expect(followUpStep(f)).toBe(stepForDays(d));
    }
  });
});
