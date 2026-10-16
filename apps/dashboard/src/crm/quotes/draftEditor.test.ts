import { describe, expect, it } from "vitest";
import type { IndexItem } from "../catalog/catalogSearch";
import {
  addCatalogItem,
  addFreeLine,
  computedLineFor,
  draftProblems,
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
      uid: expect.any(String),
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

  it("sends cleared optional numbers and choices as null, and such a draft passes the shape check", () => {
    const d = updateItem(draftFixture(), 0, {
      unit_cost: " ",
      cost_currency: "" as never,
      margin_pct: "",
      typed_unit_price: "",
      item_type: "" as never,
      product_id: "",
      supplier_organization_id: "",
      cost_observation_id: "",
    });
    expect(draftProblems(d)).toEqual([]);
    expect(sendableDraft(d).items[0]).toMatchObject({
      unit_cost: null,
      cost_currency: null,
      margin_pct: null,
      typed_unit_price: null,
      item_type: null,
      product_id: null,
      supplier_organization_id: null,
      cost_observation_id: null,
      quantity: "1",
    });
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
    s = draftReducer(s, { type: "previewDone", seq: 2, draftSeq: 0, computed: computedFixture("222") });
    s = draftReducer(s, { type: "previewDone", seq: 1, draftSeq: 0, computed: computedFixture("111") });
    expect(s.computed?.totals.net).toBe("222");
  });

  it("a save replaces version and computed when nothing changed meanwhile", () => {
    let s = draftReducer(start(), { type: "edit", fn: (d) => d });
    s = draftReducer(s, { type: "saveStarted" });
    expect(s.saveState).toBe("saving");
    s = draftReducer(s, { type: "saveDone", version: 4, computed: computedFixture("444"), at: 1000 });
    expect(s).toMatchObject({ version: 4, saveState: "saved", lastSavedAt: 1000, computedSeq: 1 });
    expect(s.computed?.totals.net).toBe("444");
  });

  it("a save answered after a newer edit keeps the draft dirty; its prices still beat older ones", () => {
    let s = draftReducer(start(), { type: "edit", fn: (d) => d });
    s = draftReducer(s, { type: "saveStarted" });
    s = draftReducer(s, { type: "edit", fn: (d) => updateItem(d, 0, { quantity: "5" }) });
    s = draftReducer(s, { type: "saveDone", version: 4, computed: computedFixture("444"), at: 1000 });
    expect(s).toMatchObject({ version: 4, saveState: "dirty", computedSeq: 1 });
    expect(s.computed?.totals.net).toBe("444");
    expect(s.draft.items[0].quantity).toBe("5");
  });

  it("a save of an older draft does not replace the prices of a newer one", () => {
    let s = draftReducer(start(), { type: "edit", fn: (d) => d });
    s = draftReducer(s, { type: "saveStarted" });
    s = draftReducer(s, { type: "edit", fn: (d) => updateItem(d, 0, { quantity: "5" }) });
    s = draftReducer(s, { type: "previewStarted", seq: 1 });
    s = draftReducer(s, { type: "previewDone", seq: 1, draftSeq: 2, computed: computedFixture("555") });
    s = draftReducer(s, { type: "saveDone", version: 4, computed: computedFixture("444"), at: 1000 });
    expect(s.computed?.totals.net).toBe("555");
  });

  it("a preview of an older draft does not replace the prices of a newer save", () => {
    let s = draftReducer(start(), { type: "edit", fn: (d) => d });
    s = draftReducer(s, { type: "previewStarted", seq: 1 });
    s = draftReducer(s, { type: "edit", fn: (d) => d });
    s = draftReducer(s, { type: "saveStarted" });
    s = draftReducer(s, { type: "saveDone", version: 4, computed: computedFixture("444"), at: 1000 });
    s = draftReducer(s, { type: "previewDone", seq: 1, draftSeq: 1, computed: computedFixture("111") });
    expect(s.computed?.totals.net).toBe("444");
    expect(s.saveState).toBe("saved");
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
    const fresh = initialDraftState(workspaceFixture({ version: 7 }));
    s = draftReducer(s, { type: "reset", state: fresh });
    expect(s).toMatchObject({ version: 7, saveState: "idle", error: null });
    expect(s.draft.items).toHaveLength(2);
  });

  it("reset takes the state it is given: the reducer makes no new uids", () => {
    const fresh = initialDraftState(workspaceFixture({ version: 7 }));
    const s = draftReducer(start(), { type: "reset", state: fresh });
    expect(s).toBe(fresh);
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

describe("draftProblems", () => {
  const fields = (d: QuoteDraft) => draftProblems(d).map((p) => [p.field, p.index ?? null]);

  it("finds nothing wrong in a complete draft", () => {
    expect(draftProblems(draftFixture())).toEqual([]);
  });

  it("asks for a line's description, naming the line", () => {
    const problems = draftProblems(updateItem(addFreeLine(draftFixture()), 2, { description: "   " }));
    expect(problems).toEqual([{ index: 2, field: "items.description", message_es: "Escribe la descripción de la línea 3." }]);
  });

  it("checks the quantity: present, a number, above zero, at most 100.000, six decimals", () => {
    const at = (quantity: string) => draftProblems(updateItem(draftFixture(), 0, { quantity }))[0]?.message_es;
    expect(at("")).toMatch(/cantidad/i);
    expect(at("1,5")).toMatch(/número/);
    expect(at("0")).toBe("La cantidad debe ser mayor que cero.");
    expect(at("100001")).toMatch(/100\.000/);
    expect(at("1.1234567")).toBe("Máximo 6 decimales.");
    expect(at("1.5000000")).toBeUndefined(); // trailing zeros do not count, as on the API
    expect(at("2.5")).toBeUndefined();
  });

  it("refuses more whole digits than the column holds: 12 for (18,6), 10 for the FX rate (18,8)", () => {
    const qty = (quantity: string) => draftProblems(updateItem(draftFixture(), 0, { quantity }));
    const cost = (unit_cost: string) => draftProblems(updateItem(draftFixture(), 0, { unit_cost, cost_currency: "EUR" }));
    expect(cost("999999999999")).toEqual([]);
    expect(cost("1000000000000")).toEqual([{ index: 0, field: "items.unit_cost", message_es: "Número demasiado grande." }]);
    expect(cost("1000000000000.5").map((p) => p.message_es)).toEqual(["Número demasiado grande."]);
    expect(qty("0.5")).toEqual([]);
    const fx = (rate: string) => draftProblems(setFxOverride(draftFixture(), "USD", rate, "tasa del día"));
    expect(fx("9999999999.5")).toEqual([]);
    expect(fx("10000000000").map((p) => p.message_es)).toEqual(["Número demasiado grande."]);
  });

  it("checks cost, margin and typed price the way the API does", () => {
    expect(fields(updateItem(draftFixture(), 0, { unit_cost: "10" }))).toEqual([["items.unit_cost", 0]]);
    expect(fields(updateItem(draftFixture(), 0, { unit_cost: "-1", cost_currency: "EUR" }))).toEqual([["items.unit_cost", 0]]);
    expect(fields(updateItem(draftFixture(), 0, { margin_mode: "none", typed_unit_price: null }))).toEqual([["items.typed_unit_price", 0]]);
    expect(fields(updateItem(draftFixture(), 0, { margin_mode: "margin", margin_pct: "1" }))).toEqual([["items.margin_pct", 0]]);
    expect(fields(updateItem(draftFixture(), 0, { margin_pct: "11" }))).toEqual([["items.margin_pct", 0]]);
    expect(fields(updateItem(draftFixture(), 0, { margin_mode: "none", typed_unit_price: "1000.5" }))).toEqual([]);
  });

  it("asks for an expense's name and a valid amount", () => {
    const d = { ...draftFixture(), manual_expenses: [{ label: " ", amount: "10", currency: "CLP" as const }, { label: "Flete", amount: "x", currency: "USD" as const }] };
    expect(draftProblems(d)).toEqual([
      { index: 0, field: "manual_expenses.label", message_es: "Escribe el nombre del gasto." },
      expect.objectContaining({ index: 1, field: "manual_expenses.amount" }),
    ]);
  });

  it("asks why the exchange rate changes, and for a rate above zero with at most 8 decimals", () => {
    expect(draftProblems(setFxOverride(draftFixture(), "EUR", "1000", "ab"))).toEqual([
      { index: 0, field: "fx_overrides.reason", message_es: "Indica por qué cambias el tipo de cambio (mínimo 3 letras)." },
    ]);
    expect(fields(setFxOverride(draftFixture(), "EUR", "0", "tasa del día"))).toEqual([["fx_overrides.clp_per_unit", 0]]);
    expect(fields(setFxOverride(draftFixture(), "EUR", "1000.123456789", "tasa del día"))).toEqual([["fx_overrides.clp_per_unit", 0]]);
    expect(fields(setFxOverride(draftFixture(), "EUR", "1000.12345678", "tasa del día"))).toEqual([]);
  });

  it("checks validity days, extra terms and the typed recipient address", () => {
    expect(fields(setTerm(draftFixture(), "validity_days", 0))).toEqual([["terms.validity_days", null]]);
    expect(fields(setTerm(draftFixture(), "validity_days", 400))).toEqual([["terms.validity_days", null]]);
    const extra = { ...draftFixture(), terms: { terms_extra: [{ label_es: "", text_es: "algo" }, { label_es: "Otra", text_es: " " }] } };
    expect(fields(extra)).toEqual([["terms_extra.label_es", 0], ["terms_extra.text_es", 1]]);
    expect(fields(setRecipient(draftFixture(), { email: "no-es-correo" }))).toEqual([["recipient.email", null]]);
    expect(fields(setRecipient(draftFixture(), { email: "persona@example.com" }))).toEqual([]);
  });
});

describe("line identity (client-only uid)", () => {
  const uids = (d: QuoteDraft) => d.items.map((i) => i.uid);

  it("gives every loaded line a uid, and the workspace's computed the same keys", () => {
    const s = initialDraftState(workspaceFixture());
    expect(uids(s.draft).every((u) => typeof u === "string" && u.length > 0)).toBe(true);
    expect(new Set(uids(s.draft)).size).toBe(2);
    expect(s.computedKeys).toEqual(uids(s.draft));
  });

  it("new lines get a fresh uid; an edit keeps it; the API never sees it", () => {
    const base = initialDraftState(workspaceFixture()).draft;
    const added = addFreeLine(addCatalogItem(base, hit()));
    expect(new Set(uids(added)).size).toBe(4);
    expect(updateItem(added, 0, { quantity: "4" }).items[0].uid).toBe(added.items[0].uid);
    const flagged = updateItem(added, 0, { input_problems: { quantity: "x" } });
    const sent = sendableDraft(flagged);
    for (const item of sent.items) {
      expect("uid" in item).toBe(false);
      expect("input_problems" in item).toBe(false);
    }
  });

  it("keeps the uids of the draft each preview and save priced", () => {
    let s = initialDraftState(workspaceFixture());
    s = draftReducer(s, { type: "edit", fn: (d) => removeItem(d, 0) });
    s = draftReducer(s, { type: "previewStarted", seq: 1 });
    s = draftReducer(s, { type: "previewDone", seq: 1, draftSeq: 1, computed: computedFixture("1"), keys: ["k-2"] });
    expect(s.computedKeys).toEqual(["k-2"]);
    s = draftReducer(s, { type: "edit", fn: (d) => d });
    s = draftReducer(s, { type: "saveStarted", keys: ["k-9"] });
    s = draftReducer(s, { type: "saveDone", version: 9, computed: computedFixture("2"), at: 1 });
    expect(s.computedKeys).toEqual(["k-9"]);
  });

  it("finds a row's priced line by its uid, not its position", () => {
    const priced = {
      ...computedFixture(),
      lines: [
        { line_no: 1, unit_price: "100" },
        { line_no: 2, unit_price: "200" },
      ] as unknown as ReturnType<typeof computedFixture>["lines"],
    };
    expect(computedLineFor(priced, ["a", "b"], "b")?.unit_price).toBe("200");
    expect(computedLineFor(priced, ["a", "b"], "c")).toBeNull();
    expect(computedLineFor(null, ["a"], "a")).toBeNull();
    expect(computedLineFor(priced, ["a", "b"], undefined)).toBeNull();
  });

  it("reports what the operator typed that cannot be read as the quantity's problem", () => {
    const d = updateItem(draftFixture(), 1, { quantity: "1.000", input_problems: { quantity: "Usa coma para decimales (1,5) — ¿quisiste decir 1000?" } });
    expect(draftProblems(d)).toEqual([
      { index: 1, field: "items.quantity", message_es: "Usa coma para decimales (1,5) — ¿quisiste decir 1000?" },
    ]);
  });
});
