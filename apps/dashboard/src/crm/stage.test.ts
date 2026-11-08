import { describe, expect, it } from "vitest";
import type { OpportunityCardData, RevisionCard } from "./crmTypes";
import { COLUMN_LABEL, moveRefusal } from "./pages/CaseMove";
import {
  BOARD_COLUMNS,
  ENVIADA_BANDS,
  HISTORICAL_STAGE_LABEL,
  LOST_ON_BOARD_DAYS,
  SILENT_AFTER_DAYS,
  boardColumnOf,
  closedAt,
  enviadaBand,
  isOnBoard,
  isSilent,
  monthKeySantiago,
  stageBasis,
  stageDisplay,
} from "./stage";

function rev(origin: string | null): RevisionCard {
  return {
    revision_id: "r",
    revision_no: 1,
    status: "sent",
    origin,
    sent_at: "2026-05-01T00:00:00Z",
    superseded_by_revision_no: null,
    is_active: true,
    document: null,
    gmail: null,
    drive: null,
    quote_number: "1300",
  };
}

function card(over: Partial<OpportunityCardData>, origins: (string | null)[] = ["historical_import"]): OpportunityCardData {
  return {
    opportunity_id: "o",
    title: "t",
    stage: "quoting",
    created_at: null,
    updated_at: null,
    closed_at: null,
    close_reason: null,
    organization: null,
    other_organizations: [],
    contact: null,
    quotes: [{ quote_id: "q", quote_number: "1300", number_origin: null, revisions: origins.map(rev) }],
    quote_numbers: ["1300"],
    revision_count: origins.length,
    latest_revision: null,
    drive_folder: null,
    attention: [],
    status: "ok",
    next_action: { text: "x", source: "suggested", due_at: null },
    ...over,
  };
}

describe("stage basis", () => {
  it("calls an imported, open `quoting` case a historical sent quotation, not a verified status", () => {
    const c = card({});
    expect(stageBasis(c)).toBe("historical_import");
    expect(stageDisplay(c).label).toBe(HISTORICAL_STAGE_LABEL);
    expect(HISTORICAL_STAGE_LABEL).toBe("Enviada · sin decidir"); // never the import's word «histórico»
    expect(stageDisplay(c).title).toMatch(/No confirma el estado comercial actual/);
  });

  it("treats the stage as a CRM record once anything authored exists, the case closes, or the stage moved", () => {
    expect(stageBasis(card({}, ["historical_import", "authored"]))).toBe("crm_record");
    expect(stageBasis(card({ closed_at: "2026-06-01T00:00:00Z", stage: "lost" }))).toBe("crm_record");
    expect(stageBasis(card({ stage: "negotiating" }))).toBe("crm_record");
    expect(stageBasis(card({}, []))).toBe("crm_record");
    expect(stageDisplay(card({ stage: "negotiating" })).label).toBe("Conversación");
  });

  it("never calls a quote sent within the last two weeks «sin decidir»: every registered quote carries the import's origin", () => {
    const now = new Date("2026-10-09T15:00:00Z");
    const sentAt = (iso: string) => ({ ...rev("historical_import"), sent_at: iso });
    const yesterday = card({ quotes: [{ quote_id: "q", quote_number: "01260-26", number_origin: null, revisions: [sentAt("2026-10-08T14:00:00Z")] }] });
    const thirteenDays = card({ quotes: [{ quote_id: "q", quote_number: "01250-26", number_origin: null, revisions: [sentAt("2026-09-26T14:00:00Z")] }] });
    const fifteenDays = card({ quotes: [{ quote_id: "q", quote_number: "01240-26", number_origin: null, revisions: [sentAt("2026-09-24T14:00:00Z")] }] });
    const revised = card({
      quotes: [{ quote_id: "q", quote_number: "01230-26", number_origin: null, revisions: [sentAt("2026-05-01T00:00:00Z"), sentAt("2026-10-01T14:00:00Z")] }],
    });
    expect(stageBasis(yesterday, now)).toBe("crm_record");
    expect(stageDisplay(yesterday, now).label).toBe("Enviada");
    expect(stageBasis(thirteenDays, now)).toBe("crm_record");
    expect(stageBasis(fifteenDays, now)).toBe("historical_import");
    // The newest revision counts: a quote revised last week was decided last week.
    expect(stageBasis(revised, now)).toBe("crm_record");
  });
});

/* ── board rules (Task 7) ─────────────────────────────────────────────────── */

const NOW = new Date("2026-11-08T15:00:00Z");
const DAY = 86_400_000;
const ago = (days: number, hours = 0) => new Date(NOW.getTime() - days * DAY - hours * 3_600_000).toISOString();

/** An open `quoting` case whose first quote went out `days` ago, nobody written since. */
function sent(days: number, over: Partial<OpportunityCardData> = {}, status = "sent", origin: string | null = "authored"): OpportunityCardData {
  const r = { ...rev(origin), status, sent_at: status === "sent" || origin === "historical_import" ? ago(days) : null };
  return card({
    quotes: [{ quote_id: "q", quote_number: "01999-26", number_origin: null, revisions: [r] }],
    latest_revision: r,
    version: 2,
    ...over,
  });
}

describe("Silenciosas", () => {
  it("quoting 22 days, no reply → silenciosas", () => {
    const c = sent(22);
    expect(isSilent(c, NOW)).toBe(true);
    expect(boardColumnOf(c, NOW)).toBe("silenciosas");
    expect(SILENT_AFTER_DAYS).toBe(21);
  });

  it("21 days → enviada", () => {
    const c = sent(21, {}, "sent");
    expect(isSilent(c, NOW)).toBe(false);
    expect(boardColumnOf(c, NOW)).toBe("enviada");
  });

  it("client replied after the quote → not silent", () => {
    const c = sent(30, { last_contact: { inbound: { at: ago(25), subject: "Re: cotización", url: null }, outbound: null } });
    expect(isSilent(c, NOW)).toBe(false);
    expect(boardColumnOf(c, NOW)).toBe("enviada");
  });

  it("no_reply «gracias» → not silent", () => {
    const c = sent(30, { last_contact: { inbound: { at: ago(25), subject: "Re: gracias", url: null, no_reply: true }, outbound: null } });
    expect(isSilent(c, NOW)).toBe(false);
  });

  it("our follow-up emails only → still silent", () => {
    const c = sent(30, { last_contact: { inbound: null, outbound: { at: ago(10), subject: "Seguimiento", url: null } } });
    expect(isSilent(c, NOW)).toBe(true);
    expect(boardColumnOf(c, NOW)).toBe("silenciosas");
  });

  it("contestó call after the quote → not silent", () => {
    expect(isSilent(sent(30, { last_call: { at: ago(5), outcome: "contesto" } }), NOW)).toBe(false);
    expect(isSilent(sent(30, { last_call: { at: ago(5), outcome: "reunion" } }), NOW)).toBe(false);
    // A call nobody answered is not the client engaging; one before the quote is old news.
    expect(isSilent(sent(30, { last_call: { at: ago(5), outcome: "no_contesto" } }), NOW)).toBe(true);
    expect(isSilent(sent(30, { last_call: { at: ago(40), outcome: "contesto" } }), NOW)).toBe(true);
  });

  it("future task → pausa, not silenciosas", () => {
    const task = { task_id: "t", title: "Retomar: fondos", due_at: ago(-5), version: 1, owner: null };
    const c = sent(40, { open_tasks: [task] });
    expect(isSilent(c, NOW)).toBe(false);
    expect(boardColumnOf(c, NOW)).toBe("pausa");
  });

  it("draft first quote → not silent", () => {
    const c = sent(40, {}, "draft", "authored");
    expect(isSilent(c, NOW)).toBe(false);
    expect(boardColumnOf(c, NOW)).toBe("enviada");
  });

  it("historical-import quoting 2024 → silenciosas", () => {
    const r = { ...rev("historical_import"), sent_at: "2024-03-12T14:00:00Z" };
    const c = card({ quotes: [{ quote_id: "q", quote_number: "00812", number_origin: null, revisions: [r] }], latest_revision: r });
    expect(isSilent(c, NOW)).toBe(true);
    expect(boardColumnOf(c, NOW)).toBe("silenciosas");
  });

  it("only an open quoting case is silent", () => {
    expect(isSilent(sent(40, { stage: "negotiating" }), NOW)).toBe(false);
    expect(isSilent(sent(40, { stage: "lost", closed_at: ago(1) }), NOW)).toBe(false);
  });

  it("columns run solicitada, enviada, silenciosas, conversacion, pausa, ganada, perdida; Silenciosas takes no drop", () => {
    expect(BOARD_COLUMNS.map((c) => c.key)).toEqual(["solicitada", "enviada", "silenciosas", "conversacion", "pausa", "ganada", "perdida"]);
    const col = BOARD_COLUMNS.find((c) => c.key === "silenciosas");
    expect(col?.label).toBe("Silenciosas");
    expect(col?.target).toBeNull();
    expect(col?.stages).toEqual([]);
  });

  it("moveRefusal to silenciosas", () => {
    expect(moveRefusal(sent(3), "silenciosas", NOW)).toBe("Silenciosas se llena sola: un caso entra a los 21 días sin respuesta.");
    expect(COLUMN_LABEL.silenciosas).toBe("Silenciosas");
  });
});

describe("closed cases on the board", () => {
  it("lost 8 days ago → not on board", () => {
    expect(isOnBoard(card({ stage: "lost", closed_at: ago(8) }), NOW)).toBe(false);
    expect(isOnBoard(card({ stage: "abandoned", closed_at: ago(8) }), NOW)).toBe(false);
    expect(LOST_ON_BOARD_DAYS).toBe(7);
  });

  it("lost 6 days ago → on board", () => {
    expect(isOnBoard(card({ stage: "lost", closed_at: ago(6) }), NOW)).toBe(true);
  });

  it("lost with closed_at null and updated_at 30 days ago → off board", () => {
    const c = card({ stage: "lost", closed_at: null, updated_at: ago(30) });
    expect(closedAt(c)).toBe(ago(30));
    expect(isOnBoard(c, NOW)).toBe(false);
  });

  it("won 400 days ago → on board", () => {
    expect(isOnBoard(card({ stage: "won", closed_at: ago(400) }), NOW)).toBe(true);
  });

  it("an open case is always on the board and has no closing date", () => {
    const c = sent(400, { updated_at: ago(300) });
    expect(closedAt(c)).toBeNull();
    expect(isOnBoard(c, NOW)).toBe(true);
  });
});

describe("Enviada bands", () => {
  it("enviadaBand 0, 7, 8, 21", () => {
    expect(enviadaBand(sent(0), NOW)).toBe("d0_7");
    expect(enviadaBand(sent(7, {}), NOW)).toBe("d0_7");
    expect(enviadaBand(sent(8), NOW)).toBe("d8_21");
    expect(enviadaBand(sent(21), NOW)).toBe("d8_21");
    expect(enviadaBand(sent(22), NOW)).toBeNull();
    expect(enviadaBand(sent(3, {}, "draft"), NOW)).toBeNull();
    expect(ENVIADA_BANDS).toEqual([
      { key: "d0_7", label: "0–7 días" },
      { key: "d8_21", label: "8–21 días" },
    ]);
  });
});

describe("monthKeySantiago", () => {
  it("reads the month on Santiago's clock, not UTC's", () => {
    expect(monthKeySantiago("2026-11-01T02:30:00Z")).toBe("2026-10");
    expect(monthKeySantiago("2026-11-01T12:00:00Z")).toBe("2026-11");
    expect(monthKeySantiago("2026-01-01T02:00:00Z")).toBe("2025-12");
  });
});
