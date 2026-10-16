/**
 * The Cotizador module's panes, by quote: what a tab needs to know before it closes. Each quote's
 * editor (`useQuoteDraft`) registers itself while it is mounted; a pane not mounted yet (a tab
 * restored but never opened) has no entry, and nothing of it is pending.
 *
 * - `hasPendingSave()`: edits not saved yet (waiting for the autosave, saving, failed, in conflict
 *   or incomplete).
 * - `flush()`: sends the pending save now; true when everything is saved afterwards.
 */
export interface TabEditor {
  hasPendingSave: () => boolean;
  flush: () => Promise<boolean>;
}

const editors = new Map<string, TabEditor>();

/** Registers the editor of `quoteId`; the returned function removes it (and only it). */
export function registerTabEditor(quoteId: string, editor: TabEditor): () => void {
  editors.set(quoteId, editor);
  return () => {
    if (editors.get(quoteId) === editor) editors.delete(quoteId);
  };
}

export function tabEditor(quoteId: string): TabEditor | undefined {
  return editors.get(quoteId);
}

/** Whether `quoteId`'s pane holds edits not saved yet. A pane never mounted holds none. */
export function hasPendingSave(quoteId: string): boolean {
  return editors.get(quoteId)?.hasPendingSave() ?? false;
}

/** For tests: forget every registered editor. */
export function forgetTabEditors(): void {
  editors.clear();
}
