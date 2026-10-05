import { isStaleRefusal, refusalText, type Refusal } from "./commandRefusal";

export const RELOAD_CURRENT_VERSION = "Cargar versión actual";

/**
 * A failed command in the operator's words. When a version moved under the operator and the
 * screen can read the record again, «Cargar versión actual» does it: the form stays mounted, so
 * what the operator typed stays, and the next save carries the current version.
 */
export function CommandErrorNotice({
  refusal,
  onReload,
  fallback,
  overrides,
  className = "text-[11px] text-bad",
}: {
  refusal: Refusal | null;
  /** Re-read the record; offered only for a stale version. */
  onReload?: () => void;
  fallback?: string;
  overrides?: Readonly<Record<string, string>>;
  className?: string;
}) {
  if (!refusal) return null;
  return (
    <p role="alert" className={className}>
      {refusalText(refusal, { fallback, overrides })}
      {onReload && isStaleRefusal(refusal) ? (
        <>
          {" "}
          <ReloadButton onReload={onReload} />
        </>
      ) : null}
    </p>
  );
}

export function ReloadButton({ onReload }: { onReload: () => void }) {
  return (
    <button type="button" onClick={onReload} className="font-medium text-ink underline underline-offset-2 hover:no-underline">
      {RELOAD_CURRENT_VERSION}
    </button>
  );
}
