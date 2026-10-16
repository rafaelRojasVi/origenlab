/**
 * The Cotizador header's autosave line (canvas «guardado automáticamente hace 4 s»): saved and
 * how long ago, saving, edits waiting, or a failed save with its reason and «Reintentar». A conflict says nothing
 * here: the page shows its banner instead.
 */
import { useEffect, useState } from "react";
import type { SaveState } from "./draftEditor";

/** «hace 4 s», «hace 5 min», then «a las 14:05». */
export function savedAgo(at: number, now: number): string {
  const s = Math.max(0, Math.floor((now - at) / 1000));
  if (s < 60) return `hace ${s} s`;
  if (s < 3600) return `hace ${Math.floor(s / 60)} min`;
  return `a las ${new Date(at).toLocaleTimeString("es-CL", { hour: "2-digit", minute: "2-digit", hour12: false })}`;
}

function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [active]);
  return now;
}

export function SaveStatus({
  saveState,
  lastSavedAt,
  onRetry,
  incomplete = false,
  message = null,
}: {
  saveState: SaveState;
  lastSavedAt: number | null;
  onRetry: () => void;
  /** The draft has something to fill in first: nothing is saved until then. */
  incomplete?: boolean;
  /** Why the save was refused (the API's words, or where to look); null for a plain failure. */
  message?: string | null;
}) {
  const ticking = saveState === "saved" && lastSavedAt !== null;
  const now = useNow(ticking);
  if (saveState === "conflict") return null;
  const cls = "text-[12.5px] text-ink-muted";
  if (saveState === "saving") return <span className={cls}>guardando…</span>;
  if (saveState === "dirty") return <span className={cls}>{incomplete ? "sin guardar · falta completar datos" : "sin guardar"}</span>;
  if (saveState === "error") {
    return (
      <span role="alert" className="text-[12.5px] text-bad">
        {message ? `no se pudo guardar: ${message}` : "no se pudo guardar"} ·{" "}
        <button type="button" onClick={onRetry} className="font-semibold text-brand-700 underline hover:text-brand-900">
          Reintentar
        </button>
      </span>
    );
  }
  return (
    <span className={cls}>
      {ticking ? `guardado automáticamente ${savedAgo(lastSavedAt, now)}` : "guardado"}
    </span>
  );
}
