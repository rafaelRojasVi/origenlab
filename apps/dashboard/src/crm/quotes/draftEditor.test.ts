import { describe, expect, it } from "vitest";
import type { IndexItem } from "../catalog/catalogSearch";
import {
  addCatalogItem,
  addFreeLine,
  draftReducer,
  initialDraftState,
  isEditable,
  removeItem,
  sendableDraft,
  setExpenseMethod,
  setFxOverride,
  setPrincipal,
  setRecipient,
  setTerm,
  updateItem,
  type DraftEditorState,
} from "./draftEditor";
import { QuoteRefusal, type QuoteDraft } from "./quoteApi";
import { computedFixture, draftFixture, workspaceFixture } from "./quoteFixtures";

const hit = (over: Partial<IndexItem> = {}): IndexItem => ({
  id: "p-9",
  model_number: "MOD-001",
  model_key: "mod001",
  name: "Balanza de prueba",
  category_es: null,
  kind: "equipment",
  manufacturer: "Fabricante de prueba",
  supplier_ids: [],
  image_id: null,
  has_datasheet: false,
  name_en: null,
  ...over,
});

describe("draft helpers", () => {
  it("adds a catalog hit as a markup line the server prices with its default", () => {
    const d = addCatalogItem(draftFixture(), hit());
    expect(d.items).toHaveLength(3);
    expect(d.items[2]).toEqual({
      product_id: "p-9",
      description: "Balanza de prueba",
      quantity: "1",
      item_type: "equipment",
      margin_mode: "markup",
      margin_pct: null,
      is_principal: false,
    });
  });

  it("falls back to the model number and drops a kind the API does not know", () => {
    const d = addCatalogItem(draftFixture(), hit({ name: "", kind: "kit" }));
    expect(d.items[2].description).toBe("MOD-001");
    expect(d.items[2].item_type).toBeNull();
  });

  it("adds a free line without a product", () => {
    const d = addFreeLine(draftFixture());
    expect(d.items[2]).toMatchObject({ product_id: null, description: "", quantity: "1", margin_mode: "markup", margin_pct: null });
  });

  it("updates and removes a line without touching the input", () => {
    const base = draftFixture();
    const updated = updateItem(base, 1, { margin_pct: "0.4" });
    expect(updated.items[1].margin_pct).toBe("0.4");
    expect(base.items[1].margin_pct).toBe("0.3");
    const removed = removeItem(base, 0);
    expect(removed.items.map((i) => i.description)).toEqual(["Servicio de prueba"]);
    expect(base.items).toHaveLength(2);
  });

  it("makes one line principal and switches the rule to manual", () => {
    const d = setPrincipal(updateItem(draftFixture(), 0, { is_principal: true }), 1);
    expect(d.principal_rule).toBe("manual");
    expect(d.items.map((i) => i.is_principal)).toEqual([false, true]);
  });

  it("copies the derived expenses into manual ones when switching to manual (B5)", () => {
    const d = setExpenseMethod(draftFixture(), "manual", computedFixture());
    expect(d.expense_method).toBe("manual");
    expect(d.manual_expenses).toEqual([
      { label: "Flete", amount: "150.5", currency: "USD" },
      { label: "Desaduanaje", amount: "45000", currency: "CLP" },
    ]);
  });

  it("keeps the manual expenses when nothing is computed, and clears them back in a derived method", () => {
    const manual = { ...draftFixture(), expense_method: "manual" as const, manual_expenses: [{ label: "Otro", amount: "10", currency: "CLP" as const }] };
    expect(setExpenseMethod(manual, "manual", null).manual_expenses).toEqual(manual.manual_expenses);
    const back = setExpenseMethod(manual, "dhl_calc", computedFixture());
    expect(back).toMatchObject({ expense_method: "dhl_calc", manual_expenses: [] });
  });

  it("sets a term, the recipient and an exchange-rate override", () => {
    let d = setTerm(draftFixture(), "validity_days", 30);
    d = setTerm(d, "payment_terms", "30 días");
    expect(d.terms).toMatchObject({ validity_days: 30, payment_terms: "30 días", terms_extra: [] });
    d = setRecipient(d, { email: "persona@example.com", name: "Persona de prueba" });
    expect(d.recipient).toEqual({ email: "persona@example.com", name: "Persona de prueba" });
    d = setFxOverride(d, "EUR", "1000.5", "tasa del proveedor");
    d = setFxOverride(d, "EUR", "1001", "tasa corregida");
    expect(d.fx_overrides).toEqual([{ currency: "EUR", clp_per_unit: "1001", reason: "tasa corregida" }]);
    d = setFxOverride(d, "EUR", null, "");
    expect(d.fx_overrides).toEqual([]);
  });

  it("sends a draft without the legacy recipient field, turning it into a pick", () => {
    const legacy = { ...draftFixture(), recipient_participant_id: "part-1" } as QuoteDraft & { recipient_participant_id: string };
    const sent = sendableDraft(legacy) as QuoteDraft & { recipient_participant_id?: unknown };
    expect(sent.recipient).toEqual({ participant_id: "part-1" });
    expect("recipient_participant_id" in sent).toBe(false);
    const both = { ...legacy, recipient: { email: "a@example.com" } };
    expect(sendableDraft(both).recipient).toEqual({ email: "a@example.com" });
    expect("recipient_participant_id" in sendableDraft(both)).toBe(false);
  });

  it("sends an empty line note as null", () => {
    const d = updateItem(draftFixture(), 0, { line_note_es: "" });
    expect(sendableDraft(d).items[0].line_note_es).toBeNull();
  });
});

describe("draftReducer", () => {
  const start = (): DraftEditorState => initialDraftState(workspaceFixture());

  it("starts from the workspace draft, version and computed", () => {
    const s = start();
    expect(s).toMatchObject({ version: 3, saveState: "idle", lastSavedAt: null, error: null, previewSeq: 0 });
    expect(s.computed?.totals.net).toBe("100000");
    expect("recipient_participant_id" in s.draft).toBe(false);
  });

  it("an edit makes the draft dirty", () => {
    const s = draftReducer(start(), { type: "edit", fn: (d) => updateItem(d, 0, { quantity: "3" }) });
    expect(s.saveState).toBe("dirty");
    expect(s.draft.items[0].quantity).toBe("3");
    expect(s.editSeq).toBe(1);
  });

  it("ignores a preview older than the latest one started", () => {
    let s = draftReducer(start(), { type: "previewStarted", seq: 1 });
    s = draftReducer(s, { type: "previewStarted", seq: 2 });
    s = draftReducer(s, { type: "previewDone", seq: 2, computed: computedFixture("222") });
    s = draftReducer(s, { type: "previewDone", seq: 1, computed: computedFixture("111") });
    expect(s.computed?.totals.net).toBe("222");
  });

  it("a save replaces version and computed when nothing changed meanwhile", () => {
    let s = draftReducer(start(), { type: "edit", fn: (d) => d });
    s = draftReducer(s, { type: "saveStarted" });
    expect(s.saveState).toBe("saving");
    s = draftReducer(s, { type: "saveDone", version: 4, computed: computedFixture("444"), at: 1000 });
    expect(s).toMatchObject({ version: 4, saveState: "saved", lastSavedAt: 1000 });
    expect(s.computed?.totals.net).toBe("444");
  });

  it("a save answered after a newer edit keeps the draft dirty and its newer computed", () => {
    let s = draftReducer(start(), { type: "edit", fn: (d) => d });
    s = draftReducer(s, { type: "saveStarted" });
    s = draftReducer(s, { type: "edit", fn: (d) => updateItem(d, 0, { quantity: "5" }) });
    s = draftReducer(s, { type: "saveDone", version: 4, computed: computedFixture("444"), at: 1000 });
    expect(s).toMatchObject({ version: 4, saveState: "dirty" });
    expect(s.computed?.totals.net).toBe("100000");
    expect(s.draft.items[0].quantity).toBe("5");
  });

  it("a 409 stale_version is a conflict; any other refusal is an error", () => {
    let s = draftReducer(start(), { type: "saveStarted" });
    s = draftReducer(s, { type: "saveFailed", error: new QuoteRefusal(409, "stale_version", "x") });
    expect(s.saveState).toBe("conflict");
    let t = draftReducer(start(), { type: "saveStarted" });
    t = draftReducer(t, { type: "saveFailed", error: new QuoteRefusal(422, "validation_failed", "bad") });
    expect(t.saveState).toBe("error");
    expect(t.error?.message).toBe("bad");
  });

  it("an edit during a conflict stays a conflict", () => {
    let s = draftReducer(start(), { type: "saveFailed", error: new QuoteRefusal(409, "stale_version", "x") });
    s = draftReducer(s, { type: "edit", fn: (d) => d });
    expect(s.saveState).toBe("conflict");
  });

  it("reset discards local edits", () => {
    let s = draftReducer(start(), { type: "edit", fn: (d) => removeItem(d, 0) });
    s = draftReducer(s, { type: "saveFailed", error: new QuoteRefusal(409, "stale_version", "x") });
    s = draftReducer(s, { type: "reset", workspace: workspaceFixture({ version: 7 }) });
    expect(s).toMatchObject({ version: 7, saveState: "idle", error: null });
    expect(s.draft.items).toHaveLength(2);
  });
});

describe("isEditable", () => {
  it("only an authored draft is editable", () => {
    expect(isEditable(workspaceFixture())).toBe(true);
    expect(isEditable(workspaceFixture({ status: "approved" }))).toBe(false);
    expect(isEditable(workspaceFixture({ origin: "imported" }))).toBe(false);
    expect(isEditable({ ...workspaceFixture(), current: null })).toBe(false);
  });
});
