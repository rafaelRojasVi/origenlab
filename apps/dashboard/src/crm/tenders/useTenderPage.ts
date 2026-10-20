import { useCallback, useEffect, useRef, useState } from "react";
import { fetchTenderPage, tenderRefusal, type TenderPageData } from "./tenderApi";

/** While a preparation is queued or running, the page is read again this often. */
export const POLL_MS = 5000;
/** After failed polls the wait grows: 5 → 10 → 20 → 60 s (as multiples of the interval). */
const BACKOFF = [1, 2, 4, 12];
/** From this many failed polls in a row the page says it is retrying. */
const SAY_RETRYING_AFTER = 2;

export type TenderPageState =
  | { kind: "loading" }
  | { kind: "ready"; data: TenderPageData }
  | { kind: "error"; error: unknown };

/** How the background reads are going: `retrying` after repeated failures, `stopped` after an API refusal. */
export type PollTrouble = null | "retrying" | "stopped";

/** True while the worker still has a phase of this tender in hand. */
export function preparationBusy(data: TenderPageData): boolean {
  return (["read", "fill"] as const).some((p) => {
    const status = data.preparations[p]?.status;
    return status === "queued" || status === "running";
  });
}

/** The API refused for certain (a 4xx): reading again will not help. */
function refused(err: unknown): boolean {
  const r = tenderRefusal(err);
  return !!r && r.status >= 400 && r.status < 500;
}

/**
 * One tender's page, read once and then every `pollMs` only while a preparation is queued or
 * running. Reads never overlap: the next poll is scheduled when a read ends, and a `reload` asked
 * for during a read runs once it is over. A failed poll keeps what is on screen and waits longer
 * each time; an API refusal stops the polling. Everything stops on unmount or when `id` changes
 * (each id is its own run: a late answer for another tender is dropped). `enabled = false` (a
 * viewer) reads nothing at all.
 */
export function useTenderPage(
  id: string,
  enabled: boolean,
  pollMs: number = POLL_MS,
): [TenderPageState, () => void, PollTrouble] {
  const [state, setState] = useState<TenderPageState>({ kind: "loading" });
  const [trouble, setTrouble] = useState<PollTrouble>(null);
  const run = useRef<{ load: () => void } | null>(null);

  useEffect(() => {
    if (!enabled) return undefined;
    let alive = true;
    let inFlight = false;
    let again = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let busy = false;
    let hasData = false;
    let failures = 0;
    let stopped = false;

    const load = async (): Promise<void> => {
      if (!alive) return;
      if (inFlight) {
        again = true;
        return;
      }
      inFlight = true;
      if (timer) clearTimeout(timer);
      timer = null;
      try {
        const data = await fetchTenderPage(id);
        if (!alive) return;
        hasData = true;
        busy = preparationBusy(data);
        failures = 0;
        stopped = false;
        setTrouble(null);
        setState({ kind: "ready", data });
      } catch (error) {
        if (!alive) return;
        if (!hasData) {
          busy = false;
          setState({ kind: "error", error });
        } else if (refused(error)) {
          stopped = true;
          setTrouble("stopped");
        } else {
          failures += 1;
          if (failures >= SAY_RETRYING_AFTER) setTrouble("retrying");
        }
      } finally {
        inFlight = false;
        if (alive) {
          if (again) {
            again = false;
            void load();
          } else if (busy && !stopped) {
            timer = setTimeout(() => void load(), pollMs * BACKOFF[Math.min(failures, BACKOFF.length - 1)]);
          }
        }
      }
    };

    run.current = {
      load: () => {
        if (!hasData) setState({ kind: "loading" });
        void load();
      },
    };
    setState({ kind: "loading" });
    setTrouble(null);
    void load();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
      run.current = null;
    };
  }, [id, enabled, pollMs]);

  const reload = useCallback(() => run.current?.load(), []);
  return [state, reload, trouble];
}
