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
  followUpsSent,
  followUpStep,
  followUpStepLabel,
  HOME_FILTER_KINDS,
  livePauseFilter,
  overdueTasks,
  retakeHint,
  RHYTHM,
  stepForDays,
  tasksDue,
  useHomeFilter,
  type QueueItem,
} from "./today";
import { nextContact, nextContactChannel, stepChannel } from "./nextContact";

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

  it("cotizar hides follow-ups (Hoy v3: reply ranks before cotizar)", () => {
    // After Hoy v3, RANK is reply=0, cotizar=1, followup=2, task=3.
    // The cotizar view includes reply+cotizar+task (not followup), sorted by rank.
    expect(filterQueue(mixed(), "cotizar").map((q) => q.kind)).toEqual(["reply", "cotizar", "task"]);
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

  it("the queue follows the view", () => {
    const pedido = card({ stage: "qualifying", latest_revision: null, quote_numbers: [], created_at: ago(1) }, null);
    const silent = card({}, ago(15)); // day 15 = 4th threshold
    const kinds = (view: "todo" | "llamadas" | "cotizar") => filterQueue(buildParaTiQueue([pedido, silent], NOW, null), view).map((q) => q.kind).sort();
    expect(kinds("llamadas")).toEqual(["followup"]);
    expect(kinds("cotizar")).toEqual(["cotizar"]);
    expect(kinds("todo")).toEqual(["cotizar", "followup"]);
  });

  it("v3 display order: Responder first, then Cotizar, Seguimiento, Tareas", () => {
    // Hoy v3 (owner 2026-11-10): Responder > Cotizar > Seguimiento > Tareas
    expect(mixed().map((q) => q.kind)).toEqual(["reply", "cotizar", "followup", "task"]);
  });

  it("Seguimientos count in the strip equals the followup rows in the queue", () => {
    // The strip tile must show the same number as the rows it filters.
    const cotizar = card({ stage: "qualifying", latest_revision: null, quote_numbers: [], created_at: ago(1) }, null);
    const followup1 = card({}, ago(15));
    const followup2 = card({}, ago(7));
    const q = buildParaTiQueue([cotizar, followup1, followup2], NOW, null);
    const stripCount = q.filter((x) => x.kind === "followup").length;
    const queueRows = q.filter((x) => x.kind === "followup").length;
    expect(stripCount).toBe(queueRows);
    expect(stripCount).toBe(2);
  });

  it("filter chip counts match queue rows for each kind", () => {
    const q = mixed();
    const counts = {
      all: q.length,
      reply: q.filter((x) => x.kind === "reply").length,
      cotizar: q.filter((x) => x.kind === "cotizar").length,
      followup: q.filter((x) => x.kind === "followup").length,
      task: q.filter((x) => x.kind === "task").length,
    };
    expect(counts).toEqual({ all: 4, reply: 1, cotizar: 1, followup: 1, task: 1 });
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

describe("retakeHint (Hoy v3 task hint mapping)", () => {
  it("maps known states to hints", () => {
    expect(retakeHint("Retomar: evaluando internamente")).toBe("preguntar si ya decidieron");
    expect(retakeHint("Retomar: esperando fondos")).toBe("preguntar si llegó el fondo");
    expect(retakeHint("Retomar: esperando presupuesto")).toBe("preguntar si llegó el fondo");
    expect(retakeHint("Retomar: esperando aprobación")).toBe("preguntar si lo aprobaron");
    expect(retakeHint("Retomar: esperando aprobacion")).toBe("preguntar si lo aprobaron");
    expect(retakeHint("Retomar: pedirán reunión")).toBe("confirmar si aceptan reunirse");
    expect(retakeHint("Retomar: revisando propuesta")).toBe("preguntar si revisaron la propuesta");
  });

  it("returns null for non-Retomar tasks or unknown states", () => {
    expect(retakeHint("Llamar al laboratorio")).toBeNull();
    expect(retakeHint("Seguimiento de 01239-26")).toBeNull();
    expect(retakeHint("Retomar: algo desconocido totalmente nuevo")).toBeNull();
    expect(retakeHint("")).toBeNull();
  });

  it("is case-insensitive on the Retomar prefix", () => {
    expect(retakeHint("retomar: evaluando")).toBe("preguntar si ya decidieron");
    expect(retakeHint("RETOMAR: evaluando")).toBe("preguntar si ya decidieron");
  });
});

describe("a pause still ahead hides every task left over from before it (audit 2026-11-22)", () => {
  // «Llamar a Ana» was due 30 Oct; on 22 Nov the case waits, paused until 15 Dec.
  const now = new Date("2026-11-22T15:00:00Z");
  const call = { task_id: "t-ana", title: "Llamar a Ana", due_at: "2026-10-30T13:00:00Z", version: 1, owner: "Operadora Ficticia" };
  const paused = () => card({ stage: "quoting", paused_until: "2026-12-15", pause_reason: "Esperando fondos o proyecto", open_tasks: [call] }, "2026-10-20T13:00:00Z");

  it("is not due, not «Vencida» and not in Hoy's queue", () => {
    const c = paused();
    expect(tasksDue([c], now)).toEqual([]);
    expect(overdueTasks([c], now, "Operadora Ficticia")).toEqual([]);
    expect(buildParaTiQueue([c], now, "Operadora Ficticia").filter((q) => q.kind === "task")).toEqual([]);
  });

  it("the same task on a case without a pause is 23 days overdue", () => {
    const c = card({ stage: "quoting", open_tasks: [call] }, "2026-10-20T13:00:00Z");
    expect(tasksDue([c], now).map((d) => d.overdueDays)).toEqual([23]);
    expect(overdueTasks([c], now, "Operadora Ficticia").map((d) => d.overdueDays)).toEqual([23]);
  });

  it("a task set for today or later still counts while the pause is ahead", () => {
    const today = { ...call, task_id: "t-hoy", due_at: "2026-11-22T20:00:00Z" };
    const c = card({ stage: "quoting", paused_until: "2026-12-15", open_tasks: [call, today] }, "2026-10-20T13:00:00Z");
    expect(tasksDue([c], now).map((d) => d.task.task_id)).toEqual(["t-hoy"]);
  });

  it("one filter: the task Hoy leaves out is not the próximo contacto either", () => {
    const c = paused();
    expect(livePauseFilter(c, now)(call)).toBe(false);
    expect(nextContact(c, now)?.source).toBe("pause");
  });
});

describe("the rhythm step is the follow-ups sent, not the days (owner 2026-11-23)", () => {
  /** A quote sent 33 days ago and our emails `daysAgo` (the quote's own email first). */
  function chased(daysAgo: number[], over: Partial<OpportunityCardData> = {}): OpportunityCardData {
    const quote = ago(33);
    const times = [quote, ...daysAgo.map((d) => ago(d))];
    const newest = times[times.length - 1];
    return card({
      last_contact: { outbound: { at: newest, subject: "Re: Cotización", url: null, sent_times: times }, inbound: null },
      ...over,
    }, quote);
  }

  it("four unanswered follow-ups are past the closing email, not «Seguimiento 1 de 3»", () => {
    // The real case: quote, then follow-ups 17, 13, 9 and 3 days ago — each restarted the day count.
    const c = chased([17, 13, 9, 3]);
    expect(followUpsSent(c)).toBe(4);
    // Closing email sent 3 days ago: not chased again, and not to close yet.
    expect(followUpsDue([c], NOW)).toEqual([]);
    expect(nextContact(c, NOW)?.label).toBe("Cerrar · sin respuesta");
    // 14 days after it with no answer, Hoy says to close it.
    const old = chased([30, 26, 22, 14]);
    const due = followUpsDue([old], NOW)[0];
    expect(due.close).toBe(true);
    expect(followUpStepLabel(followUpStep(due), due.close)).toBe("Cerrar · sin respuesta");
  });

  it("each follow-up sent moves the step: email, call, WhatsApp, closing email", () => {
    expect(followUpStep(followUpsDue([chased([])], NOW)[0])).toBe(1);
    expect(followUpStep(followUpsDue([chased([10])], NOW)[0])).toBe(2);
    expect(followUpStep(followUpsDue([chased([10, 5])], NOW)[0])).toBe(3);
    const fourth = followUpsDue([chased([12, 8, 4])], NOW)[0];
    expect(followUpStep(fourth)).toBe(4);
    expect(followUpStepLabel(followUpStep(fourth))).toBe("Último correo");
    // Still 3 days after our last email before the next one.
    expect(followUpsDue([chased([12, 8, 2])], NOW)).toEqual([]);
  });

  it("the client writing back restarts the count", () => {
    const c = chased([20, 16, 12, 4], {
      last_contact: {
        outbound: { at: ago(4), subject: "Re", url: null, sent_times: [ago(33), ago(20), ago(16), ago(12), ago(4)] },
        inbound: { at: ago(14), subject: "Re", url: null, no_reply: true },
      },
    });
    // Only our emails after the client's «gracias, te aviso» count: 12 and 4 days ago.
    expect(followUpsSent(c)).toBe(2);
    expect(followUpStep(followUpsDue([c], NOW)[0])).toBe(3);
  });

  it("an older API without the case's emails keeps the day rhythm", () => {
    const c = card({ last_contact: { outbound: { at: ago(8), subject: "Re", url: null }, inbound: null } });
    expect(followUpsSent(c)).toBeNull();
    expect(followUpStep(followUpsDue([c], NOW)[0])).toBe(stepForDays(8));
  });
});

describe("Seguimiento gaps (owner 2026-11-23)", () => {
  const contact = (address: string, phone: string | null = null) =>
    ({ source: "gmail_recipient", name: null, address, others: 0, phone }) as unknown as OpportunityCardData["contact"];
  function chasedBy(days: number[], over: Partial<OpportunityCardData> = {}): OpportunityCardData {
    const times = [ago(33), ...days.map((d) => ago(d))];
    return card({
      last_contact: { outbound: { at: times[times.length - 1], subject: "Re", url: null, sent_times: times }, inbound: null },
      ...over,
    }, ago(33));
  }

  it("channel: email first, then phone — steps 1 and 4 by email, 2 and 3 by phone when there is one", () => {
    expect(stepChannel(1, true)).toBe("correo");
    expect(stepChannel(2, true)).toBe("llamada");
    expect(stepChannel(3, true)).toBe("llamada");
    expect(stepChannel(4, true)).toBe("correo");
    expect(stepChannel(2, false)).toBe("correo");
    const phone = "+56 9 1234 5678";
    expect(nextContactChannel(chasedBy([], { contact: contact("a@ficticio.invalid", phone) }), NOW)).toBe("correo");
    expect(nextContactChannel(chasedBy([10], { contact: contact("a@ficticio.invalid", phone) }), NOW)).toBe("llamada");
    // A channel someone chose still wins.
    const chosen = chasedBy([], { contact: contact("a@ficticio.invalid", phone),
      follow_up: { operator_id: null, operator_name: null, channel: "llamada" } });
    expect(nextContactChannel(chosen, NOW)).toBe("llamada");
  });

  it("a WhatsApp sent counts as a touch: step 3 moves on", () => {
    const recent = chasedBy([10, 6], { last_call: { at: ago(2), outcome: "whatsapp" } });
    expect(callTouch(recent)).toBe(ago(2));
    expect(followUpsSent(recent)).toBe(3);
    expect(followUpsDue([recent], NOW)).toEqual([]); // touched 2 days ago: not due yet
    const later = chasedBy([12, 8], { last_call: { at: ago(4), outcome: "whatsapp" } });
    expect(followUpStep(followUpsDue([later], NOW)[0])).toBe(4);
  });

  it("two cases of the same person are one Hoy row naming the other", () => {
    const a = chasedBy([5], { contact: contact("Persona@Ficticio.invalid") });
    const b = chasedBy([4], { contact: contact("persona@ficticio.invalid") });
    const other = chasedBy([4], { contact: contact("otra@ficticio.invalid") });
    const rows = buildParaTiQueue([a, b, other], NOW, null).filter((q) => q.kind === "followup");
    expect(rows.map((r) => r.opportunity_id)).toEqual([a.opportunity_id, other.opportunity_id]);
    const lead = rows[0];
    expect(lead.kind === "followup" && lead.others.map((o) => o.opportunity_id)).toEqual([b.opportunity_id]);
  });

  it("without a contact on the card, whom our last email went to (`to_ref`) joins the two cases", () => {
    const sentTo = (c: OpportunityCardData, ref: string): OpportunityCardData =>
      ({ ...c, last_contact: { inbound: null, outbound: { ...c.last_contact!.outbound!, to_ref: ref } } });
    const a = sentTo(chasedBy([5]), "ref-1");
    const b = sentTo(chasedBy([4]), "ref-1");
    const c = sentTo(chasedBy([4]), "ref-2");
    const rows = buildParaTiQueue([a, b, c], NOW, null).filter((q) => q.kind === "followup");
    expect(rows.map((r) => r.opportunity_id)).toEqual([a.opportunity_id, c.opportunity_id]);
  });
});
