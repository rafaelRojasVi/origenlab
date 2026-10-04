import { useCallback, useEffect, useRef, useState } from "react";
import type { AuthSessionState } from "../api/authClient";
import { OperatorApiError } from "../api/operatorClient";
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
 * Page memory: the last answer of each read, so reopening a page shows it at once while the
 * read runs again behind it. Memory only — never written to browser storage, since the answers
 * carry contact details. Kept per signed-in operator, role and profile (a viewer never sees an
 * admin's unmasked answer), only for a module-level loader (an inline closure is a new key every
 * render and simply never hits), and dropped on any failed refresh and whenever the session
 * ends (`AuthGate` calls `clearResourceCache`).
 */
const memory = new WeakMap<object, Map<string, unknown>>();
const remembered = new Set<object>();

export function clearResourceCache(): void {
  remembered.forEach((load) => memory.delete(load));
  remembered.clear();
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

function recall<T>(load: object, key: string | null): T | undefined {
  return key === null ? undefined : (memory.get(load)?.get(key) as T | undefined);
}

function remember(load: object, key: string, data: unknown): void {
  let byKey = memory.get(load);
  if (!byKey) {
    byKey = new Map();
    memory.set(load, byKey);
    remembered.add(load);
  }
  byKey.set(key, data);
}

/** `deps` re-run the read (e.g. a filter changed); `reload` re-runs it on demand. */
export function useResource<T>(
  load: () => Promise<T>,
  deps: readonly unknown[] = [],
): [ResourceState<T>, () => void] {
  const key = memoryKey(sessionScope(useAuthSession().session), deps);
  const [state, setState] = useState<ResourceState<T>>(() => {
    const kept = recall<T>(load, key);
    return kept === undefined ? { kind: "loading" } : { kind: "ready", data: kept };
  });
  const loadRef = useRef(load);
  loadRef.current = load;
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    const loader = loadRef.current;
    const kept = recall<T>(loader, key);
    setState(kept === undefined ? { kind: "loading" } : { kind: "ready", data: kept });
    loader()
      .then((data) => {
        if (key !== null) remember(loader, key, data);
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
