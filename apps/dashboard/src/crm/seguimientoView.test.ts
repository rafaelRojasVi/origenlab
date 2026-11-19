import { describe, expect, it } from "vitest";
import type { ProfileCard } from "../api/authClient";
import type { OpportunityCardData } from "./crmTypes";
import { seguimientoRows, type SegRow } from "./seguimiento";
import {
  EMPTY_FILTERS,
  agendaGroups,
  agendaKey,
  applyFilters,
  boardColumns,
  boardMoveBody,
  facetCounts,
  filterGroups,
  knownFilters,
  readSegQuery,
  sortRows,
  writeSegQuery,
  type FilterContext,
  type SegFilters,
} from "./seguimientoView";

// Every value below is invented; the repository is public.
const NOW = new Date("2026-11-18T15:00:00Z"); // Wednesday, 12:00 in Santiago (UTC-3)
const PROFILES: ProfileCard[] = [
  { id: "op-tati", displayName: "Tati", roleLabel: "", key: "tati" },
  { id: "op-rafa", displayName: "Rafa", roleLabel: "", key: "rafael" },
  { id: "op-karla", displayName: "Karla", roleLabel: "", key: "karla" },
];
const CTX: FilterContext = { me: "op-rafa", profiles: PROFILES, now: NOW };

interface Spec {
  id: string;
  owner?: string | null;
  channel?: "llamada" | "correo" | "whatsapp" | null;
  phone?: string | null;
  amount?: string | null;
  sent?: string;
  stage?: string;
  today?: number;
  total?: number;
  answered?: boolean;
  due?: string | null;
  lastCallToday?: boolean;
}

function card(s: Spec): OpportunityCardData {
  const rev = { revision_id: `r-${s.id}`, revision_no: 1, status: "sent", origin: null, sent_at: s.sent ?? "2026-11-10T15:00:00Z", superseded_by_revision_no: null, is_active: true, document: null, gmail: null, drive: null, quote_number: `CN0${s.id}`, candidates: [] };
  return {
    opportunity_id: s.id, title: "Cotización", stage: s.stage ?? "quoting", version: 7, created_at: "2026-11-01T12:00:00Z", updated_at: null, closed_at: null, close_reason: null,
    organization: { organization_id: `org-${s.id}`, name: `Instituto Ficticio ${s.id}`, confirmation: "confirmed", version: 1 }, other_organizations: [],
    contact: { source: "crm_participant", name: "Persona Ficticia", address: "persona@ejemplo.invalid", others: 0, phone: s.phone ?? null, phone_confirmed: true },
    quotes: [{ quote_id: `q-${s.id}`, quote_number: `CN0${s.id}`, revisions: [rev] }], quote_numbers: [`CN0${s.id}`], revision_count: 1,
    money: s.amount === null ? [] : [{ currency: "CLP", net_total: s.amount ?? "500000" }], latest_revision: rev, drive_folder: null, attention: [], status: "ok",
    next_action: s.due ? { text: "Seguimiento", source: "task", due_at: s.due } : { text: "", source: "suggested", due_at: null },
    follow_up: { operator_id: s.owner ?? null, operator_name: null, channel: s.channel ?? null },
    call_counts: { total: s.total ?? 0, today: s.today ?? 0 },
    last_call_any: s.lastCallToday ? { at: "2026-11-18T14:00:00Z", outcome: "no_contesto", author_name: null } : null,
    last_call: s.answered ? { at: "2026-11-01T14:00:00Z", outcome: "contesto" } : null,
  } as unknown as OpportunityCardData;
}

const rowsOf = (specs: Spec[]): SegRow[] => seguimientoRows(specs.map(card), PROFILES, NOW);
const ids = (rows: SegRow[]) => rows.map((r) => r.card.opportunity_id).sort();
const f = (over: Partial<SegFilters>): SegFilters => ({ ...EMPTY_FILTERS, ...over });

describe("filters", () => {
  const rows = rowsOf([
    { id: "1", owner: "op-karla", channel: "llamada", phone: "+56 9 1234 5678", amount: "500000", stage: "quoting", total: 0 },
    { id: "2", owner: "op-rafa", channel: "correo", amount: "5000000", stage: "negotiating", today: 1, total: 2, answered: true, lastCallToday: true },
    { id: "3", owner: null, channel: null, amount: "20000000", stage: "quoting", total: 1, due: "2026-11-18T12:00:00Z" },
    { id: "4", owner: "op-tati", channel: "whatsapp", phone: "+56 9 1234 5678", amount: null, stage: "quoting", sent: "2026-11-17T15:00:00Z" },
  ]);

  it("matches every chip of every group", () => {
    const one = (g: keyof SegFilters, v: string) => ids(applyFilters(rows, f({ [g]: [v] }), CTX));
    // «3» has no encargado: Karla is its suggestion (no phone), so it is hers and unconfirmed.
    expect(one("encargado", "karla")).toEqual(["1", "3"]);
    expect(one("encargado", "mios")).toEqual(["2"]);
    expect(one("encargado", "sin")).toEqual(["3"]);
    expect(one("canal", "llamada")).toEqual(["1"]);
    expect(one("canal", "correo")).toEqual(["2", "3"]); // no phone, no channel → correo
    expect(one("canal", "whatsapp")).toEqual(["4"]);
    expect(one("estado", "atrasado")).toEqual(["1", "3"]);
    expect(one("estado", "toca_hoy")).toEqual(["3"]);
    expect(one("estado", "nunca")).toEqual(["1", "4"]);
    expect(one("estado", "llamado_hoy")).toEqual(["2"]);
    expect(one("estado", "contesto")).toEqual(["2"]);
    expect(one("etapa", "negotiating")).toEqual(["2"]);
    expect(one("fono", "con")).toEqual(["1", "4"]);
    expect(one("fono", "sin")).toEqual(["2", "3"]);
    expect(one("monto", "lt1")).toEqual(["1"]);
    expect(one("monto", "1a10")).toEqual(["2"]);
    expect(one("monto", "gt10")).toEqual(["3"]);
    expect(one("monto", "sin")).toEqual(["4"]);
  });

  it("is OR inside a group and AND across groups", () => {
    expect(ids(applyFilters(rows, f({ canal: ["llamada", "whatsapp"] }), CTX))).toEqual(["1", "4"]);
    expect(ids(applyFilters(rows, f({ canal: ["llamada", "whatsapp"], fono: ["con"], monto: ["sin"] }), CTX))).toEqual(["4"]);
    expect(ids(applyFilters(rows, f({ canal: ["correo"], estado: ["contesto"] }), CTX, "ficticio 2"))).toEqual(["2"]);
    expect(applyFilters(rows, f({ canal: ["correo"] }), CTX, "nadie")).toHaveLength(0);
  });

  it("counts each chip as if added to the other groups' choices", () => {
    const groups = filterGroups(rows, CTX);
    const counts = facetCounts(rows, groups, f({ fono: ["con"] }), CTX);
    // Inside «Teléfono» the count ignores the group's own choice.
    expect(counts.get("fono:con")).toBe(2);
    expect(counts.get("fono:sin")).toBe(2);
    // Other groups count within «Con teléfono».
    expect(counts.get("canal:llamada")).toBe(1);
    expect(counts.get("canal:correo")).toBe(0);
    expect(counts.get("monto:sin")).toBe(1);
  });

  it("lists only the stages present and drops unknown values", () => {
    const groups = filterGroups(rows, CTX);
    expect(groups.find((g) => g.key === "etapa")!.options.map((o) => o.value)).toEqual(["quoting", "negotiating"]);
    expect(groups.find((g) => g.key === "encargado")!.options.map((o) => o.label)).toContain("Sin confirmar");
    expect(knownFilters(f({ encargado: ["nadie", "karla"], etapa: ["won"] }), groups)).toEqual(f({ encargado: ["karla"] }));
  });
});

describe("sortRows", () => {
  const rows = rowsOf([
    { id: "a", amount: "100", sent: "2026-11-17T15:00:00Z", due: "2026-11-20T12:00:00Z" },
    { id: "b", amount: "9000000", sent: "2026-11-01T15:00:00Z", phone: "+56 9 1234 5678" },
    { id: "c", amount: null, sent: "2026-11-12T15:00:00Z", due: "2026-11-19T12:00:00Z" },
  ]);
  const order = (o: Parameters<typeof sortRows>[1]) => sortRows(rows, o).map((r) => r.card.opportunity_id);

  it("orders by score, amount, days and next contact", () => {
    expect(order("prioridad")[0]).toBe("b"); // highest amount and a phone
    expect(order("monto")).toEqual(["b", "a", "c"]);
    expect(order("dias")).toEqual(["b", "c", "a"]);
    expect(order("proximo")).toEqual(["c", "a", "b"]);
  });
});

describe("agenda", () => {
  it("groups by the Santiago day of the next contact, hiding empty groups", () => {
    const rows = rowsOf([
      { id: "late", due: "2026-11-17T20:00:00Z" },
      { id: "today", due: "2026-11-18T20:00:00Z" },
      { id: "tomorrow", due: "2026-11-19T15:00:00Z" },
      { id: "week", due: "2026-11-22T15:00:00Z" }, // Sunday
      { id: "later", due: "2026-11-23T15:00:00Z" }, // next Monday
      { id: "none" },
    ]);
    const groups = agendaGroups(rows, NOW);
    expect(groups.map((g) => [g.key, g.label, g.rows.map((r) => r.card.opportunity_id)])).toEqual([
      ["atrasados", "Atrasados", ["late"]],
      ["hoy", "Hoy", ["today"]],
      ["manana", "Mañana", ["tomorrow"]],
      ["semana", "Esta semana", ["week"]],
      ["despues", "Más adelante", ["later"]],
      ["sin", "Sin fecha", ["none"]],
    ]);
    expect(agendaGroups(rowsOf([{ id: "x" }]), NOW).map((g) => g.key)).toEqual(["sin"]);
  });

  it("uses Santiago midnight, not UTC", () => {
    // 02:30 UTC on the 19th is 23:30 on the 18th in Santiago: still «Hoy».
    const [row] = rowsOf([{ id: "n", due: "2026-11-19T02:30:00Z" }]);
    expect(agendaKey(row, NOW)).toBe("hoy");
    // 03:30 UTC on the 19th is 00:30 on the 19th: «Mañana».
    const [next] = rowsOf([{ id: "m", due: "2026-11-19T03:30:00Z" }]);
    expect(agendaKey(next, NOW)).toBe("manana");
    // At 23:50 Santiago, a due at 00:10 the next day is tomorrow.
    expect(agendaKey(next, new Date("2026-11-19T02:50:00Z"))).toBe("manana");
  });

  it("ignores a suggested next step's date", () => {
    const [row] = rowsOf([{ id: "s" }]);
    row.card.next_action = { text: "x", source: "suggested", due_at: "2026-11-18T12:00:00Z" };
    expect(agendaKey(row, NOW)).toBe("sin");
  });

  it("reads the open «Seguimiento» reminder first, like the sheet's «Próximo»", () => {
    const [row] = rowsOf([{ id: "r" }]);
    row.card.open_tasks = [
      { task_id: "t1", title: "Enviar ficha", due_at: "2026-11-18T13:00:00Z", version: 1, owner: null },
      { task_id: "t2", title: "Seguimiento · llamar", due_at: "2026-11-19T15:00:00Z", version: 1, owner: null },
    ];
    expect(agendaKey(row, NOW)).toBe("manana");
    row.card.open_tasks = [{ task_id: "t1", title: "Enviar ficha", due_at: "2026-11-18T13:00:00Z", version: 1, owner: null }];
    expect(agendaKey(row, NOW)).toBe("hoy");
  });
});

describe("Por encargado", () => {
  const rows = rowsOf([
    { id: "1", owner: "op-karla" },
    { id: "2", owner: "op-tati", channel: "whatsapp" },
    { id: "3", owner: null },
  ]);

  it("puts Karla, Rafa and Tati first and «Sin confirmar» last", () => {
    const cols = boardColumns(rows, PROFILES);
    expect(cols.map((c) => [c.label, c.rows.map((r) => r.card.opportunity_id)])).toEqual([
      ["Karla", ["1"]],
      ["Rafa", []],
      ["Tati", ["2"]],
      ["Sin confirmar", ["3"]],
    ]);
  });

  it("keeps the channel, except WhatsApp leaving Tati", () => {
    const tati = rows.find((r) => r.card.opportunity_id === "2")!.card;
    expect(boardMoveBody(tati, "op-karla", PROFILES)).toEqual({ opportunity_id: "2", opportunity_version: 7, follow_up_operator_id: "op-karla", channel: "llamada" });
    const karla = rowsOf([{ id: "9", owner: "op-karla", channel: "correo" }])[0].card;
    expect(boardMoveBody(karla, null, PROFILES)).toMatchObject({ follow_up_operator_id: null, channel: "correo" });
  });
});

describe("the query in the address", () => {
  it("reads and writes the view, filters and order, keeping other keys", () => {
    const q = readSegQuery(new URLSearchParams("caso=x&vista=agenda&canal=llamada,correo&orden=monto"));
    expect(q).toEqual({ view: "agenda", order: "monto", filters: f({ canal: ["llamada", "correo"] }) });
    const out = writeSegQuery(new URLSearchParams("caso=x"), { ...q, view: "encargado", order: "prioridad", filters: f({ estado: ["nunca"] }) });
    expect(out.toString()).toBe("caso=x&estado=nunca&vista=encargado");
    expect(readSegQuery(new URLSearchParams("vista=nope"), "agenda").view).toBe("agenda");
  });
});
