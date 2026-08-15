import type { ReactNode } from "react";
import type { Availed } from "../../api/institutionIntel/types";

/**
 * Renders one of the four data-availability states uniformly: not available,
 * available-but-empty, available-but-incomplete, or available. Backend W1
 * owns the authoritative status shape — this component just needs `Availed<T>`
 * shaped data, so swapping the real contract in only requires the adapter to
 * keep producing this shape (or a small mapper in front of it).
 */
export function AvailabilityBlock<T>({
  availed,
  render,
  notAvailableLabel = "No disponible en esta fuente todavía.",
  emptyLabel = "Se revisó — no hay datos que mostrar aquí.",
  renderPartial,
}: {
  availed: Availed<T>;
  render: (data: T) => ReactNode;
  notAvailableLabel?: string;
  emptyLabel?: string;
  renderPartial?: (partial: T) => ReactNode;
}) {
  if (availed.status === "not_available") {
    return (
      <p className="rounded-md border border-dashed border-[var(--color-border)] px-3 py-2 text-xs text-[var(--color-muted)]">
        {notAvailableLabel}
      </p>
    );
  }
  if (availed.status === "available_empty") {
    return (
      <p className="rounded-md border border-[var(--color-border)] bg-slate-50/80 px-3 py-2 text-xs text-[var(--color-muted)]">
        {emptyLabel}
      </p>
    );
  }
  if (availed.status === "available_incomplete") {
    return (
      <div
        className="rounded-md border border-amber-200 bg-amber-50/80 px-3 py-2 text-xs text-amber-950"
        data-testid="availability-incomplete"
      >
        <p className="font-medium">Lectura incompleta — puede faltar información.</p>
        {availed.reasonCodes.length > 0 ? (
          <p className="mt-1 text-[11px] text-amber-900">{availed.reasonCodes.join(", ")}</p>
        ) : null}
        {availed.partial !== undefined && renderPartial ? (
          <div className="mt-2">{renderPartial(availed.partial)}</div>
        ) : null}
      </div>
    );
  }
  return <>{render(availed.data)}</>;
}
