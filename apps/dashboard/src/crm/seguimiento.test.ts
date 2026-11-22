import { describe, expect, it } from "vitest";
import type { ProfileCard } from "../api/authClient";
import type { OpportunityCardData, PipelineResponse, RevisionCard } from "./crmTypes";
import { PILOT_CALLS_PER_DAY } from "./goals";
import {
  channelsFor,
  normalizeChannel,
  scoreCase,
  seguimientoKpis,
  seguimientoRows,
  suggestOwner,
  WHATSAPP_PROFILE_KEYS,
} from "./seguimiento";
import { followUpsDue } from "./today";

const NOW = new Date("2026-11-18T15:00:00Z"); // 12:00 in Santiago (UTC-3)

const profiles: ProfileCard[] = [
  { id: "op-tati", displayName: "Persona Uno", roleLabel: "", key: "tatiana" },
  { id: "op-rafa", displayName: "Persona Dos", roleLabel: "", key: "rafael" },
  { id: "op-karla", displayName: "Persona Tres", roleLabel: "", key: "karla" },
];

function rev(over: Partial<RevisionCard> = {}): RevisionCard {
  return {
    revision_id: "r1",
    revision_no: 1,
    status: "sent",
    origin: null,
    sent_at: "2026-11-10T15:00:00Z",
    superseded_by_revision_no: null,
    is_active: true,
    document: null,
    gmail: null,
    drive: null,
    quote_number: "CN09876",
    ...over,
  };
}

function card(over: Partial<OpportunityCardData> & { revs?: RevisionCard[]; phone?: string | null; clp?: string } = {}): OpportunityCardData {
  const { revs = [rev()], phone = null, clp = "500000", ...rest } = over;
  const latest = revs[revs.length - 1] ?? null;
  return {
    opportunity_id: "o-" + Math.random().toString(36).slice(2),
    title: "Balanza analitica",
    stage: "quoting",
    version: 1,
    created_at: "2026-11-01T12:00:00Z",
    updated_at: null,
    closed_at: null,
    close_reason: null,
    organization: null,
    other_organizations: [],
    contact: { source: "crm_participant", name: "Persona", address: "persona@ejemplo.invalid", others: 0, phone, phone_confirmed: phone ? true : null },
    quotes: [{ quote_id: "q1", quote_number: "CN09876", revisions: revs }],
    quote_numbers: ["CN09876"],
    revision_count: revs.length,
    money: [{ currency: "CLP", net_total: clp }],
    latest_revision: latest,
    drive_folder: null,
    attention: [],
    status: "ok",
    follow_up: { operator_id: null, operator_name: null, channel: null },
    call_counts: { total: 0, today: 0 },
    last_call_any: null,
    next_action: { text: "", source: "suggested", due_at: null },
    ...rest,
  } as OpportunityCardData;
}

describe("seguimientoRows", () => {
  it("excludes draft-only and void-only cases", () => {
    const rows = seguimientoRows(
      [card({ revs: [rev({ status: "draft" })] }), card({ revs: [rev({ status: "void" })] }), card()],
      profiles,
      NOW,
    );
    expect(rows).toHaveLength(1);
  });

  it("includes historical_import", () => {
    const rows = seguimientoRows([card({ revs: [rev({ status: "imported", origin: "historical_import" })] })], profiles, NOW);
    expect(rows).toHaveLength(1);
  });

  it("excludes closed cases", () => {
    expect(seguimientoRows([card({ stage: "lost", closed_at: "2026-11-12T12:00:00Z" })], profiles, NOW)).toHaveLength(0);
  });

  it("sorts late first then score", () => {
    // Late: the reminder's day went by (`SegRow.next`).
    const overdue = [{ task_id: "t-late", title: "Seguimiento de CN09876", due_at: "2026-11-16T13:00:00Z", version: 1, owner: null }];
    const fresh = card({ title: "fresh", last_contact: { outbound: { at: "2026-11-17T15:00:00Z", subject: null, url: null }, inbound: null } });
    const lateLow = card({ title: "late low", clp: "500000", open_tasks: overdue });
    const lateHigh = card({ title: "late high", clp: "50000000", open_tasks: overdue });
    const rows = seguimientoRows([fresh, lateLow, lateHigh], profiles, NOW);
    expect(rows.map((r) => r.card.title)).toEqual(["late high", "late low", "fresh"]);
    expect(rows[0].late).toBe(true);
    expect(rows[2].late).toBe(false);
  });

  it("late counts calendar days, not hours", () => {
    // Local wall-clock times, so the rows read the same in every zone. 23:30 on the 15th is
    // 3 days before noon on the 18th (60.5 hours, but three calendar days)…
    const now = new Date(2026, 10, 18, 12);
    const edge = card({ revs: [rev({ sent_at: new Date(2026, 10, 15, 23, 30).toISOString() })] });
    // …and 00:30 on the 16th only 2.
    const young = card({ revs: [rev({ sent_at: new Date(2026, 10, 16, 0, 30).toISOString() })] });
    const [a, b] = seguimientoRows([edge, young], profiles, now);
    const byEdge = [a, b].find((r) => r.card === edge)!;
    const byYoung = [a, b].find((r) => r.card === young)!;
    expect(byEdge.daysSinceTouch).toBe(3);
    expect(byYoung.daysSinceTouch).toBe(2);
  });

  it("uses the newest touch among email, call and sent date", () => {
    const c = card({ last_call_any: { at: "2026-11-17T15:00:00Z", outcome: "no_contesto", author_name: "Persona Uno" } });
    const [row] = seguimientoRows([c], profiles, NOW);
    expect(row.daysSinceTouch).toBe(1);
    expect(row.lastTouch?.kind).toBe("call");
  });

  it("whatsapp is kept for Tati and an unset owner is suggested", () => {
    const c = card({ title: "Sonicador UP200St", follow_up: { operator_id: null, operator_name: null, channel: "whatsapp" } });
    const [row] = seguimientoRows([c], profiles, NOW);
    expect(row.ownerId).toBeNull();
    expect(row.suggestedOwnerId).toBe("op-tati");
    expect(row.isSuggested).toBe(true);
    expect(row.channel).toBe("whatsapp");
  });
});

describe("suggestOwner", () => {
  it("suggests Tati for a sonicator or >=40M", () => {
    expect(suggestOwner(card({ title: "Sonicador UP200St" }), profiles)).toBe("op-tati");
    expect(suggestOwner(card({ title: "Hielscher UIP1000" }), profiles)).toBe("op-tati");
    expect(suggestOwner(card({ title: "Equipo grande", clp: "40000000" }), profiles)).toBe("op-tati");
    expect(suggestOwner(card({ title: "Equipo", clp: "39999999" }), profiles)).not.toBe("op-tati");
  });

  it("suggests Rafa when phone else Karla", () => {
    expect(suggestOwner(card({ phone: "+56 9 1234 5678" }), profiles)).toBe("op-rafa");
    expect(suggestOwner(card({ phone: null }), profiles)).toBe("op-karla");
  });

  it("resolves aliases and returns null when the profile is missing", () => {
    const alias: ProfileCard[] = [{ id: "x", displayName: "A", roleLabel: "", key: "tati" }];
    expect(suggestOwner(card({ title: "proyecto" }), alias)).toBe("x");
    expect(suggestOwner(card({ phone: null }), alias)).toBeNull();
  });
});

describe("channels", () => {
  it("whatsapp only for Tati's key", () => {
    expect(WHATSAPP_PROFILE_KEYS).toEqual(["tatiana", "tati"]);
    expect(channelsFor("op-tati", profiles)).toEqual(["llamada", "correo", "whatsapp"]);
    expect(channelsFor("op-rafa", profiles)).toEqual(["llamada", "correo"]);
    expect(channelsFor(null, profiles)).toEqual(["llamada", "correo"]);
  });

  it("normalizeChannel drops whatsapp when owner changes", () => {
    expect(normalizeChannel("whatsapp", "op-tati", profiles, true)).toBe("whatsapp");
    expect(normalizeChannel("whatsapp", "op-rafa", profiles, true)).toBe("llamada");
    expect(normalizeChannel("whatsapp", "op-karla", profiles, false)).toBe("llamada");
    expect(normalizeChannel("correo", "op-rafa", profiles, true)).toBe("correo");
  });

  it("a null channel is llamada with a phone, else correo", () => {
    expect(normalizeChannel(null, "op-rafa", profiles, true)).toBe("llamada");
    expect(normalizeChannel(null, "op-rafa", profiles, false)).toBe("correo");
  });
});

describe("masked phone (viewer role)", () => {
  it("counts as no phone: no Rafa suggestion, no +2 score", () => {
    expect(suggestOwner(card({ phone: "***" }), profiles)).toBe("op-karla");
    expect(scoreCase(card({ title: "Balanza OHAUS", clp: "500000", phone: "***" }))).toBe(scoreCase(card({ title: "Balanza OHAUS", clp: "500000", phone: null })));
  });
});

describe("scoreCase", () => {
  it("score values for a cheap balanza with phone", () => {
    // monto 1 + facilidad 5 + interes 2 + phone 2
    expect(scoreCase(card({ title: "Balanza OHAUS", clp: "500000", phone: "+56 9 1234 5678" }))).toBe(10);
  });

  it("monto bands, facilidad 1 for a sonicator and interes 5 for an inbound reply", () => {
    expect(scoreCase(card({ title: "x", clp: "2999999" }))).toBe(2 + 3 + 2);
    expect(scoreCase(card({ title: "x", clp: "9999999" }))).toBe(3 + 3 + 2);
    expect(scoreCase(card({ title: "x", clp: "29999999" }))).toBe(4 + 3 + 2);
    expect(scoreCase(card({ title: "x", clp: "30000000" }))).toBe(5 + 3 + 2);
    expect(scoreCase(card({ title: "Sonicador", clp: "500000" }))).toBe(1 + 1 + 2);
    const replied = card({ title: "x", clp: "500000", last_contact: { outbound: null, inbound: { at: "2026-11-12T12:00:00Z", subject: null, url: null } } });
    expect(scoreCase(replied)).toBe(1 + 3 + 5);
    const talked = card({ title: "x", clp: "500000", last_call_any: { at: "2026-11-12T12:00:00Z", outcome: "contesto", author_name: null } });
    expect(scoreCase(talked)).toBe(1 + 3 + 4);
  });
});

describe("seguimientoKpis", () => {
  it("kpis count 3 calls today as 3", () => {
    const rows = seguimientoRows(
      [
        card({ call_counts: { total: 3, today: 3 }, last_call_any: { at: "2026-11-18T14:00:00Z", outcome: "no_contesto", author_name: "Persona Uno" } }),
        card({ last_contact: { outbound: { at: "2026-11-18T14:00:00Z", subject: null, url: null }, inbound: null } }),
        // Late: its reminder was due on Monday.
        card({ open_tasks: [{ task_id: "t-late", title: "Seguimiento de CN09876", due_at: "2026-11-16T13:00:00Z", version: 1, owner: null }] }),
      ],
      profiles,
      NOW,
    );
    const resp = {
      items: [],
      total: 3,
      drive_configured: false,
      calls_by_operator: [{ operator_id: "op-tati", operator_name: "Persona Uno", today: 3 }],
    } as PipelineResponse;
    const k = seguimientoKpis(rows, resp, NOW);
    expect(k.callsByOperator).toEqual([{ operatorId: "op-tati", name: "Persona Uno", today: 3 }]);
    expect(k.callsToday).toBe(3);
    expect(k.goal).toBe(PILOT_CALLS_PER_DAY);
    expect(k.emailsToday).toBe(1);
    expect(k.touchedToday).toBe(2);
    expect(k.late).toBe(1);
    expect(k.moneyClp).toBe(1_500_000);
  });
});

describe("Hoy's rhythm and Seguimiento count the same calendar days (audit 2026-11-22)", () => {
  it("a quote sent at 20:00 three days ago is day 3 in both, at 10:00", () => {
    const sentAt = new Date(2026, 10, 19, 20).toISOString();
    const now = new Date(2026, 10, 22, 10);
    const c = card({ revs: [rev({ sent_at: sentAt })], last_contact: { inbound: null, outbound: { at: sentAt, subject: "Cotización", url: null } } });
    const f = followUpsDue([c], now)[0];
    expect(f?.days).toBe(3);
    expect(f?.touchDays).toBe(3);
    expect(seguimientoRows([c], profiles, now)[0].daysSinceTouch).toBe(3);
  });
});

describe("«Atrasado» is the próximo contacto's clock (audit 2026-11-22)", () => {
  const now = new Date(2026, 10, 18, 12);
  const at = (d: number, h = 12) => new Date(2026, 10, d, h).toISOString();
  const reminder = (due: string) => ({ task_id: `t-${due}`, title: "Seguimiento de CN09876", due_at: due, version: 1, owner: null, created_at: at(10) });

  it("untouched for 8 days but set for tomorrow: not late, and the row carries that próximo contacto", () => {
    const c = card({ revs: [rev({ sent_at: at(10) })], open_tasks: [reminder(at(19, 10))] });
    const row = seguimientoRows([c], profiles, now)[0];
    expect(row.daysSinceTouch).toBe(8);
    expect(row.next?.source).toBe("followup");
    expect(row.late).toBe(false);
  });

  it("touched two days ago but its reminder was due yesterday: late", () => {
    const c = card({ revs: [rev({ sent_at: at(16) })], open_tasks: [reminder(at(17, 10))] });
    const row = seguimientoRows([c], profiles, now)[0];
    expect(row.daysSinceTouch).toBe(2);
    expect(row.late).toBe(true);
  });

  it("a case paused until December is not late, whatever the days without a touch", () => {
    const c = card({ revs: [rev({ sent_at: at(1) })], paused_until: "2026-12-15", pause_reason: "Esperando fondos o proyecto" });
    const row = seguimientoRows([c], profiles, now)[0];
    expect(row.next?.source).toBe("pause");
    expect(row.late).toBe(false);
  });
});
