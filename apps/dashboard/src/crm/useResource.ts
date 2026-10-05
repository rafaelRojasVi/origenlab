import { useCallback, useEffect, useRef, useState } from "react";
import type { AuthSessionState } from "../api/authClient";
import { OperatorApiError, getWriteGeneration, noteWrite } from "../api/operatorClient";
import { useAuthSession } from "../context/AuthSessionContext";

/**
 * One read, four honest outcomes. `permission` (401/403 from the API) and `unavailable`
 * (404, or the proxy's 403 `path_not_allowed`) are kept apart from a real `error`, so a page
 * never says "no data" when the truth is "you may not see this" or "not enabled here".
 */
export type ResourceState<T> =
  | { kind: "loading" }
  | { kind: "ready"; data: T }
  | { kind: "permission"; message: string }
  | { kind: "unavailable"; message: string }
  | { kind: "error"; message: string };

export function classifyError(err: unknown): Exclude<ResourceState<never>, { kind: "loading" } | { kind: "ready" }> {
  if (err instanceof OperatorApiError) {
    const text = err.message || "";
    if (err.status === 404 || (err.status === 403 && text.includes("path_not_allowed"))) {
      return { kind: "unavailable", message: text };
    }
    if (err.status === 401 || err.status === 403) {
      return { kind: "permission", message: text };
    }
    return { kind: "error", message: text || `HTTP ${err.status}` };
  }
  return { kind: "error", message: err instanceof Error ? err.message : String(err) };
}

/**
 * Page memory: the last answer of each read, so reopening a page shows it at once. Memory only —
 * never written to browser storage, since the answers carry contact details. Kept per signed-in
 * operator, role and profile (a viewer never sees an admin's unmasked answer), only for a
 * module-level loader (an inline closure is a new key every render and simply never hits), and
 * dropped on any failed refresh and whenever the session ends (`AuthGate` calls
 * `clearResourceCache`).
 *
 * Freshness: an answer younger than the read's freshness window (`freshMs`, default
 * {@link DEFAULT_FRESH_MS}) is shown without asking the API again — each request costs a session
 * check and at least one ~180 ms database round trip, and moving between pages within half a
 * minute should not pay that again. Older answers are shown at once and refreshed behind them, as
 * before. A command (any write) ends every window (`noteWrite`), and `reload()` always asks.
 * Concurrent reads of one URL share one request (`fetchJsonGet`).
 */
export const DEFAULT_FRESH_MS = 30_000;

interface Remembered {
  data: unknown;
  at: number;
  generation: number;
}

const memory = new WeakMap<object, Map<string, Remembered>>();
const remembered = new Set<object>();

export function clearResourceCache(): void {
  remembered.forEach((load) => memory.delete(load));
  remembered.clear();
  noteWrite(); // nothing in flight for the session that ended is handed to the next one
}

function sessionScope(session: AuthSessionState): string | null {
  if (session.kind !== "signed_in") return null;
  return [session.operator.operatorId, session.operator.role, session.profile?.id ?? ""].join("|");
}

function memoryKey(scope: string | null, deps: readonly unknown[]): string | null {
  if (scope === null) return null;
  try {
    return `${scope}#${JSON.stringify(deps)}`;
  } catch {
    return null;
  }
}

function recall(load: object, key: string | null): Remembered | undefined {
  return key === null ? undefined : memory.get(load)?.get(key);
}

function remember(load: object, key: string, data: unknown, generation: number): void {
  let byKey = memory.get(load);
  if (!byKey) {
    byKey = new Map();
    memory.set(load, byKey);
    remembered.add(load);
  }
  byKey.set(key, { data, at: Date.now(), generation });
}

function isFresh(kept: Remembered, freshMs: number): boolean {
  return kept.generation === getWriteGeneration() && Date.now() - kept.at < freshMs;
}

export interface ResourceOptions {
  /** How long a remembered answer is shown without asking again (ms); 0 always asks. */
  freshMs?: number;
}

/** `deps` re-run the read (e.g. a filter changed); `reload` re-runs it on demand, always. */
export function useResource<T>(
  load: () => Promise<T>,
  deps: readonly unknown[] = [],
  options: ResourceOptions = {},
): [ResourceState<T>, () => void] {
  const freshMs = options.freshMs ?? DEFAULT_FRESH_MS;
  const key = memoryKey(sessionScope(useAuthSession().session), deps);
  const [state, setState] = useState<ResourceState<T>>(() => {
    const kept = recall(load, key);
    return kept === undefined ? { kind: "loading" } : { kind: "ready", data: kept.data as T };
  });
  const loadRef = useRef(load);
  loadRef.current = load;
  const [tick, setTick] = useState(0);
  const handledTick = useRef(tick);

  useEffect(() => {
    let alive = true;
    const loader = loadRef.current;
    const kept = recall(loader, key);
    const reloading = handledTick.current !== tick;
    handledTick.current = tick;
    setState(kept === undefined ? { kind: "loading" } : { kind: "ready", data: kept.data as T });
    if (kept !== undefined && !reloading && isFresh(kept, freshMs)) {
      return () => {
        alive = false;
      };
    }
    const generation = getWriteGeneration();
    loader()
      .then((data) => {
        if (key !== null) remember(loader, key, data, generation);
        if (alive) setState({ kind: "ready", data });
      })
      .catch((err: unknown) => {
        if (key !== null) memory.get(loader)?.delete(key);
        if (alive) setState(classifyError(err));
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, key, ...deps]);

  const reload = useCallback(() => setTick((n) => n + 1), []);
  return [state, reload];
}
