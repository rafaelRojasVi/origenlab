/**
 * «Ocultar» a person suggestion — in this browser only. A dismissal everyone shares needs a table
 * and waits (spec, part A §3). Only the suggestion's opaque `suggestion_ref` is stored, never the
 * address: the page memory keeps contact details out of browser storage (`useResource.ts`), and
 * so does this. Storage can be missing or refuse (private window): hiding then lasts the visit.
 */
import { useCallback, useState } from "react";

export const HIDDEN_SUGGESTIONS_KEY = "origenlab.crm.hiddenPersonSuggestions.v1";

export function readHidden(): Set<string> {
  try {
    const raw = window.localStorage.getItem(HIDDEN_SUGGESTIONS_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return new Set(Array.isArray(parsed) ? parsed.filter((v): v is string => typeof v === "string") : []);
  } catch {
    return new Set();
  }
}

function writeHidden(refs: Set<string>): void {
  try {
    window.localStorage.setItem(HIDDEN_SUGGESTIONS_KEY, JSON.stringify([...refs]));
  } catch {
    /* storage refused: hidden for this visit only */
  }
}

export function useHiddenSuggestions(): [Set<string>, (ref: string) => void] {
  const [hidden, setHidden] = useState<Set<string>>(readHidden);
  const hide = useCallback(
    (ref: string) => {
      // The updater stays pure: compute from the current state, then set and write.
      const next = new Set(hidden);
      next.add(ref);
      setHidden(next);
      writeHidden(next);
    },
    [hidden],
  );
  return [hidden, hide];
}
