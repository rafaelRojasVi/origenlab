/**
 * The quote editor's state: the draft the operator is editing, the last `version` the server
 * gave us, and the last `computed` the server priced. Everything here is pure; the timers,
 * requests and their ordering live in `useQuoteDraft`.
 *
 * The browser never prices. These helpers only change what the operator asked for (lines,
 * expenses, terms, recipient, exchange-rate overrides); the numbers come back from the API.
 */
import type { IndexItem } from "../catalog/catalogSearch";
import type {
  Computed,
  QuoteDraft,
  QuoteDraftItem,
  QuoteDraftTerms,
  QuoteItemType,
  QuoteRecipientPick,
  QuoteRefusal,
  QuoteWorkspace,
} from "./quoteApi";

export type SaveState = "idle" | "dirty" | "saving" | "saved" | "conflict" | "error";

export interface DraftEditorState {
  draft: QuoteDraft;
  version: number;
  computed: Computed | null;
  saveState: SaveState;
  lastSavedAt: number | null;
  /** Why the last save failed: a `QuoteRefusal`, or a network error. */
  error: QuoteRefusal | Error | null;
  /** The newest preview started; an older one answering late is ignored. */
  previewSeq: number;
  /** The `editSeq` of the draft `computed` priced: a result for an older draft never replaces it. */
  computedSeq: number;
  previewError: QuoteRefusal | Error | null;
  /** Bumped by every edit, so a save answered after a newer edit does not claim the draft is saved. */
  editSeq: number;
  /** The `editSeq` the save in flight carries. */
  savingSeq: number | null;
  /** The workspace this state was built from (the last load or reload). */
  workspace: QuoteWorkspace;
}

export type DraftAction =
  | { type: "edit"; fn: (draft: QuoteDraft) => QuoteDraft }
  | { type: "previewStarted"; seq: number }
  | { type: "previewDone"; seq: number; draftSeq: number; computed: Computed }
  | { type: "previewFailed"; seq: number; error: QuoteRefusal | Error }
  | { type: "saveStarted" }
  | { type: "saveDone"; version: number; computed: Computed; at: number }
  | { type: "saveFailed"; error: QuoteRefusal | Error }
  | { type: "reset"; workspace: QuoteWorkspace };

/** Only an authored draft is edited here; approved, sent, void and imported revisions are read-only. */
export function isEditable(workspace: QuoteWorkspace): boolean {
  const cur = workspace.current;
  return !!cur && cur.origin === "authored" && cur.status === "draft" && !!cur.draft;
}

/** `409 stale_version`: someone else saved this draft since we loaded it. */
export function isConflict(error: unknown): boolean {
  const e = error as { status?: unknown; code?: unknown } | null;
  return !!e && e.status === 409 && e.code === "stale_version";
}

const EMPTY_DRAFT: QuoteDraft = {
  items: [],
  manual_expenses: [],
  expense_method: "profile",
  allocation_mode: "principal",
  principal_rule: "highest_cost_imported_equipment",
  fx_overrides: [],
  terms: { terms_extra: [] },
  recipient: null,
};

/**
 * The draft as the API takes it. The workspace still reports the older `recipient_participant_id`;
 * the API refuses a draft carrying both it and `recipient` (422), so it becomes a `recipient`
 * pick when there is none, and is dropped. A blank line note or recipient name is sent as none
 * (the API strips whitespace and refuses an empty one).
 */
export function sendableDraft(draft: QuoteDraft & { recipient_participant_id?: string | null }): QuoteDraft {
  const { recipient_participant_id: legacy, ...rest } = draft;
  let recipient = rest.recipient ?? (legacy ? { participant_id: legacy } : null);
  if (recipient && typeof recipient.name === "string" && !recipient.name.trim()) {
    const { name: _blank, ...pick } = recipient;
    recipient = pick;
  }
  return {
    ...rest,
    recipient,
    items: rest.items.map((item) =>
      typeof item.line_note_es === "string" && !item.line_note_es.trim() ? { ...item, line_note_es: null } : item,
    ),
  };
}

export function initialDraftState(workspace: QuoteWorkspace): DraftEditorState {
  const cur = workspace.current;
  return {
    draft: sendableDraft(cur?.draft ?? EMPTY_DRAFT),
    version: cur?.version ?? 0,
    computed: cur?.computed ?? null,
    saveState: "idle",
    lastSavedAt: null,
    error: null,
    previewSeq: 0,
    computedSeq: 0,
    previewError: null,
    editSeq: 0,
    savingSeq: null,
    workspace,
  };
}

export function draftReducer(state: DraftEditorState, action: DraftAction): DraftEditorState {
  switch (action.type) {
    case "edit":
      return {
        ...state,
        draft: action.fn(state.draft),
        editSeq: state.editSeq + 1,
        saveState: state.saveState === "conflict" ? "conflict" : state.saveState === "saving" ? "saving" : "dirty",
      };
    case "previewStarted":
      return action.seq > state.previewSeq ? { ...state, previewSeq: action.seq } : state;
    case "previewDone":
      if (action.seq < state.previewSeq || action.draftSeq < state.computedSeq) return state;
      return { ...state, computed: action.computed, computedSeq: action.draftSeq, previewError: null };
    case "previewFailed":
      if (action.seq < state.previewSeq) return state;
      return { ...state, previewError: action.error };
    case "saveStarted":
      return { ...state, saveState: "saving", savingSeq: state.editSeq, error: null };
    case "saveDone": {
      // The response priced the draft as it was sent: it replaces what is shown unless that
      // already prices a newer draft (a preview of a later edit). Edits since keep it dirty.
      const pricedSeq = state.savingSeq ?? state.editSeq;
      const newer = pricedSeq >= state.computedSeq;
      return {
        ...state,
        version: action.version,
        computed: newer ? action.computed : state.computed,
        computedSeq: newer ? pricedSeq : state.computedSeq,
        saveState: pricedSeq === state.editSeq ? "saved" : "dirty",
        lastSavedAt: action.at,
        savingSeq: null,
        error: null,
      };
    }
    case "saveFailed":
      return { ...state, saveState: isConflict(action.error) ? "conflict" : "error", error: action.error, savingSeq: null };
    case "reset":
      return initialDraftState(action.workspace);
  }
}

/* ── edit helpers (pure: each returns a new draft) ─────────────────────────── */

const ITEM_TYPES: readonly QuoteItemType[] = ["equipment", "accessory", "consumable", "spare_part", "service", "other"];

/** A catalog product as a new line; the server fills the markup default for its type. */
export function addCatalogItem(draft: QuoteDraft, hit: IndexItem): QuoteDraft {
  const itemType = ITEM_TYPES.find((t) => t === hit.kind) ?? null;
  const item: QuoteDraftItem = {
    product_id: hit.id,
    description: hit.name || hit.model_number,
    quantity: "1",
    item_type: itemType,
    margin_mode: "markup",
    margin_pct: null,
    is_principal: false,
  };
  return { ...draft, items: [...draft.items, item] };
}

/** A line with no catalog product; the operator types what it is. */
export function addFreeLine(draft: QuoteDraft): QuoteDraft {
  const item: QuoteDraftItem = {
    product_id: null,
    description: "",
    quantity: "1",
    item_type: null,
    margin_mode: "markup",
    margin_pct: null,
    is_principal: false,
  };
  return { ...draft, items: [...draft.items, item] };
}

export function updateItem(draft: QuoteDraft, index: number, patch: Partial<QuoteDraftItem>): QuoteDraft {
  return { ...draft, items: draft.items.map((item, i) => (i === index ? { ...item, ...patch } : item)) };
}

export function removeItem(draft: QuoteDraft, index: number): QuoteDraft {
  return { ...draft, items: draft.items.filter((_, i) => i !== index) };
}

/** The operator picks the principal line (it absorbs the import expenses): the rule becomes manual. */
export function setPrincipal(draft: QuoteDraft, index: number): QuoteDraft {
  return {
    ...draft,
    principal_rule: "manual",
    items: draft.items.map((item, i) => ({ ...item, is_principal: i === index })),
  };
}

/**
 * B5: in Perfil / DHL modes the expenses are derived and read-only. Switching to manual copies the
 * expenses the server last computed into `manual_expenses`, so the amounts the operator saw stay
 * and become editable. Back in a derived method the server computes them again, so the manual
 * copies are dropped (the API adds manual expenses on top of derived ones in every method).
 */
export function setExpenseMethod(
  draft: QuoteDraft,
  method: QuoteDraft["expense_method"],
  computed: Computed | null,
): QuoteDraft {
  if (method === "manual") {
    const manual = computed
      ? computed.expenses.map((e) => ({
          label: e.label,
          amount: e.amount,
          currency: (e.currency === "EUR" || e.currency === "USD" ? e.currency : "CLP") as "CLP" | "EUR" | "USD",
        }))
      : draft.manual_expenses;
    return { ...draft, expense_method: "manual", manual_expenses: manual };
  }
  return { ...draft, expense_method: method, manual_expenses: [] };
}

type TermKey = Exclude<keyof QuoteDraftTerms, "terms_extra">;

export function setTerm<K extends TermKey>(draft: QuoteDraft, key: K, value: QuoteDraftTerms[K]): QuoteDraft {
  return { ...draft, terms: { ...draft.terms, [key]: value } };
}

export function setRecipient(draft: QuoteDraft, pick: QuoteRecipientPick | null): QuoteDraft {
  return { ...draft, recipient: pick };
}

/** A manual exchange rate for one currency, with its reason; `rate` null goes back to the Banco Central. */
export function setFxOverride(
  draft: QuoteDraft,
  currency: "EUR" | "USD",
  rate: string | null,
  reason: string,
): QuoteDraft {
  const others = draft.fx_overrides.filter((o) => o.currency !== currency);
  return {
    ...draft,
    fx_overrides: rate === null ? others : [...others, { currency, clp_per_unit: rate, reason }],
  };
}

/* ── what the API would refuse ─────────────────────────────────────────────── */

/** Something the API would refuse in the draft's shape (422), said in Spanish. */
export interface DraftProblem {
  /** The position in the list the field belongs to (items, manual_expenses, fx_overrides, terms_extra). */
  index?: number;
  field: string;
  message_es: string;
}

const DECIMAL = /^-?(\d+\.?\d*|\.\d+)$/;

/**
 * Checks a decimal string like the API's `Decimal` fields: a plain number (point for decimals),
 * at most `places` decimals (trailing zeros do not count) and 18 digits in total. Returns the
 * problem in Spanish, or null. `min`/`max` bound the value; `minExclusive` makes `min` strict.
 */
function decimalProblem(
  raw: string | null | undefined,
  what: string,
  { places = 6, min = 0, minExclusive = false, max }: { places?: number; min?: number; minExclusive?: boolean; max?: number } = {},
): string | null {
  const text = (raw ?? "").trim();
  if (!text) return `Escribe ${what}.`;
  if (!DECIMAL.test(text)) return `${capital(what)} debe ser un número (usa punto para los decimales).`;
  const [int = "", frac = ""] = text.replace(/^-/, "").split(".");
  const decimals = frac.replace(/0+$/, "");
  if (decimals.length > places) return `Máximo ${places} decimales.`;
  if (int.replace(/^0+/, "").length + decimals.length > 18) return `${capital(what)} tiene demasiados dígitos.`;
  const value = Number(text);
  if (minExclusive ? value <= min : value < min) {
    return min === 0 && minExclusive ? `${capital(what)} debe ser mayor que cero.` : `${capital(what)} no puede ser negativo.`;
  }
  if (max !== undefined && value > max) return `${capital(what)} no puede pasar de ${max.toLocaleString("es-CL")}.`;
  return null;
}

const capital = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
const blank = (s: string | null | undefined) => !(s ?? "").trim();
const tooLong = (s: string | null | undefined, max: number) => (s ?? "").trim().length > max;
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

/**
 * The draft's shape problems, mirroring only the field constraints of the API's draft model
 * (`apps/api/src/origenlab_api/v2/quotes/draft.py`): what would come back as a 422 «Request
 * validation failed» with no Spanish word in it. Pricing problems stay the server's (warnings,
 * `computed_error`). While this list is not empty the editor neither previews nor saves.
 */
export function draftProblems(draft: QuoteDraft): DraftProblem[] {
  const out: DraftProblem[] = [];
  const add = (field: string, message_es: string | null, index?: number) => {
    if (message_es) out.push(index === undefined ? { field, message_es } : { index, field, message_es });
  };

  draft.items.forEach((item, i) => {
    const n = i + 1;
    if (blank(item.description)) add("items.description", `Escribe la descripción de la línea ${n}.`, i);
    else if (tooLong(item.description, 500)) add("items.description", `La descripción de la línea ${n} es demasiado larga (máximo 500).`, i);
    add("items.quantity", decimalProblem(item.quantity, "la cantidad", { minExclusive: true, max: 100000 }), i);
    const hasCost = !blank(item.unit_cost);
    if (hasCost !== !!item.cost_currency) add("items.unit_cost", `Indica el costo y su moneda juntos (línea ${n}).`, i);
    else if (hasCost) add("items.unit_cost", decimalProblem(item.unit_cost, "el costo"), i);
    if (!blank(item.margin_pct)) {
      const bad = decimalProblem(item.margin_pct, "el recargo");
      add("items.margin_pct", bad ?? (Number(item.margin_pct) > 10 ? `El recargo de la línea ${n} no puede pasar de 1000 %.` : null), i);
    }
    if (item.margin_mode === "margin" && (blank(item.margin_pct) || Number(item.margin_pct) >= 1)) {
      add("items.margin_pct", `El margen de la línea ${n} debe ser menor que 100 %.`, i);
    }
    if (item.margin_mode === "none" && blank(item.typed_unit_price)) {
      add("items.typed_unit_price", `Escribe el precio de la línea ${n}.`, i);
    } else if (!blank(item.typed_unit_price)) {
      add("items.typed_unit_price", decimalProblem(item.typed_unit_price, "el precio"), i);
    }
    if (tooLong(item.line_note_es, 1000)) add("items.line_note_es", `La nota de la línea ${n} es demasiado larga (máximo 1000).`, i);
  });

  draft.manual_expenses.forEach((e, i) => {
    if (blank(e.label)) add("manual_expenses.label", "Escribe el nombre del gasto.", i);
    else if (tooLong(e.label, 120)) add("manual_expenses.label", "El nombre del gasto es demasiado largo (máximo 120).", i);
    add("manual_expenses.amount", decimalProblem(e.amount, "el monto del gasto"), i);
  });

  draft.fx_overrides.forEach((o, i) => {
    add("fx_overrides.clp_per_unit", decimalProblem(o.clp_per_unit, "el tipo de cambio", { places: 8, minExclusive: true }), i);
    const reason = (o.reason ?? "").trim();
    if (reason.length < 3) add("fx_overrides.reason", "Indica por qué cambias el tipo de cambio (mínimo 3 letras).", i);
    else if (reason.length > 500) add("fx_overrides.reason", "El motivo es demasiado largo (máximo 500).", i);
  });

  const t = draft.terms;
  const days = t.validity_days;
  if (days !== null && days !== undefined && (!Number.isInteger(days) || days < 1 || days > 365)) {
    add("terms.validity_days", "La validez debe ser de 1 a 365 días.");
  }
  for (const key of ["delivery_terms", "payment_terms", "warranty_terms_es", "delivery_place_es", "installation_terms_es", "client_notes_es"] as const) {
    if (tooLong(t[key], 1000)) add(`terms.${key}`, "Este texto es demasiado largo (máximo 1000).");
  }
  t.terms_extra.forEach((x, i) => {
    if (blank(x.label_es)) add("terms_extra.label_es", "Escribe el nombre de la condición.", i);
    else if (tooLong(x.label_es, 80)) add("terms_extra.label_es", "El nombre de la condición es demasiado largo (máximo 80).", i);
    if (blank(x.text_es)) add("terms_extra.text_es", "Escribe el texto de la condición.", i);
    else if (tooLong(x.text_es, 1000)) add("terms_extra.text_es", "El texto de la condición es demasiado largo (máximo 1000).", i);
  });

  const email = draft.recipient?.email;
  if (typeof email === "string" && !EMAIL.test(email.trim())) add("recipient.email", "El correo del destinatario no es válido.");
  return out;
}
