import type { ReactNode } from "react";

/** One titled block of a 360 screen. `count` is the real total, never the rendered length. */
export function V2Panel({
  title,
  count,
  caption,
  actions,
  children,
  testId,
}: {
  title: string;
  count?: number;
  caption?: string;
  actions?: ReactNode;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <section
      className="space-y-2 rounded-xl border border-slate-200 bg-[var(--color-card)] p-4"
      data-testid={testId}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold text-slate-900">
          {title}
          {typeof count === "number" ? (
            <span className="ml-2 font-normal text-[var(--color-muted)]">
              {count.toLocaleString("es-CL")}
            </span>
          ) : null}
        </h3>
        {actions}
      </div>
      {caption ? <p className="text-xs text-[var(--color-muted)]">{caption}</p> : null}
      {children}
    </section>
  );
}
