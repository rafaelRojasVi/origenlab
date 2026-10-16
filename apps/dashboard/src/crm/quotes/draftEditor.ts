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
  | { type: "previewDone"; seq: number; computed: Computed }
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
 * pick when there is none, and is dropped. An empty line note is sent as none.
 */
export function sendableDraft(draft: QuoteDraft & { recipient_participant_id?: string | null }): QuoteDraft {
  const { recipient_participant_id: legacy, ...rest } = draft;
  const recipient = rest.recipient ?? (legacy ? { participant_id: legacy } : null);
  return {
    ...rest,
    recipient,
    items: rest.items.map((item) => (item.line_note_es === "" ? { ...item, line_note_es: null } : item)),
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
      if (action.seq < state.previewSeq) return state;
      return { ...state, computed: action.computed, previewError: null };
    case "previewFailed":
      if (action.seq < state.previewSeq) return state;
      return { ...state, previewError: action.error };
    case "saveStarted":
      return { ...state, saveState: "saving", savingSeq: state.editSeq, error: null };
    case "saveDone": {
      // The response priced the draft as it was sent. If the operator edited since, a newer
      // preview (or the queued save) answers for the current draft, so keep what we have.
      const current = state.savingSeq === state.editSeq;
      return {
        ...state,
        version: action.version,
        computed: current ? action.computed : state.computed,
        saveState: current ? "saved" : "dirty",
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
