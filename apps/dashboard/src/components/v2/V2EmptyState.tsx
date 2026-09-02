import type { ReactNode } from "react";

export function V2EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div
      className="rounded-xl border border-dashed border-slate-300 bg-[var(--color-card)] px-6 py-10 text-center"
      data-testid="v2-empty-state"
    >
      <p className="text-sm font-medium text-slate-800">{title}</p>
      <p className="mt-1 text-sm text-[var(--color-muted)]">{description}</p>
      {action ? <div className="mt-4 flex justify-center">{action}</div> : null}
    </div>
  );
}
