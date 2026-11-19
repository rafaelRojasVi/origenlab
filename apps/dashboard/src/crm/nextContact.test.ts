import { describe, expect, it } from "vitest";
import type { OpenTask, OpportunityCardData } from "./crmTypes";
import { nextContact } from "./nextContact";
import { planNextSteps } from "./nextSteps";
import { stepAge } from "./pages/HoyQueueParts";
import { urgencyByCase, urgencyText } from "./urgency";
import { nextContactAt } from "./seguimientoView";

// Every value below is invented; the repository is public.
const NOW = new Date(2026, 10, 18, 12, 0); // Wednesday 18 Nov, local noon

function task(over: Partial<OpenTask> & { due_at: string }): OpenTask {
  return { task_id: `t-${over.due_at}`, title: "Seguimiento de CN09876", version: 1, owner: null, created_at: null, ...over };
}

function card(over: Partial<OpportunityCardData> = {}): OpportunityCardData {
  const rev = { revision_id: "r1", revision_no: 1, status: "sent", origin: null, sent_at: "2026-09-25T15:00:00Z", superseded_by_revision_no: null, is_active: true, document: null, gmail: null, drive: null, quote_number: "CN09876", candidates: [] };
  return {
    opportunity_id: "case-1", title: "Cotización", stage: "quoting", version: 3, created_at: "2026-09-20T12:00:00Z", updated_at: null, closed_at: null, close_reason: null,
    organization: { organization_id: "org-1", name: "Instituto Ficticio", confirmation: "confirmed", version: 1 }, other_organizations: [],
    contact: null, quotes: [{ quote_id: "q1", quote_number: "CN09876", revisions: [rev] }], quote_numbers: ["CN09876"], revision_count: 1,
    money: [], latest_revision: rev, drive_folder: null, attention: [], status: "ok",
    next_action: { text: "", source: "suggested", due_at: null }, open_tasks: [], last_contact: null,
    ...over,
  } as unknown as OpportunityCardData;
}

const local = (d: number, h = 9) => new Date(2026, 10, d, h, 0).toISOString();

describe("nextContact: one rule", () => {
  it("the earliest follow-up reminder wins over an earlier other task", () => {
    const c = card({ open_tasks: [task({ title: "Llamar a compras", due_at: local(19) }), task({ due_at: local(21) }), task({ due_at: local(20) })] });
    expect(nextContact(c, NOW)).toMatchObject({ source: "followup", at: local(20) });
  });

  it("else the earliest other task, else the pause day, else none", () => {
    expect(nextContact(card({ open_tasks: [task({ title: "Enviar ficha", due_at: local(25) }), task({ title: "Llamar", due_at: local(22) })] }), NOW))
      .toMatchObject({ source: "task", at: local(22) });
    expect(nextContact(card({ paused_until: "2026-11-15", pause_reason: "Esperan fondos" }), NOW)).toMatchObject({
      source: "pause", label: "Vuelve de pausa · Esperan fondos",
    });
    expect(nextContact(card(), NOW)).toBeNull();
  });

  it("a reminder set after a call is not met by that call", () => {
    // «Llamé · no contestó» at 11:00, reminder «otra vez hoy (en 2 h)» created right after.
    const c = card({
      last_call: { at: new Date(2026, 10, 18, 11, 0).toISOString(), outcome: "contesto" },
      open_tasks: [task({ due_at: new Date(2026, 10, 18, 13, 0).toISOString(), created_at: new Date(2026, 10, 18, 11, 0, 5).toISOString() })],
    });
    expect(nextContact(c, NOW)?.source).toBe("followup");
  });

  it("Seguimiento, Hoy and the Pipeline agree: a reminder clears «Vuelve de pausa» lateness", () => {
    // Paused until three days ago, then «Llamé» set the next contact for Friday.
    const c = card({ paused_until: "2026-11-15", pause_reason: "Esperan fondos", open_tasks: [task({ due_at: local(20), created_at: local(18, 11) })] });
    expect(nextContactAt(c, NOW)).toBe(local(20));
    const plan = planNextSteps([c], NOW, null);
    expect(plan.byGroup.atrasado).toHaveLength(0);
    expect(plan.steps.map((s) => [s.kind, s.dueDays])).toEqual([["tarea", 2]]);
    expect(urgencyText(urgencyByCase([c], NOW).get("case-1")!)).not.toMatch(/atrasado/);
    // Without the reminder the pause that came back is still due, three days late.
    // (No quote on it, so the follow-up rhythm does not claim the case first.)
    const back = planNextSteps([card({ paused_until: "2026-11-15", pause_reason: "Esperan fondos", quotes: [], latest_revision: null })], NOW, null);
    expect(back.byGroup.atrasado.map((s) => s.kind)).toEqual(["pausa"]);
    expect(stepAge(back.byGroup.atrasado[0])).toMatchObject({ text: "3 d atrasado", title: "3 días desde la fecha del próximo contacto" });
  });

  it("the Pipeline's step for a paused case with a task is the task, not the pause", () => {
    const c = card({ paused_until: "2026-11-24", pause_reason: "Vacaciones", open_tasks: [task({ title: "Llamar", due_at: local(20) })] });
    expect(planNextSteps([c], NOW, null).steps.map((s) => s.label)).toEqual(["Llamar"]);
  });
});
