import { useCallback, useEffect, useReducer, useRef } from "react";
import { draftReducer, initialDraftState, isConflict, isEditable, sendableDraft, type DraftEditorState } from "./draftEditor";
import {
  approveQuoteRevision,
  fetchQuoteWorkspace,
  newQuoteCommandKey,
  previewQuote,
  saveQuoteDraft,
  type QuoteDraft,
  type QuoteRefusal,
  type QuoteWorkspace,
} from "./quoteApi";

/**
 * The quote editor's engine (rulings B2/B3): local edits, a live preview and an autosave.
 *
 * - Preview: 300 ms after the last edit, `POST /v2/quotes/preview` (it writes nothing). A new
 *   preview aborts the one in flight, and a response older than the newest preview started is
 *   ignored, so a stale price never overwrites a newer one.
 * - Autosave: 1.2 s after the last edit, `save-quote-draft` with the last version the server
 *   gave us. One save is in flight at a time; edits during it queue one follow-up, sent with the
 *   version that save returned (never a 409 against ourselves). One `Idempotency-Key` per save
 *   attempt: retrying the same edits reuses it, a new edit gets a new one.
 * - Conflict: `409 stale_version` (someone else saved) stops autosave until `reload()`, which
 *   loads the workspace again and discards the local edits.
 * - Leaving: unmounting flushes a pending save; `beforeunload` warns while edits are unsaved.
 *
 * The page must `flush()` (or use `approve()`) before any command that needs the saved draft.
 * A new `workspace` prop object resets the editor to it, so pass a new one only after a flush.
 */
export const PREVIEW_DELAY_MS = 300;
export const SAVE_DELAY_MS = 1200;

export interface QuoteDraftApi {
  previewQuote: typeof previewQuote;
  saveQuoteDraft: typeof saveQuoteDraft;
  approveQuoteRevision: typeof approveQuoteRevision;
  fetchQuoteWorkspace: typeof fetchQuoteWorkspace;
}

const DEFAULT_API: QuoteDraftApi = { previewQuote, saveQuoteDraft, approveQuoteRevision, fetchQuoteWorkspace };

export interface FlushResult {
  /** True when every edit is saved (no conflict, no failed save left). */
  ok: boolean;
  /** The version to send with the next command. */
  version: number;
}

export interface QuoteDraftEditor {
  state: DraftEditorState;
  editable: boolean;
  edit: (fn: (draft: QuoteDraft) => QuoteDraft) => void;
  flush: () => Promise<FlushResult>;
  reload: () => Promise<QuoteWorkspace>;
  /** Saves pending edits first, then approves with the version that save returned. Throws the refusal. */
  approve: (key?: string) => ReturnType<typeof approveQuoteRevision>;
}

const asError = (e: unknown): QuoteRefusal | Error => (e instanceof Error ? e : new Error(String(e)));
const isAbort = (e: unknown) => e instanceof DOMException && e.name === "AbortError";

export function useQuoteDraft(workspace: QuoteWorkspace, options: { api?: Partial<QuoteDraftApi> } = {}): QuoteDraftEditor {
  const [state, dispatch] = useReducer(draftReducer, workspace, initialDraftState);
  const apiRef = useRef<QuoteDraftApi>(DEFAULT_API);
  apiRef.current = { ...DEFAULT_API, ...options.api };

  // The async engine reads these, not `state`: they change synchronously with each edit/answer.
  const workspaceRef = useRef(workspace);
  const editableRef = useRef(isEditable(workspace));
  const draftRef = useRef(state.draft);
  const versionRef = useRef(state.version);
  const editSeqRef = useRef(0);
  const savedSeqRef = useRef(0);
  const stoppedRef = useRef(false);
  const keyRef = useRef<{ seq: number; key: string } | null>(null);
  const inFlightRef = useRef<Promise<void> | null>(null);
  const queuedRef = useRef(false);
  const lastErrorRef = useRef<QuoteRefusal | Error | null>(null);
  const genRef = useRef(0);
  const previewSeqRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const previewTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearTimers = () => {
    if (previewTimer.current) clearTimeout(previewTimer.current);
    if (saveTimer.current) clearTimeout(saveTimer.current);
    previewTimer.current = saveTimer.current = null;
  };
  const unsaved = () => editableRef.current && !stoppedRef.current && editSeqRef.current !== savedSeqRef.current;

  const runSave = useCallback((): Promise<void> => {
    if (inFlightRef.current) {
      queuedRef.current = true;
      return inFlightRef.current;
    }
    const gen = genRef.current;
    const revisionId = workspaceRef.current.current?.id ?? "";
    const loop = async () => {
      for (;;) {
        queuedRef.current = false;
        if (!unsaved()) return;
        const seq = editSeqRef.current;
        const key = keyRef.current?.seq === seq ? keyRef.current.key : newQuoteCommandKey();
        keyRef.current = { seq, key };
        dispatch({ type: "saveStarted" });
        try {
          const res = await apiRef.current.saveQuoteDraft(revisionId, versionRef.current, sendableDraft(draftRef.current), key);
          if (gen !== genRef.current) return;
          versionRef.current = res.version;
          savedSeqRef.current = seq;
          lastErrorRef.current = null;
          dispatch({ type: "saveDone", version: res.version, computed: res.computed, at: Date.now() });
        } catch (e) {
          if (gen !== genRef.current) return;
          const error = asError(e);
          lastErrorRef.current = error;
          dispatch({ type: "saveFailed", error });
          if (isConflict(error)) {
            stoppedRef.current = true;
            return;
          }
        }
        if (!queuedRef.current) return;
      }
    };
    const p: Promise<void> = loop().finally(() => {
      if (inFlightRef.current === p) inFlightRef.current = null;
    });
    inFlightRef.current = p;
    return p;
  }, []);

  const runPreview = useCallback(() => {
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    const seq = ++previewSeqRef.current;
    const gen = genRef.current;
    const revisionId = workspaceRef.current.current?.id ?? "";
    dispatch({ type: "previewStarted", seq });
    apiRef.current.previewQuote(revisionId, sendableDraft(draftRef.current), ctrl.signal).then(
      (res) => {
        if (gen !== genRef.current) return;
        if (abortRef.current === ctrl) abortRef.current = null;
        dispatch({ type: "previewDone", seq, computed: res.computed });
      },
      (e) => {
        if (gen !== genRef.current || ctrl.signal.aborted || isAbort(e)) return;
        if (abortRef.current === ctrl) abortRef.current = null;
        dispatch({ type: "previewFailed", seq, error: asError(e) });
      },
    );
  }, []);

  const edit = useCallback(
    (fn: (draft: QuoteDraft) => QuoteDraft) => {
      if (!editableRef.current) return;
      const next = fn(draftRef.current);
      draftRef.current = next;
      editSeqRef.current += 1;
      dispatch({ type: "edit", fn: () => next });
      clearTimers();
      previewTimer.current = setTimeout(runPreview, PREVIEW_DELAY_MS);
      if (!stoppedRef.current) saveTimer.current = setTimeout(() => void runSave(), SAVE_DELAY_MS);
    },
    [runPreview, runSave],
  );

  const flush = useCallback(async (): Promise<FlushResult> => {
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = null;
    if (unsaved()) await runSave();
    else if (inFlightRef.current) await inFlightRef.current;
    return { ok: !stoppedRef.current && editSeqRef.current === savedSeqRef.current, version: versionRef.current };
  }, [runSave]);

  const resetTo = useCallback((ws: QuoteWorkspace) => {
    genRef.current += 1;
    clearTimers();
    abortRef.current?.abort();
    abortRef.current = null;
    workspaceRef.current = ws;
    const fresh = initialDraftState(ws);
    editableRef.current = isEditable(ws);
    draftRef.current = fresh.draft;
    versionRef.current = fresh.version;
    editSeqRef.current = savedSeqRef.current = 0;
    stoppedRef.current = false;
    keyRef.current = null;
    inFlightRef.current = null;
    queuedRef.current = false;
    lastErrorRef.current = null;
    dispatch({ type: "reset", workspace: ws });
  }, []);

  const reload = useCallback(async () => {
    const ws = await apiRef.current.fetchQuoteWorkspace(workspaceRef.current.quote.id);
    resetTo(ws);
    return ws;
  }, [resetTo]);

  const approve = useCallback(
    async (key: string = newQuoteCommandKey()) => {
      const flushed = await flush();
      if (!flushed.ok) throw lastErrorRef.current ?? new Error("El borrador no está guardado.");
      const res = await apiRef.current.approveQuoteRevision(workspaceRef.current.current?.id ?? "", flushed.version, key);
      await reload().catch(() => undefined);
      return res;
    },
    [flush, reload],
  );

  // A new workspace from the page (not our own reload) replaces the editor's state.
  useEffect(() => {
    if (workspaceRef.current !== workspace) resetTo(workspace);
  }, [workspace, resetTo]);

  // Leaving the page (navigation unmounts the editor): send what is pending instead of waiting.
  useEffect(
    () => () => {
      clearTimers();
      abortRef.current?.abort();
      if (unsaved()) void runSave();
    },
    [],
  );

  const pending = state.saveState === "dirty" || state.saveState === "saving" || state.saveState === "error";
  useEffect(() => {
    if (!pending) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
      void flush();
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [pending, flush]);

  return { state, editable: isEditable(state.workspace), edit, flush, reload, approve };
}
