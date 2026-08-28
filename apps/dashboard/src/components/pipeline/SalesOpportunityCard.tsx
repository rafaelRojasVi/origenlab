import { formatSalesOpportunityAge } from "../../lib/salesOpportunityFormat";
import type { SalesOpportunityListItem, SalesOpportunityStage } from "../../api/commercialOperationsTypes";
import { StageChangeMenu } from "./StageChangeMenu";

function taskUrgencyClass(dueAt: string | null): string {
  if (!dueAt) return "text-slate-600";

  const due = new Date(dueAt).getTime();
  if (Number.isNaN(due)) return "text-slate-600";

  const diffDays = Math.floor((due - Date.now()) / (1000 * 60 * 60 * 24));
  if (diffDays < 0) return "text-red-700";
  if (diffDays <= 1) return "text-amber-700";
  return "text-slate-600";
}

export function SalesOpportunityCard({
  item,
  onOpen,
  onStageChange,
  stagePending,
}: {
  item: SalesOpportunityListItem;
  onOpen: () => void;
  onStageChange: (stage: SalesOpportunityStage) => void;
  stagePending: boolean;
}) {
  const primaryIdentity = item.account_display_domain ?? item.contact_display_email;

  return (
    <article
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen();
        }
      }}
      className="motion-safe:transition-shadow motion-safe:duration-150 cursor-pointer space-y-2 rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-3 shadow-sm hover:shadow-md focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600"
    >
      <div className="flex items-start justify-between gap-2">
        <p className="min-w-0 truncate text-sm font-semibold text-slate-900">
          {primaryIdentity ?? "Sin identidad de cliente"}
        </p>
        <span className="shrink-0 text-xs text-[var(--color-muted)]">
          {formatSalesOpportunityAge(item.stage_updated_at)}
        </span>
      </div>

      <p className="truncate text-sm text-slate-700">{item.title}</p>

      {item.next_task_title ? (
        <p className={`text-xs ${taskUrgencyClass(item.next_task_due_at)}`}>
          {item.next_task_title}
          {item.open_task_count > 1 ? ` · +${item.open_task_count - 1} más` : ""}
        </p>
      ) : (
        <p className="text-xs text-[var(--color-muted)]">Sin próxima acción</p>
      )}

      <div className="flex items-center justify-between gap-2 pt-1">
        <span className="truncate text-xs text-slate-500">{item.owner_key}</span>
        <StageChangeMenu stage={item.stage} disabled={stagePending} onChange={onStageChange} />
      </div>
    </article>
  );
}
