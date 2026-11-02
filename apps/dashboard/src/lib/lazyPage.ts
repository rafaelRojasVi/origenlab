import { lazy, type ComponentType } from "react";

const RELOADED_KEY = "crm.chunk.reloaded";

/**
 * A page loaded on first visit. A release replaces the hashed files, so a tab left open across it
 * asks for a chunk that no longer exists: reload once to pick up the new build instead of showing
 * a blank section. The flag stops a loop when the failure is something else (offline).
 */
export function lazyPage<P extends object>(load: () => Promise<ComponentType<P>>) {
  return lazy(async () => {
    try {
      const page = await load();
      try {
        window.sessionStorage.removeItem(RELOADED_KEY);
      } catch {
        /* storage blocked: nothing to clear */
      }
      return { default: page };
    } catch (err) {
      let reloaded = true;
      try {
        reloaded = window.sessionStorage.getItem(RELOADED_KEY) === "1";
        if (!reloaded) window.sessionStorage.setItem(RELOADED_KEY, "1");
      } catch {
        /* storage blocked: never reload, just fail */
      }
      if (!reloaded) window.location.reload();
      throw err;
    }
  });
}
