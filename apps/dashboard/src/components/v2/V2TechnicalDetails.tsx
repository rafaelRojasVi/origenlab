import type { ReactNode } from "react";

/**
 * The closed drawer that keeps the schema off the operator's screen.
 *
 * Identifiers, raw vocabulary values, provenance URIs, request bodies and the reasons a
 * command is unreachable are all true and all worth keeping — and none of them is what an
 * operator opened the page to read. They live here, collapsed, in one place per screen, so
 * the surface above can be the four questions the business actually asks.
 *
 * **Closed by default, and never `open` by prop.** A drawer that some screens expand is a
 * drawer nobody trusts to stay shut.
 */
export function V2TechnicalDetails({
  summary = "Detalles técnicos",
  children,
}: {
  summary?: string;
  children: ReactNode;
}) {
  return (
    <details
      className="rounded-lg border border-slate-200 bg-slate-50/60 px-3 py-2"
      data-testid="v2-technical-details"
    >
      <summary className="cursor-pointer text-xs font-medium text-[var(--color-muted)]">
        {summary}
      </summary>
      <div className="mt-2 space-y-3 text-xs text-[var(--color-muted)]">{children}</div>
    </details>
  );
}
