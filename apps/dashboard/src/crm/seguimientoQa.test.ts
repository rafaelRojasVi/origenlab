import { describe, expect, it } from "vitest";
import type { ProfileCard } from "../api/authClient";
import { OperatorApiError } from "../api/operatorClient";
import { bulkResultText, runBulkFollowUp } from "./bulkAssign";
import type { SetCaseFollowUpBody } from "./caseCommands";
import type { OpportunityCardData } from "./crmTypes";
import { nextContact } from "./nextContact";
import { compactClp } from "./money";
import { whatsAppUrl } from "./drawer/PersonPhones";
import { rowNotes, waHref } from "./pages/SegParts";
import type { SegRow } from "./seguimiento";
import { bulkBodies, dueForCall, foldText, keepOrder } from "./seguimientoView";

// Every value below is invented; the repository is public.
const NOW = new Date("2026-11-18T15:00:00Z");
const PROFILES: ProfileCard[] = [
  { id: "op-tati", displayName: "Tati", roleLabel: "", key: "tati" },
  { id: "op-karla", displayName: "Karla", roleLabel: "", key: "karla" },
];

function row(id: string, over: Partial<SegRow> = {}, card: Partial<OpportunityCardData> = {}): SegRow {
  const c = { opportunity_id: id, version: 2, follow_up: { operator_id: null, operator_name: null, channel: null }, open_tasks: [], quotes: [], latest_revision: null, ...card } as OpportunityCardData;
  return {
    card: c,
    // As `seguimientoRows` computes it: the row's one próximo contacto.
    next: nextContact(c, NOW),
    ownerId: null,
    suggestedOwnerId: "op-karla",
    isSuggested: true,
    channel: "correo",
    callsToday: 0,
    late: false,
    ...over,
  } as SegRow;
}

describe("keepOrder", () => {
  it("keeps the order last shown and puts new rows after", () => {
    const rows = [row("a"), row("b"), row("c"), row("d")];
    expect(keepOrder(rows, ["c", "a", "b"]).map((r) => r.card.opportunity_id)).toEqual(["c", "a", "b", "d"]);
    expect(keepOrder(rows, []).map((r) => r.card.opportunity_id)).toEqual(["a", "b", "c", "d"]);
    expect(keepOrder([row("b")], ["a", "b"]).map((r) => r.card.opportunity_id)).toEqual(["b"]);
  });
});

describe("dueForCall", () => {
  it("is due today or late, and not called today", () => {
    const today = { open_tasks: [{ task_id: "t", title: "Seguimiento de X", due_at: "2026-11-18T12:00:00Z", version: 1, owner: null }] };
    const later = { open_tasks: [{ task_id: "t", title: "Seguimiento de X", due_at: "2026-11-25T12:00:00Z", version: 1, owner: null }] };
    expect(dueForCall(row("a", {}, today), NOW)).toBe(true);
    expect(dueForCall(row("a", { callsToday: 1 }, today), NOW)).toBe(false);
    expect(dueForCall(row("a", { late: true }, later), NOW)).toBe(false);
    expect(dueForCall(row("a", { late: true }), NOW)).toBe(true);
    expect(dueForCall(row("a"), NOW)).toBe(false);
  });
});

describe("bulkBodies", () => {
  const confirmed = row("b", { ownerId: "op-karla", isSuggested: false, channel: "llamada" }, { follow_up: { operator_id: "op-karla", operator_name: "Karla", channel: "llamada" } });
  it("«Confirmar sugerido» sends each suggestion with its channel and skips confirmed cases", () => {
    expect(bulkBodies([row("a"), confirmed], { kind: "confirm" }, PROFILES)).toEqual({
      bodies: [{ opportunity_id: "a", opportunity_version: 2, follow_up_operator_id: "op-karla", channel: "correo" }],
      skipped: 1,
    });
  });
  it("«Asignar a…» moves the case, keeping a channel the new person may use", () => {
    expect(bulkBodies([confirmed], { kind: "owner", ownerId: "op-tati" }, PROFILES).bodies).toEqual([
      { opportunity_id: "b", opportunity_version: 2, follow_up_operator_id: "op-tati", channel: "llamada" },
    ]);
  });
  it("«Canal…» WhatsApp only for Tati's cases", () => {
    const tati = row("t", { ownerId: "op-tati", isSuggested: false }, { follow_up: { operator_id: "op-tati", operator_name: "Tati", channel: "correo" } });
    expect(bulkBodies([tati, confirmed], { kind: "channel", channel: "whatsapp" }, PROFILES)).toEqual({
      bodies: [{ opportunity_id: "t", opportunity_version: 2, follow_up_operator_id: "op-tati", channel: "whatsapp" }],
      skipped: 1,
    });
  });
});

describe("runBulkFollowUp", () => {
  it("runs one by one, each with its own key, counts failures and flags a 409", async () => {
    const seen: string[] = [];
    const progress: number[] = [];
    const bodies = ["a", "b", "c"].map((id) => ({ opportunity_id: id }) as SetCaseFollowUpBody);
    const result = await runBulkFollowUp(
      bodies,
      (done) => progress.push(done),
      async (body, key) => {
        seen.push(key);
        if (body.opportunity_id === "b") throw new OperatorApiError("conflict", 409);
      },
    );
    expect(result).toEqual({ ok: 2, failed: 1, conflict: true });
    expect(new Set(seen).size).toBe(3);
    expect(progress).toEqual([1, 2, 3]);
    expect(bulkResultText(result)).toBe("2 asignados, 1 falló");
    expect(bulkResultText({ ok: 12, failed: 0, conflict: false }, 2)).toBe("12 asignados, 2 sin cambio");
  });
});

describe("small helpers", () => {
  it("folds accents and case", () => {
    expect(foldText("Universidad CATÓLICA Ficticia")).toBe("universidad catolica ficticia");
    expect(foldText("Ñuñoa")).toBe("nunoa");
  });
  it("wa.me needs international digits", () => {
    expect(waHref("+56 9 1234 5678")).toBe("https://wa.me/56912345678");
    expect(waHref("9 1234 5678")).toBe("https://wa.me/56912345678");
    expect(waHref("0056 9 1234 5678")).toBe("https://wa.me/56912345678");
    expect(waHref("09 1234 5678")).toBe("https://wa.me/56912345678");
  });
  it("wa.me only for a Chilean mobile: never a landline (QA 2026-11-19)", () => {
    expect(waHref("+56 2 2123 4567")).toBeNull();
    expect(waHref("+56 41 212 3456")).toBeNull();
    expect(waHref("22 123 4567")).toBeNull();
    expect(waHref("+56 9 1234 567")).toBeNull();
    expect(whatsAppUrl("+56 2 2123 4567")).toBeNull();
    expect(whatsAppUrl("+56 9 1234 5678")).toBe("https://wa.me/56912345678");
    expect(whatsAppUrl("+54 9 11 1234 5678")).toBe("https://wa.me/5491112345678");
  });
  it("compact pesos for a KPI tile", () => {
    expect(compactClp(466_297_700)).toBe("$466 M");
    expect(compactClp(4_250_000)).toBe("$4,3 M");
    expect(compactClp(850_000)).toBe("$850.000");
  });
  it("a call's default body is just its outcome", () => {
    const notes = rowNotes(row("a", {}, { recent_notes: [{ at: "2026-11-18T13:00:00Z", body: "Llamada: No contestó", kind: "llamada", outcome: "no_contesto", author: "Karla" }] }));
    expect(notes).toEqual([{ at: "2026-11-18T13:00:00Z", head: "No contestó", text: null, author: "Karla" }]);
  });
});
