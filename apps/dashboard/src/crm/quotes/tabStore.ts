/**
 * The Cotizador module's open quotes (R4): up to eight tabs after «Todas», kept on this computer
 * for each operator (`cotizador:tabs:<operator_id>`) until they are closed.
 *
 * - `open(id, meta)`: a quote already open is only activated — the same quote is never open twice.
 *   A ninth closes the oldest tab with nothing pending; when every tab holds a pending save, the
 *   page is asked first (`ask.evict`), and the oldest then closes like any other (below).
 * - `close(id)`: a pane with a pending save sends it first (`tabEditors`); when that fails the page
 *   is asked (`ask.discard`: «No se pudo guardar. ¿Cerrar igual?») and the tab stays unless told yes.
 * - `active` null is «Todas».
 *
 * A tab's meta (CN label, client, state) is what the pane said the last time its workspace loaded;
 * a restored tab shows it until its pane opens again and says it afresh. Stored values are checked
 * on the way in: an id that is not a UUID, a meta of the wrong shape or a ninth tab is dropped.
 */
import { useCallback, useRef, useState } from "react";
import type { QuoteWorkspace } from "./quoteApi";
import { quoteLabel } from "./quoteLabel";
import { markDiscarded, tabEditor } from "./tabEditors";

export type TabStatus = "draft" | "approved" | "sent" | "void";

export interface TabMeta {
  /** «CN00042A». */
  label: string;
  client: string | null;
  status: TabStatus;
}

export interface QuoteTab {
  id: string;
  /** Null until the pane has loaded its workspace once. */
  meta: TabMeta | null;
}

export interface TabsState {
  tabs: QuoteTab[];
  /** The open quote shown, or null for «Todas». */
  active: string | null;
}

export const MAX_TABS = 8;
export const EMPTY_TABS: TabsState = { tabs: [], active: null };

const STATUSES = new Set<string>(["draft", "approved", "sent", "void"]);
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** A revision's status as a tab's dot: «En revisión» is still a draft. */
export function tabStatus(status: string | null | undefined): TabStatus {
  if (!status || status === "in_review") return "draft";
  return STATUSES.has(status) ? (status as TabStatus) : "void";
}

/**
 * What a pane says about its quote, from its workspace: the label of the revision shown, whom it
 * is for (the institution, else the chosen recipient, else the case's title) and its state.
 */
export function tabMetaOf(ws: QuoteWorkspace): TabMeta {
  const cur = ws.current;
  const last = ws.revisions[ws.revisions.length - 1];
  const recipientId = cur?.draft?.recipient_participant_id ?? cur?.recipient_participant_id ?? null;
  const recipient = recipientId ? ws.case.participants.find((p) => p.id === recipientId)?.name : null;
  return {
    label: quoteLabel({ label: cur?.label ?? last?.label, quote_number: ws.quote.quote_number, revision_no: cur?.revision_no ?? last?.revision_no }),
    client: ws.case.institution?.name || recipient || ws.case.title || null,
    status: tabStatus(cur?.status ?? last?.status),
  };
}

/* ── pure transitions ─────────────────────────────────────────────────────── */

export function openTab(s: TabsState, id: string, meta?: TabMeta | null): TabsState {
  if (s.tabs.some((t) => t.id === id)) {
    const merged = meta ? setTabMeta(s, id, meta) : s;
    return merged.active === id ? merged : { ...merged, active: id };
  }
  return { tabs: [...s.tabs, { id, meta: meta ?? null }], active: id };
}

export function closeTab(s: TabsState, id: string): TabsState {
  const at = s.tabs.findIndex((t) => t.id === id);
  if (at < 0) return s;
  const tabs = s.tabs.filter((t) => t.id !== id);
  if (s.active !== id) return { tabs, active: s.active };
  return { tabs, active: (tabs[at] ?? tabs[at - 1])?.id ?? null };
}

export function activateTab(s: TabsState, id: string | null): TabsState {
  if (id !== null && !s.tabs.some((t) => t.id === id)) return s;
  return s.active === id ? s : { ...s, active: id };
}

const sameMeta = (a: TabMeta | null, b: TabMeta) => !!a && a.label === b.label && a.client === b.client && a.status === b.status;

export function setTabMeta(s: TabsState, id: string, meta: TabMeta): TabsState {
  const tab = s.tabs.find((t) => t.id === id);
  if (!tab || sameMeta(tab.meta, meta)) return s;
  return { ...s, tabs: s.tabs.map((t) => (t.id === id ? { id, meta: { ...meta } } : t)) };
}

/* ── persistence ──────────────────────────────────────────────────────────── */

export function tabsStorageKey(operatorId: string): string {
  return `cotizador:tabs:${operatorId}`;
}

function readMeta(v: unknown): TabMeta | null {
  if (!v || typeof v !== "object") return null;
  const m = v as Record<string, unknown>;
  if (typeof m.label !== "string" || !m.label || typeof m.status !== "string" || !STATUSES.has(m.status)) return null;
  if (m.client !== null && typeof m.client !== "string") return null;
  return { label: m.label, client: m.client, status: m.status as TabStatus };
}

export function loadTabs(operatorId: string): TabsState {
  try {
    const raw = window.localStorage.getItem(tabsStorageKey(operatorId));
    if (!raw) return EMPTY_TABS;
    const data = JSON.parse(raw) as { tabs?: unknown; active?: unknown };
    const seen = new Set<string>();
    const tabs: QuoteTab[] = [];
    for (const t of Array.isArray(data.tabs) ? data.tabs : []) {
      const tid = t && typeof t === "object" ? (t as { id?: unknown }).id : null;
      if (typeof tid !== "string" || !UUID.test(tid)) continue;
      const lower = tid.toLowerCase();
      if (seen.has(lower)) continue;
      seen.add(lower);
      tabs.push({ id: lower, meta: readMeta((t as { meta?: unknown }).meta) });
      if (tabs.length === MAX_TABS) break;
    }
    const active = typeof data.active === "string" && seen.has(data.active.toLowerCase()) ? data.active.toLowerCase() : null;
    return { tabs, active };
  } catch {
    return EMPTY_TABS;
  }
}

export function saveTabs(operatorId: string, s: TabsState): void {
  try {
    window.localStorage.setItem(tabsStorageKey(operatorId), JSON.stringify({ v: 1, tabs: s.tabs, active: s.active }));
  } catch {
    /* the tabs just won't be remembered */
  }
}

/* ── the hook ─────────────────────────────────────────────────────────────── */

export interface TabsAsk {
  /** A pane's pending save could not be sent: close it anyway? */
  discard: (tab: QuoteTab) => Promise<boolean>;
  /** Eight tabs, all with a pending save: close the oldest (`tab`) to open another? */
  evict: (tab: QuoteTab) => Promise<boolean>;
}

export interface QuoteTabs {
  state: TabsState;
  /** The state right now, between renders (after an `open` or `close` resolved). */
  peek: () => TabsState;
  /**
   * Opens (or activates) `id`; false when it was not opened (the operator kept the tabs, or
   * `stillWanted` said no once the operator had been asked — they had moved on meanwhile).
   */
  open: (id: string, meta?: TabMeta | null, opts?: { stillWanted?: () => boolean }) => Promise<boolean>;
  /** Closes `id` after its pending save; false when the operator kept it. */
  close: (id: string) => Promise<boolean>;
  activate: (id: string | null) => void;
  setMeta: (id: string, meta: TabMeta) => void;
}

const pendingSave = (id: string) => tabEditor(id)?.hasPendingSave() ?? false;

/**
 * The state a page opens with: the stored tabs, with `initial` (the address's quote) shown when it
 * is open or there is room for it; else «Todas» until `open` makes room, so no pane is read for
 * nothing on the way.
 */
export function initialTabs(operatorId: string, initial: string | null | undefined): TabsState {
  const stored = loadTabs(operatorId);
  if (!initial) return stored;
  const key = initial.toLowerCase();
  if (stored.tabs.some((t) => t.id === key) || stored.tabs.length < MAX_TABS) {
    const next = openTab(stored, key);
    saveTabs(operatorId, next);
    return next;
  }
  return { ...stored, active: null };
}

export function useQuoteTabs(operatorId: string, ask: TabsAsk, initial?: string | null): QuoteTabs {
  const [state, setState] = useState<TabsState>(() => initialTabs(operatorId, initial));
  // The async steps read and write this, not `state`: it changes synchronously with each step.
  const ref = useRef(state);
  const askRef = useRef(ask);
  askRef.current = ask;
  const closing = useRef(new Set<string>());

  const commit = useCallback(
    (next: TabsState) => {
      if (next === ref.current) return;
      ref.current = next;
      setState(next);
      saveTabs(operatorId, next);
    },
    [operatorId],
  );

  const close = useCallback(
    async (id: string): Promise<boolean> => {
      const tab = ref.current.tabs.find((t) => t.id === id);
      if (!tab) return true;
      if (closing.current.has(id)) return false;
      closing.current.add(id);
      try {
        const editor = tabEditor(id);
        if (editor?.hasPendingSave()) {
          const saved = await editor.flush().catch(() => false);
          if (!saved) {
            if (!(await askRef.current.discard(tab))) return false;
            // Told «Cerrar igual»: the pane closing must not warn again about what was just accepted.
            markDiscarded(id);
          }
        }
        commit(closeTab(ref.current, id));
        return true;
      } finally {
        closing.current.delete(id);
      }
    },
    [commit],
  );

  const open = useCallback(
    async (id: string, meta?: TabMeta | null, opts?: { stillWanted?: () => boolean }): Promise<boolean> => {
      const key = id.toLowerCase();
      if (ref.current.tabs.some((t) => t.id === key) || ref.current.tabs.length < MAX_TABS) {
        commit(openTab(ref.current, key, meta));
        return true;
      }
      const free = ref.current.tabs.find((t) => !pendingSave(t.id));
      if (free) {
        commit(closeTab(ref.current, free.id));
      } else {
        const oldest = ref.current.tabs[0];
        if (!(await askRef.current.evict(oldest)) || !(await close(oldest.id))) return false;
      }
      // While the operator was asked, they may have gone to another tab (or left), and something
      // else may have opened tabs.
      if (opts?.stillWanted && !opts.stillWanted()) return false;
      if (!ref.current.tabs.some((t) => t.id === key) && ref.current.tabs.length >= MAX_TABS) return false;
      commit(openTab(ref.current, key, meta));
      return true;
    },
    [commit, close],
  );

  const activate = useCallback((id: string | null) => commit(activateTab(ref.current, id)), [commit]);
  const setMeta = useCallback((id: string, meta: TabMeta) => commit(setTabMeta(ref.current, id, meta)), [commit]);

  const peek = useCallback(() => ref.current, []);

  return { state, peek, open, close, activate, setMeta };
}
