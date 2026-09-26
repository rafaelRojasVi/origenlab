import { useCallback, useEffect, useRef, useState } from "react";
import { OperatorApiError } from "../api/operatorClient";

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

/** `deps` re-run the read (e.g. a filter changed); `reload` re-runs it on demand. */
export function useResource<T>(
  load: () => Promise<T>,
  deps: readonly unknown[] = [],
): [ResourceState<T>, () => void] {
  const [state, setState] = useState<ResourceState<T>>({ kind: "loading" });
  const loadRef = useRef(load);
  loadRef.current = load;
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    setState({ kind: "loading" });
    loadRef
      .current()
      .then((data) => alive && setState({ kind: "ready", data }))
      .catch((err: unknown) => alive && setState(classifyError(err)));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, ...deps]);

  const reload = useCallback(() => setTick((n) => n + 1), []);
  return [state, reload];
}
