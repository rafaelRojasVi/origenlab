import { useCallback, useEffect, useMemo, useReducer, useRef } from "react";
import {
  draftProblems,
  draftReducer,
  initialDraftState,
  freezesEditor,
  isEditable,
  lineUids,
  sendableDraft,
  withUids,
  type DraftEditorState,
  type DraftProblem,
} from "./draftEditor";
import {
  approveQuoteRevision,
  fetchQuoteWorkspace,
  newQuoteCommandKey,
  previewQuote,
  saveQuoteDraft,
  QuoteRefusal,
  type QuoteDraft,
  type QuoteWorkspace,
} from "./quoteApi";
import { registerTabEditor } from "./tabEditors";

/**
 * The quote editor's engine (rulings B2/B3): local edits, a live preview and an autosave.
 *
 * - Preview: 300 ms after the last edit, `POST /v2/quotes/preview` (it writes nothing). A new
 *   preview aborts the one in flight. Every result is tagged with the edit it priced, and one for
 *   an older draft than the one shown (a late preview, or a save overtaken by a newer preview)
 *   never replaces it.
 * - Autosave: 1.2 s after the last edit, `save-quote-draft` with the last version the server
 *   gave us. One save is in flight at a time; edits during it queue one follow-up, sent with the
 *   version that save returned (never a 409 against ourselves). One `Idempotency-Key` per save
 *   attempt: retrying the same edits (`retry()`, `flush()`) reuses it, a new edit gets a new one.
 * - Incomplete drafts: while `draftProblems` finds something the API would refuse (a line with
 *   no description, a cleared quantity…), nothing is previewed or saved; `invalid` lists the
 *   problems in Spanish and the state stays "dirty".
 * - Conflict: `409 stale_version` (someone else saved) freezes the editor (no edits, previews or
 *   saves) until `reload()`, which loads the workspace again and discards the local edits. So do
 *   `409 not_a_draft` and `404 revision_not_found` (approved or voided in another tab): saving
 *   again could never succeed, so nothing is retried.
 * - Leaving: unmounting sends a pending save; `beforeunload` warns while edits are unsaved, and
 *   `pending` tells the page to guard its own navigation.
 * - Tabs: while mounted, the editor is registered under its quote (`tabEditors`), so the Cotizador
 *   module can ask a pane whether a save is pending and send it before closing its tab. Each
 *   editor's state (and its freeze) is its own: two quotes open at once never affect each other.
 *
 * The page must `flush()` (or use `approve()`) before any command that needs the saved draft. A new
 * `workspace` prop resets the editor, except for the same revision while edits are pending or at
 * a version older than ours (a stale copy must not wipe local edits).
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
  /** True when every edit is saved (no conflict, no failed save, no incomplete draft left). */
  ok: boolean;
  /** The version to send with the next command. */
  version: number;
}

export interface QuoteDraftEditor {
  state: DraftEditorState;
  /** An authored draft, not frozen by a conflict. */
  editable: boolean;
  /** The prices shown are for an older draft (an edit since has not been priced yet). */
  stale: boolean;
  /** What the API would refuse in the current draft; nothing is sent while this is not empty. */
  invalid: DraftProblem[];
  /** Edits not saved yet (dirty, saving, failed, in conflict or incomplete): guard navigation while true. */
  pending: boolean;
  edit: (fn: (draft: QuoteDraft) => QuoteDraft) => void;
  flush: () => Promise<FlushResult>;
  /**
   * Saves now even when nothing was edited (B4 «Guardar ahora»: the saved totals are older than
   * today's rates or parameters, and a save stores the new ones). Pending edits go in that save.
   */
  saveNow: () => Promise<FlushResult>;
  /** After a failed save ("error"), sends it again with the same key; otherwise reports the state. */
  retry: () => Promise<FlushResult>;
  reload: () => Promise<QuoteWorkspace>;
  /**
   * Saves pending edits first (or the whole draft, when the workspace says its stored prices are
   * older than today's), then approves with the version that save returned, then reloads. Throws
   * the refusal. Without `key`, one key is kept while the same revision and version is retried.
   */
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
  const propRef = useRef(workspace);
  const editableRef = useRef(isEditable(workspace));
  const draftRef = useRef(state.draft);
  const versionRef = useRef(state.version);
  const editSeqRef = useRef(0);
  const savedSeqRef = useRef(0);
  const stoppedRef = useRef(false);
  const keyRef = useRef<{ seq: number; key: string } | null>(null);
  const approveKeyRef = useRef<{ sig: string; key: string } | null>(null);
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
  const incomplete = () => draftProblems(draftRef.current).length > 0;
  const unsaved = () => editableRef.current && !stoppedRef.current && editSeqRef.current !== savedSeqRef.current;
  const status = (): FlushResult => ({
    ok: !stoppedRef.current && editSeqRef.current === savedSeqRef.current,
    version: versionRef.current,
  });

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
        if (!unsaved() || incomplete()) return;
        const seq = editSeqRef.current;
        const key = keyRef.current?.seq === seq ? keyRef.current.key : newQuoteCommandKey();
        keyRef.current = { seq, key };
        dispatch({ type: "saveStarted", keys: lineUids(draftRef.current) });
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
          if (freezesEditor(error)) {
            // Freeze: nothing more is sent until the operator reloads.
            stoppedRef.current = true;
            clearTimers();
            abortRef.current?.abort();
            abortRef.current = null;
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
    if (stoppedRef.current || incomplete()) return;
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    const seq = ++previewSeqRef.current;
    const draftSeq = editSeqRef.current;
    const keys = lineUids(draftRef.current);
    const gen = genRef.current;
    const revisionId = workspaceRef.current.current?.id ?? "";
    dispatch({ type: "previewStarted", seq });
    apiRef.current.previewQuote(revisionId, sendableDraft(draftRef.current), ctrl.signal).then(
      (res) => {
        if (gen !== genRef.current) return;
        if (abortRef.current === ctrl) abortRef.current = null;
        dispatch({ type: "previewDone", seq, draftSeq, computed: res.computed, keys });
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
      if (!editableRef.current || stoppedRef.current) return;
      const next = withUids(fn(draftRef.current));
      draftRef.current = next;
      editSeqRef.current += 1;
      dispatch({ type: "edit", fn: () => next });
      clearTimers();
      previewTimer.current = setTimeout(runPreview, PREVIEW_DELAY_MS);
      saveTimer.current = setTimeout(() => void runSave(), SAVE_DELAY_MS);
    },
    [runPreview, runSave],
  );

  const flush = useCallback(async (): Promise<FlushResult> => {
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = null;
    if (unsaved() && !incomplete()) await runSave();
    else if (inFlightRef.current) await inFlightRef.current;
    return status();
  }, [runSave]);

  const saveNow = useCallback((): Promise<FlushResult> => {
    if (!editableRef.current || stoppedRef.current) return Promise.resolve(status());
    // An incomplete draft is never sent, so nothing is marked as edited (it would read «sin
    // guardar» for an edit nobody made); a save already in flight is still waited for.
    if (incomplete()) return flush().then((r) => ({ ...r, ok: false }));
    if (editSeqRef.current === savedSeqRef.current && !inFlightRef.current) {
      // Nothing edited: mark the same draft as an edit, so the save loop sends it.
      editSeqRef.current += 1;
      dispatch({ type: "edit", fn: (d) => d });
    }
    return flush();
  }, [flush]);

  const retry = useCallback(
    (): Promise<FlushResult> => (lastErrorRef.current && !stoppedRef.current ? flush() : Promise.resolve(status())),
    [flush],
  );

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
    dispatch({ type: "reset", state: fresh });
  }, []);

  const reload = useCallback(async () => {
    const ws = await apiRef.current.fetchQuoteWorkspace(workspaceRef.current.quote.id);
    resetTo(ws);
    return ws;
  }, [resetTo]);

  const approve = useCallback(
    async (key?: string) => {
      const problems = draftProblems(draftRef.current);
      if (editableRef.current && problems.length > 0) throw new Error(problems[0].message_es);
      // The API refuses to freeze stored prices that differ from today's (`draft_needs_saving`):
      // a draft the workspace says needs saving, not saved in this page yet, is saved first; one
      // refused for that reason anyway (the rates moved since our save) is saved and tried once more.
      let force = !!workspaceRef.current.current?.needs_saving && savedSeqRef.current === 0;
      for (let attempt = 0; ; attempt++) {
        const flushed = force ? await saveNow() : await flush();
        if (!flushed.ok) throw lastErrorRef.current ?? new Error("El borrador no está guardado.");
        const revisionId = workspaceRef.current.current?.id ?? "";
        // One key per approval: retrying the same revision at the same version reuses it, so a
        // request that reached the API but whose answer was lost is replayed, not run twice.
        const sig = `${revisionId}@${flushed.version}`;
        const useKey = key ?? (approveKeyRef.current?.sig === sig ? approveKeyRef.current.key : newQuoteCommandKey());
        approveKeyRef.current = { sig, key: useKey };
        let res: Awaited<ReturnType<typeof approveQuoteRevision>>;
        try {
          res = await apiRef.current.approveQuoteRevision(revisionId, flushed.version, useKey);
        } catch (e) {
          if (attempt === 0 && e instanceof QuoteRefusal && e.code === "draft_needs_saving") {
            force = true;
            continue;
          }
          throw e;
        }
        approveKeyRef.current = null;
        await reload().catch(() => undefined);
        return res;
      }
    },
    [flush, saveNow, reload],
  );

  // A new workspace from the page (not our own reload) replaces the editor's state, unless it is
  // the same revision and we hold edits it does not have, or it is older than what we saved.
  useEffect(() => {
    if (propRef.current === workspace) return;
    propRef.current = workspace;
    const ours = workspaceRef.current.current;
    const theirs = workspace.current;
    if (ours && theirs && ours.id === theirs.id) {
      const holding = unsaved() || inFlightRef.current !== null;
      if (holding || theirs.version < versionRef.current) return;
    }
    resetTo(workspace);
  }, [workspace, resetTo]);

  // Leaving the page (navigation unmounts the editor): send what is pending instead of waiting.
  useEffect(
    () => () => {
      clearTimers();
      abortRef.current?.abort();
      if (unsaved() && !incomplete()) void runSave();
    },
    [runSave],
  );

  const invalid = useMemo(() => draftProblems(state.draft), [state.draft]);
  // A conflict always holds the local edits its save carried; «Ver cambios» discards them.
  const pending =
    state.saveState === "dirty" ||
    state.saveState === "saving" ||
    state.saveState === "error" ||
    state.saveState === "conflict" ||
    (invalid.length > 0 && state.editSeq > 0);
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

  const pendingRef = useRef(pending);
  pendingRef.current = pending;
  const quoteId = state.workspace.quote.id;
  useEffect(
    () =>
      registerTabEditor(quoteId, {
        hasPendingSave: () => pendingRef.current || unsaved() || inFlightRef.current !== null,
        flush: () => flush().then((r) => r.ok),
      }),
    // `unsaved` reads refs only: any render's copy answers the same.
    [quoteId, flush],
  );

  return {
    state,
    editable: isEditable(state.workspace) && state.saveState !== "conflict",
    stale: state.computedSeq < state.editSeq,
    invalid,
    pending,
    edit,
    flush,
    saveNow,
    retry,
    reload,
    approve,
  };
}
