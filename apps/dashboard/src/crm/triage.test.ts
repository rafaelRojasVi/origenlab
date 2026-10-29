import { describe, expect, it } from "vitest";
import type { OpportunityCardData, RevisionCard } from "./crmTypes";
import {
  approvalMove, cardsById, isOpenableRequest, openCaseTitle, reopenProposal, sortInbox, stageLabel, wonProposal, TRIAGE_PATHS, type TriageReading,
} from "./triage";
import { MAIL_RULES_PATHS } from "./mailRules";
import { parseProducts } from "./pages/TriagePanel";

const CASE = { opportunity_id: "o-1", title: "Balanzas", stage: "quoting", version: 4 };

// Every value below is invented; the repository is public.
function revision(no: number, status = "sent", superseded: number | null = null): RevisionCard {
  return {
    revision_id: `r-${no}`, revision_no: no, status, origin: "gmail_capture", sent_at: `2026-10-0${no}T10:00:00Z`,
    superseded_by_revision_no: superseded, is_active: superseded === null, document: null, gmail: null, drive: null,
    quote_number: "01253-26",
  };
}

function card(over: Partial<OpportunityCardData> = {}, revisions: RevisionCard[] = [revision(1)]): OpportunityCardData {
  return {
    opportunity_id: "o-1", title: "Balanzas", stage: "quoting", created_at: null, updated_at: null, closed_at: null,
    close_reason: null, organization: null, other_organizations: [], contact: null,
    quotes: [{ quote_id: "q-1", quote_number: "01253-26", number_origin: "printed", revisions }],
    quote_numbers: ["01253-26"], revision_count: revisions.length, latest_revision: revisions[0] ?? null,
    drive_folder: null, attention: [], status: "ok", next_action: { text: "", source: "suggested", due_at: null },
    ...over,
  } as OpportunityCardData;
}

describe("wonProposal — a purchase order the triage read on a case that can be won", () => {
  const po = { class: "purchase_order", intent: null, cases: [CASE] };
  const byId = (c: OpportunityCardData) => new Map([[c.opportunity_id, c]]);

  it("proposes the case's sent revisions when the case is at Cotizando or Conversación", () => {
    const got = wonProposal(po, byId(card()));
    expect(got?.case).toEqual(CASE);
    expect(got?.revisions.map((r) => [r.quote_id, r.revision_no])).toEqual([["q-1", 1]]);
    expect(wonProposal(po, byId(card({ stage: "negotiating" })))?.case.stage).toBe("negotiating");
  });

  it("takes the board's stage over the reading's, which may be minutes old", () => {
    const stale = { ...po, cases: [{ ...CASE, stage: "lead" }] };
    expect(wonProposal(stale, byId(card({ stage: "negotiating" })))?.case.stage).toBe("negotiating");
  });

  it("lists every sent, unreplaced revision, newest first, so the row can ask which one", () => {
    const two = card({}, [revision(1, "sent", 2), revision(2), revision(3)]);
    expect(wonProposal(po, byId(two))?.revisions.map((r) => r.revision_no)).toEqual([3, 2]);
  });

  it("proposes nothing for a reading that is not a purchase order, a thread on 0 or 2 cases, a case not on the board, a won or closed case, or a case with nothing sent", () => {
    expect(wonProposal({ class: "quote_request", intent: null, cases: [CASE] }, byId(card()))).toBeNull();
    expect(wonProposal({ ...po, cases: [] }, byId(card()))).toBeNull();
    expect(wonProposal({ ...po, cases: [CASE, { ...CASE, opportunity_id: "o-2" }] }, byId(card()))).toBeNull();
    expect(wonProposal(po, new Map())).toBeNull();
    expect(wonProposal(po, byId(card({ stage: "won" })))).toBeNull();
    expect(wonProposal(po, byId(card({ stage: "lead" })))).toBeNull();
    expect(wonProposal(po, byId(card({ closed_at: "2026-10-09T10:00:00Z" })))).toBeNull();
    expect(wonProposal(po, byId(card({}, [revision(1, "draft")])))).toBeNull();
    expect(wonProposal(po, byId(card({}, [revision(1, "sent", 2)])))).toBeNull();
  });

  it("reads the intent too: the model may say «purchase_order» where the rules said business_other", () => {
    expect(wonProposal({ class: "business_other", intent: "purchase_order", cases: [CASE] }, byId(card()))).not.toBeNull();
  });
});

describe("«Abrir caso» — which rows offer it and what the case is called", () => {
  it("offers it on a quote request no case holds, by class or by intent", () => {
    expect(isOpenableRequest({ class: "quote_request", intent: null, cases: [] })).toBe(true);
    expect(isOpenableRequest({ class: "business_other", intent: "quote_request", cases: [] })).toBe(true);
    expect(isOpenableRequest({ class: "quote_request", intent: null, cases: [CASE] })).toBe(false);
    expect(isOpenableRequest({ class: "business_other", intent: "technical_question", cases: [] })).toBe(false);
  });

  it("titles the case with the subject minus reply prefixes, or the sender's domain", () => {
    expect(openCaseTitle({ subject: "RE: Re: Solicitud cotización balanza", sender: "x@uni.example" })).toBe("Solicitud cotización balanza");
    expect(openCaseTitle({ subject: "RV: Fwd: Gradillas", sender: null })).toBe("Gradillas");
    expect(openCaseTitle({ subject: "  ", sender: "persona@uni.example" })).toBe("Solicitud por correo · uni.example");
    expect(openCaseTitle({ subject: null, sender: null })).toBe("Solicitud por correo");
    expect(openCaseTitle({ subject: "x".repeat(500), sender: null })).toHaveLength(400);
  });
});

describe("approvalMove — what «Aprobar» does to the case", () => {
  it("moves the one case of the thread to the suggested stage when the board can walk there", () => {
    expect(approvalMove({ stage: "negotiating", cases: [CASE] })).toEqual({ case: CASE, to: "negotiating" });
    expect(approvalMove({ stage: "lost", cases: [CASE] })).toEqual({ case: CASE, to: "lost" });
  });

  it("moves nothing when the stage is the same, not a case, unclear, «Ganada», or the thread is on 0 or 2 cases", () => {
    expect(approvalMove({ stage: "quoting", cases: [CASE] })).toBeNull();
    expect(approvalMove({ stage: "not_a_case", cases: [CASE] })).toBeNull();
    expect(approvalMove({ stage: "unclear", cases: [CASE] })).toBeNull();
    expect(approvalMove({ stage: "won", cases: [CASE] })).toBeNull(); // needs «Marcar ganada» with its revision
    expect(approvalMove({ stage: "negotiating", cases: [] })).toBeNull();
    expect(approvalMove({ stage: "negotiating", cases: [CASE, { ...CASE, opportunity_id: "o-2" }] })).toBeNull();
    expect(approvalMove({ stage: "lead", cases: [{ ...CASE, stage: "won" }] })).toBeNull(); // a closed case
  });
});

describe("the triage vocabulary in the board's words", () => {
  it("labels stages as the Tablero does, plus the two non-stage answers", () => {
    expect(stageLabel("negotiating")).toBe("Conversación");
    expect(stageLabel("not_a_case")).toBe("No es un caso");
    expect(stageLabel("unclear")).toBe("No se sabe");
    expect(stageLabel(null)).toBe("—");
  });

  it("reads the allow-listed queue; the verdict goes through the mail-rules client", () => {
    expect(TRIAGE_PATHS).toEqual({ readings: "/v2/workspace/triage-readings" });
    expect(MAIL_RULES_PATHS.reviewTriage).toBe("/v2/commands/review-triage");
  });
});

describe("parseProducts — the correction's product lines", () => {
  it("reads «2 × balanza», «3x pipeta» and plain lines, skipping blanks", () => {
    expect(parseProducts("2 × balanza analítica\n\n3x pipeta pasteur\nagitador")).toEqual([
      { description: "balanza analítica", model: null, quantity: 2, catalog_product_id: null },
      { description: "pipeta pasteur", model: null, quantity: 3, catalog_product_id: null },
      { description: "agitador", model: null, quantity: null, catalog_product_id: null },
    ]);
  });
});

describe("sortInbox — which emails «Hoy» asks about", () => {
  const base = {
    source_record_id: "s", version: "v", class: "quote_request", reasons: [], model_state: "off", stage: null,
    intent: null, urgency: null, summary_es: null, needs_reply: null, requester_organization: null, products: [],
    candidates: [], cases: [], transitions: [], review: null, sender_is_supplier: false,
  };
  const r = (id: string, over: Partial<TriageReading>): TriageReading =>
    ({ ...base, assertion_id: id, subject: id, sender: `${id}@cliente.example`, sent_at: "2026-10-09T10:00:00Z",
       thread_id: `t-${id}`, ...over }) as TriageReading;

  it("asks only about new emails from people, one per thread, newest first", () => {
    const { ask, hidden } = sortInbox([
      r("nuevo", { thread_id: "t1" }),
      r("mismo-hilo", { thread_id: "t1", sent_at: "2026-10-08T10:00:00Z" }),
      r("en-caso", { cases: [CASE] }),
      r("proveedor", { sender_is_supplier: true }),
      r("oferta", { intent: "supplier_offer" }),
      r("aviso", { sender: "no-reply@portal.example" }),
      r("info", { sender: "info@publicidad.example" }),
      r("notas", { sender: "gemini-notes@google.example" }),
      r("reenvio", { sender: "contacto@labdelivery.cl" }),
      r("masivo", { class: "bulk" }),
    ]);
    expect(ask.map((x) => x.assertion_id)).toEqual(["nuevo"]);
    expect(hidden).toEqual({ "en un caso": 1, proveedor: 2, "aviso automático": 4, "reenvío antiguo": 1, "mismo hilo": 1 });
  });

  it("hides courier and loose-noreply senders as «aviso automático» even before re-triage (2026-10-29)", () => {
    // Already-triaged rows: class is business_other/purchase_order (pre-rule-change), but the
    // dashboard's widened check hides them without waiting for the worker to re-triage.
    const { ask, hidden } = sortInbox([
      r("dhl", { sender: "tracking@dhl.com", class: "purchase_order" }),
      r("dhl-sub", { sender: "cl@express.dhl.com", class: "business_other" }),
      r("icliente", { sender: "no_responder@icliente.com", class: "business_other" }),
      r("noreply-loose", { sender: "drive-shares-dm-noreply@google.com", class: "business_other" }),
      r("real", {}),
    ]);
    expect(ask.map((x) => x.assertion_id)).toEqual(["real"]);
    expect(hidden).toEqual({ "aviso automático": 4 });
  });

  it("hides an email older than 14 days that contacto@ already answered on its thread (2026-10-19)", () => {
    const now = new Date("2026-10-30T12:00:00Z");
    const { ask, hidden } = sortInbox([
      r("viejo-respondido", { sent_at: "2026-10-09T10:00:00Z", answered_at: "2026-10-09T12:00:00Z" }),
      r("viejo-sin-respuesta", { sent_at: "2026-10-09T10:00:00Z", answered_at: null }),
      r("nuevo-respondido", { sent_at: "2026-10-25T10:00:00Z", answered_at: "2026-10-25T11:00:00Z" }),
      r("api-antigua", { sent_at: "2026-10-01T10:00:00Z" }),
    ], [], now);
    expect(ask.map((x) => x.assertion_id)).toEqual(["nuevo-respondido", "viejo-sin-respuesta", "api-antigua"]);
    expect(hidden).toEqual({ "ya respondido": 1 });
  });

  it("asks «¿Marcar ganada?» for a purchase order on a winnable case, first, and still counts the rest «en un caso»", () => {
    const won = { ...CASE, opportunity_id: "o-won", stage: "won" };
    const { ask, hidden } = sortInbox(
      [
        r("nuevo", { sent_at: "2026-10-10T10:00:00Z" }),
        r("oc", { class: "purchase_order", cases: [CASE], sent_at: "2026-10-01T10:00:00Z" }),
        r("oc-ganada", { class: "purchase_order", cases: [won] }),
        r("respuesta", { cases: [CASE] }),
      ],
      [card(), card({ opportunity_id: "o-won", stage: "won" })],
    );
    expect(ask.map((x) => x.assertion_id)).toEqual(["oc", "nuevo"]);
    expect(hidden).toEqual({ "en un caso": 2 });
  });

  it("asks «¿Reabrir?» for a person's reply on a closed case, first; an open or won case keeps it «en un caso»", () => {
    const NOW = new Date("2026-10-10T12:00:00Z");
    const lost = { ...CASE, opportunity_id: "o-lost", stage: "lost", closed_at: "2026-09-20T10:00:00Z" };
    const old = { ...CASE, opportunity_id: "o-old", stage: "abandoned", closed_at: "2026-05-01T10:00:00Z" };
    const won = { ...CASE, opportunity_id: "o-won", stage: "won", closed_at: "2026-09-20T10:00:00Z" };
    const { ask, hidden } = sortInbox(
      [
        r("nuevo", { sent_at: "2026-10-10T10:00:00Z" }),
        r("vuelve", { class: "business_other", cases: [lost], sent_at: "2026-10-01T10:00:00Z" }),
        r("vuelve-antiguo", { class: "quote_request", cases: [old], sent_at: "2026-09-30T10:00:00Z" }),
        r("abierto", { cases: [CASE] }),
        r("ganado", { cases: [won] }),
        r("rebote", { class: "bounce", cases: [lost] }),
      ],
      [],
      NOW,
    );
    // A case closed more than 90 days ago no longer holds its thread: that request is new mail.
    expect(ask.map((x) => x.assertion_id)).toEqual(["vuelve", "nuevo", "vuelve-antiguo"]);
    expect(hidden).toEqual({ "en un caso": 3 }); // the open one, the won one, and the bounce on the lost one
    const recent = reopenProposal(r("vuelve", { cases: [lost] }), new Map(), NOW)!;
    expect([recent.case.opportunity_id, recent.daysSinceClose, recent.stage]).toEqual(["o-lost", 20, "negotiating"]);
    expect(reopenProposal(r("vuelve-antiguo", { cases: [old] }), new Map(), NOW)).toBeNull();
    expect(isOpenableRequest(r("vuelve-antiguo", { class: "quote_request", cases: [old] }), new Map(), NOW)).toBe(true);
  });

  it("with the board's cards, a closed case that never named its requester reopens at «Solicitada»", () => {
    const NOW = new Date("2026-10-10T12:00:00Z");
    const lost = { ...CASE, opportunity_id: "o-lost", stage: "lost" };
    const cards = [
      card({ opportunity_id: "o-lost", stage: "lost", closed_at: "2026-10-01T10:00:00Z", organization: null,
             requesting_institution_confirmation: null }),
      card({ opportunity_id: "o-lost2", stage: "lost", closed_at: "2026-10-01T10:00:00Z",
             organization: { organization_id: "org-1", name: "Laboratorio Ejemplo", confirmation: "confirmed" },
             requesting_institution_confirmation: "confirmed" }),
    ];
    const noRequester = reopenProposal(r("a", { cases: [lost] }), cardsById(cards), NOW)!;
    expect([noRequester.stage, noRequester.daysSinceClose]).toEqual(["lead", 9]);
    const withRequester = reopenProposal(r("b", { cases: [{ ...lost, opportunity_id: "o-lost2" }] }), cardsById(cards), NOW)!;
    expect(withRequester.stage).toBe("negotiating");
    // Several cases on the thread: all must be closed; the most recently closed one is reopened.
    expect(reopenProposal(r("c", { cases: [lost, CASE] }), cardsById(cards), NOW)).toBeNull();
  });

  it("a case an undo discarded never holds its email: the request is new mail, with «Abrir caso»", () => {
    const NOW = new Date("2026-10-10T12:00:00Z");
    const discarded = { ...CASE, opportunity_id: "o-d", stage: "abandoned", closed_at: "2026-10-10T09:00:00Z",
                        close_reason: "discarded_by_correction" };
    const reading = r("pago", { class: "quote_request", cases: [discarded], sent_at: "2026-10-08T20:10:00Z" });
    expect(reopenProposal(reading, new Map(), NOW)).toBeNull();
    expect(isOpenableRequest(reading, new Map(), NOW)).toBe(true);
    const { ask, hidden } = sortInbox([reading], [], NOW);
    expect(ask.map((x) => x.assertion_id)).toEqual(["pago"]);
    expect(hidden).toEqual({});
    // Even after the closing: a discarded case is never proposed for reopening.
    expect(reopenProposal({ ...reading, sent_at: "2026-10-10T11:00:00Z" }, new Map(), NOW)).toBeNull();
  });

  it("an email that was already on the thread when the case was closed is not «volvió a escribir»", () => {
    const NOW = new Date("2026-10-10T12:00:00Z");
    const closedAfter = { ...CASE, opportunity_id: "o-x", stage: "abandoned", closed_at: "2026-10-09T15:00:00Z" };
    // Sent the day before the closing: whoever closed the case had it in front of them.
    expect(reopenProposal(r("before", { cases: [closedAfter], sent_at: "2026-10-08T20:10:00Z" }), new Map(), NOW)).toBeNull();
    expect(reopenProposal(r("after", { cases: [closedAfter], sent_at: "2026-10-10T09:00:00Z" }), new Map(), NOW)).not.toBeNull();
    const { ask, hidden } = sortInbox([r("before", { cases: [closedAfter], sent_at: "2026-10-08T20:10:00Z" })], [], NOW);
    expect(ask).toEqual([]);
    expect(hidden).toEqual({ "en un caso": 1 });
  });

  it("without the board's cards, a purchase order on a case is only counted, as before", () => {
    const { ask, hidden } = sortInbox([r("oc", { class: "purchase_order", cases: [CASE] })]);
    expect(ask).toEqual([]);
    expect(hidden).toEqual({ "en un caso": 1 });
  });

  it("deduplicates by purchase-order number when po_refs read the same OC on two threads (goal 3)", () => {
    // Two emails (different threads) that are the same OC: only the newest is asked about.
    const { ask, hidden } = sortInbox([
      r("oc-new", { class: "purchase_order", cases: [CASE], sent_at: "2026-10-10T10:00:00Z",
                    purchase_order_numbers: ["4500000123"] }),
      r("oc-old", { class: "purchase_order", cases: [CASE], sent_at: "2026-10-09T10:00:00Z",
                    thread_id: "t-oc-old", purchase_order_numbers: ["4500000123"] }),
    ], [card()]);
    expect(ask.map((x) => x.assertion_id)).toEqual(["oc-new"]);
    expect(hidden).toEqual({ "mismo hilo": 1 });
  });
});

describe("wonProposal — po_quote_numbers filter (goal 2)", () => {
  const byId = (c: OpportunityCardData) => new Map([[c.opportunity_id, c]]);
  const po = { class: "purchase_order" as const, intent: null, cases: [CASE] };

  it("with no po_quote_numbers, proposes all winnable revisions as before", () => {
    expect(wonProposal({ ...po, po_quote_numbers: [] }, byId(card()))?.revisions.length).toBe(1);
    expect(wonProposal({ ...po, po_quote_numbers: undefined }, byId(card()))?.revisions.length).toBe(1);
  });

  it("with po_quote_numbers, keeps only revisions whose quote_number matches", () => {
    const twoRevisions = card({}, [revision(1), revision(2)]);
    // revision() uses quote_number "01253-26"; token "CN01253" matches by correlative (year=0)
    expect(wonProposal({ ...po, po_quote_numbers: ["CN01253"] }, byId(twoRevisions))?.revisions.length).toBe(2);
    // Token "01253-26" matches by (year=26, correlative=1253)
    expect(wonProposal({ ...po, po_quote_numbers: ["01253-26"] }, byId(twoRevisions))?.revisions.length).toBe(2);
  });

  it("returns null when po_quote_numbers is set but no revision matches (wrong quote)", () => {
    // The OC PDF says CN09876 but the case's quote is 01253-26 — no match → stay for a person
    expect(wonProposal({ ...po, po_quote_numbers: ["CN09876"] }, byId(card()))).toBeNull();
  });
});
