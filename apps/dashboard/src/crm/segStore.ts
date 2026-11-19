/**
 * «Seguimiento»'s screen state that must outlive the list under it: which «Llamé» form is open
 * (only one at a time), what was typed in each, which «⋯» menu or notes popover is open, and the
 * search. The list re-mounts its rows on a refetch, a receipt, a re-sort, an Agenda regroup or a
 * board move — and the whole dashboard re-mounts when the session is re-read — so this lives in
 * module memory, keyed by the case's `opportunity_id`.
 *
 * Memory only, never browser storage (a note can carry client details), and per signed-in scope
 * (`useMemoryScope`): another operator or profile in the same tab never sees these drafts.
 */
import { useCallback, useMemo, useSyncExternalStore } from "react";
import { useMemoryScope } from "./useResource";

export type NextKey = "later" | "tomorrow" | "3d" | "week" | "date";
export type WhenKey = "now" | "today" | "yesterday" | "other";

export interface CallDraft {
  outcome: string;
  next: NextKey;
  /** The day for «Otro día» as the next contact (YYYY-MM-DD). */
  nextDay: string;
  /** The person picked «Próximo contacto» by hand: an outcome no longer changes it. */
  nextTouched: boolean;
  text: string;
  when: WhenKey;
  time: string;
  otherDay: string;
  /** ISO instant the form first opened (its «today»). */
  openedAt: string;
  /** One set of keys per distinct request (see `CallForm`). */
  keys: { sig: string; at: string; note: string; task: string; cancel: string };
}

export interface SegUi {
  callOpen: string | null;
  menuOpen: string | null;
  drafts: Readonly<Record<string, CallDraft>>;
  q: string;
}

const EMPTY: SegUi = { callOpen: null, menuOpen: null, drafts: {}, q: "" };
const byScope = new Map<string, SegUi>();
const listeners = new Set<() => void>();

const keyOf = (scope: string | null) => scope ?? "";

export function readSegUi(scope: string | null): SegUi {
  return byScope.get(keyOf(scope)) ?? EMPTY;
}

function write(scope: string | null, change: (s: SegUi) => SegUi) {
  byScope.set(keyOf(scope), change(readSegUi(scope)));
  listeners.forEach((l) => l());
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => listeners.delete(l);
}

/** Tests: forget everything. */
export function resetSegUi(): void {
  byScope.clear();
  listeners.forEach((l) => l());
}

export interface SegUiActions {
  /** Open one case's «Llamé» (closing any other), or close it with null. */
  openCall: (id: string | null) => void;
  /** Open one case's menu or popover (by a key such as `menu:<id>`), or close it with null. */
  openMenu: (key: string | null) => void;
  setDraft: (id: string, draft: CallDraft) => void;
  /** Forget a case's draft (saved or cancelled). */
  dropDraft: (id: string) => void;
  setQ: (q: string) => void;
}

export function segUiActions(scope: string | null): SegUiActions {
  return {
    openCall: (id) => write(scope, (s) => ({ ...s, callOpen: id })),
    openMenu: (key) => write(scope, (s) => ({ ...s, menuOpen: key })),
    setDraft: (id, draft) => write(scope, (s) => ({ ...s, drafts: { ...s.drafts, [id]: draft } })),
    dropDraft: (id) =>
      write(scope, (s) => {
        if (!(id in s.drafts)) return s;
        const drafts = { ...s.drafts };
        delete drafts[id];
        return { ...s, drafts };
      }),
    setQ: (q) => write(scope, (s) => ({ ...s, q })),
  };
}

/** The state and its actions for the signed-in scope. */
export function useSegUi(): [SegUi, SegUiActions] {
  const scope = useMemoryScope();
  const get = useCallback(() => readSegUi(scope), [scope]);
  const state = useSyncExternalStore(subscribe, get, get);
  // Stable per scope: the actions close over nothing else.
  const actions = useMemo(() => segUiActions(scope), [scope]);
  return [state, actions];
}
