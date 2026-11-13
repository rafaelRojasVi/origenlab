import { describe, expect, it } from "vitest";
import type { OpportunityCardData, RevisionCard } from "./crmTypes";
import { caseAmountClp, dayDiff, planNextSteps } from "./nextSteps";

const NOW = new Date("2026-11-10T15:00:00Z");
const DAY = 86_400_000;
const ago = (days: number, extraMs = 0) => new Date(NOW.getTime() - days * DAY - extraMs).toISOString();
const ahead = (days: number) => new Date(NOW.getTime() + days * DAY).toISOString();

function rev(sentAt: string): RevisionCard {
  return {
    revision_id: `r-${sentAt}`, revision_no: 1, status: "sent", origin: "historical_import", sent_at: sentAt,
    superseded_by_revision_no: null, is_active: true, document: null, gmail: null, drive: null, quote_number: "01239-26",
  };
}

let n = 0;
function card(over: Partial<OpportunityCardData> = {}, sentAt: string | null = ago(20)): OpportunityCardData {
  n += 1;
  const latest = sentAt ? rev(sentAt) : null;
  return {
    opportunity_id: `00000000-0000-4000-8000-0000000000${String(n).padStart(2, "0")}`,
    title: `Caso ${n}`, stage: "negotiating", version: 1, created_at: null, updated_at: null, closed_at: null, close_reason: null,
    organization: { organization_id: `org-${n}`, name: `Instituto Ficticio ${n}`, confirmation: "confirmed", version: 1 },
    other_organizations: [], contact: null, quotes: [], quote_numbers: ["01239-26"], revision_count: latest ? 1 : 0,
    latest_revision: latest, drive_folder: null, attention: [], status: "ok",
    next_action: { text: "", source: "suggested", due_at: null },
    ...over,
  };
}

const task = (title: string, due_at: string, owner: string | null = null) =>
  ({ task_id: `t-${title}-${due_at}`, title, due_at, version: 1, owner });

describe("planNextSteps", () => {
  it("groups a returned pause as «atrasado» with its reason, a reply as «hoy», a task due tomorrow as «mañana»", () => {
    const returned = card({ open_tasks: [task("Retomar: Evaluando internamente", ago(16))] }, ago(30));
    const reply = card({ last_contact: { outbound: null, inbound: { at: ago(0, 3_600_000), subject: "Consulta", url: "https://mail.example.cl/r" } } }, ago(5));
    const tomorrow = card({ open_tasks: [task("Llamar por las pesas", ahead(1))] }, ago(1));
    const plan = planNextSteps([returned, reply, tomorrow], NOW, null);

    const pause = plan.byGroup.atrasado.find((s) => s.card === returned);
    expect(pause?.kind).toBe("pausa");
    expect(pause?.pause?.reason).toBe("Evaluando internamente");
    expect(pause?.pause?.hint).toMatch(/decidieron/);
    expect(plan.byGroup.hoy.find((s) => s.card === reply)?.kind).toBe("responder");
    const t = plan.byGroup.manana.find((s) => s.card === tomorrow);
    expect(t?.kind).toBe("tarea");
    expect(t?.item).toBeNull();
    expect(plan.overdue).toBe(1);
  });

  it("projects the first follow-up day of a quote sent yesterday", () => {
    const oneDay = card({}, ago(1)); // day 3 is two days ahead
    const plan = planNextSteps([oneDay], NOW, null);
    const s = plan.steps.find((x) => x.card === oneDay);
    expect(s?.group).toBe("semana");
    expect(s?.dueDays).toBe(2);
    expect(s?.label).toBe("Seguimiento 1 de 3 · día 3");
    expect(plan.days[2].count).toBe(1);
  });

  it("keeps a follow-up past its day in today's queue until we touch the case", () => {
    // Owner 2026-11-13: day 5 (past day 3, no email since) is due now — never parked until day 7.
    const fiveDays = card({}, ago(5));
    const plan = planNextSteps([fiveDays], NOW, null);
    const s = plan.steps.find((x) => x.card === fiveDays);
    expect(s?.group).not.toBe("semana");
  });

  it("puts the operator's own steps first, then the higher amount", () => {
    const mineSmall = card({ open_tasks: [task("Llamar", ago(2), "Karla")], money: [{ currency: "CLP", net_total: "300000" }] }, ago(1));
    const otherBig = card({ open_tasks: [task("Llamar", ago(2), "Tati")], money: [{ currency: "CLP", net_total: "7000000" }] }, ago(1));
    const otherSmall = card({ open_tasks: [task("Llamar", ago(2), "Tati")], money: [{ currency: "CLP", net_total: "200000" }] }, ago(1));
    const plan = planNextSteps([otherSmall, otherBig, mineSmall], NOW, "Karla");
    expect(plan.byGroup.atrasado.map((s) => s.card)).toEqual([mineSmall, otherBig, otherSmall]);
  });

  it("counts each step once across the seven days", () => {
    const plan = planNextSteps([card({}, ago(5)), card({ open_tasks: [task("Visita", ahead(3))] }, ago(1))], NOW, null);
    expect(plan.days.reduce((a, d) => a + d.count, 0)).toBe(2);
  });
});

describe("order inside a group", () => {
  it("puts a small client's reply before a big quote's follow-up", () => {
    const bigFollowUp = card({ money: [{ currency: "CLP", net_total: "9000000" }] }, ago(15)); // día 15: due today
    const smallReply = card({
      money: [{ currency: "CLP", net_total: "200000" }],
      last_contact: { outbound: null, inbound: { at: ago(0, 3_600_000), subject: "Consulta", url: "https://mail.example.cl/r" } },
    }, ago(5));
    const plan = planNextSteps([bigFollowUp, smallReply], NOW, null);
    expect(plan.byGroup.hoy.map((s) => s.kind)).toEqual(["responder", "seguimiento"]);
  });
});

describe("weekend", () => {
  it("rolls a follow-up that would fall on Saturday to Monday", () => {
    const thursday = new Date(2026, 10, 12, 15, 0, 0); // jueves 12 nov 2026
    const sentYesterday = new Date(thursday.getTime() - 1 * DAY).toISOString(); // day 3 = sábado 14
    const plan = planNextSteps([card({}, sentYesterday)], thursday, null);
    const s = plan.steps[0];
    expect(s.due.getDay()).toBe(1);
    expect(s.dueDays).toBe(4);
  });
});

describe("helpers", () => {
  it("reads the amount only in pesos", () => {
    expect(caseAmountClp(card({ money: [{ currency: "CLP", net_total: "1234567" }] }))).toBe(1234567);
    expect(caseAmountClp(card({ money: [{ currency: "USD", net_total: "1000" }] }))).toBeNull();
    expect(caseAmountClp(card({}))).toBeNull();
  });
  it("measures whole local days", () => {
    const noon = new Date(2026, 10, 10, 12, 0, 0);
    expect(dayDiff(noon, new Date(2026, 10, 11, 23, 59, 0))).toBe(1);
    expect(dayDiff(noon, new Date(2026, 10, 9, 0, 30, 0))).toBe(-1);
  });
});

describe("no case is left without a next step (owner 2026-11-11)", () => {
  it("a quote sent yesterday has its first follow-up on day 3, not «no next step»", () => {
    const fresh = card({ stage: "quoting" }, ago(1));
    const step = planNextSteps([fresh], NOW, null).steps.find((s) => s.card === fresh);
    expect(step?.kind).toBe("seguimiento");
    expect(step?.dueDays).toBeGreaterThanOrEqual(2);
    expect(step?.actionable).toBe(false);
  });

  it("a quotation approved and never sent is today's «Enviar la cotización»; one replaced by a sent revision is not", () => {
    const approved = (no: number) => ({ ...rev(ago(1)), revision_id: `a-${no}`, revision_no: no, status: "approved", sent_at: null });
    const sent = (no: number) => ({ ...rev(ago(2)), revision_id: `s-${no}`, revision_no: no });
    const ready = card({ stage: "lead", quotes: [{ quote_id: "q-ready", quote_number: "00042-26", number_origin: "minted", revisions: [approved(1)] }] }, null);
    const replaced = card({ quotes: [{ quote_id: "q-old", quote_number: "00043-26", number_origin: "minted", revisions: [approved(1), sent(2)] }] }, ago(2));
    const plan = planNextSteps([ready, replaced], NOW, null);
    const send = plan.steps.find((s) => s.card === ready);
    expect(send).toMatchObject({ kind: "cotizar", group: "hoy", actionable: true, label: "Enviar la cotización 00042-26" });
    expect(plan.steps.find((s) => s.card === replaced)?.label ?? "").not.toMatch(/Enviar la cotización/);
  });
});
