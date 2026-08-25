import type { CommercialWorkQueueTask } from "../../../api/commercialOperationsTypes";
import { commercialOpportunityStageLabel } from "../../../lib/commercialOpportunityFormat";
import { formatDashboardDateTime } from "../../../lib/dashboardDateFormat";

export function CommercialTaskCard({
  item,
  urgency,
  onOpenOpportunity,
}: {
  item: CommercialWorkQueueTask;
  urgency: "overdue" | "today";
  onOpenOpportunity: (opportunityId: string) => void;
}) {
  const task = item.task;
  const opportunityId = task.opportunity_id;

  const urgencyLabel =
    urgency === "overdue" ? "Vencida" : "Vence hoy";

  return (
    <article
      className={`rounded-lg border p-3 ${
        urgency === "overdue"
          ? "border-red-200 bg-red-50/60"
          : "border-amber-200 bg-amber-50/60"
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-medium text-slate-900">{task.title}</p>

          <p className="mt-1 truncate text-sm text-slate-700">
            {item.account_display_domain ??
              item.contact_display_email ??
              "Cuenta sin identificar"}
          </p>
        </div>

        <span className="shrink-0 rounded-full bg-white px-2 py-0.5 text-xs font-medium text-slate-700">
          {urgencyLabel}
        </span>
      </div>

      <dl className="mt-3 grid gap-1 text-xs text-[var(--color-muted)]">
        <div>
          <dt className="inline font-medium">Vencimiento: </dt>
          <dd className="inline">
            {task.due_at
              ? formatDashboardDateTime(task.due_at)
              : "Sin fecha"}
          </dd>
        </div>

        {item.canonical_stage ? (
          <div>
            <dt className="inline font-medium">Etapa: </dt>
            <dd className="inline">
              {commercialOpportunityStageLabel(item.canonical_stage)}
            </dd>
          </div>
        ) : null}

        {task.owner_key ? (
          <div>
            <dt className="inline font-medium">Responsable: </dt>
            <dd className="inline">{task.owner_key}</dd>
          </div>
        ) : null}
      </dl>

      {opportunityId ? (
        <button
          type="button"
          onClick={() => onOpenOpportunity(opportunityId)}
          aria-label={`Abrir oportunidad ${opportunityId}`}
          className="mt-3 text-xs font-medium text-brand-700 hover:text-brand-900"
        >
          Ver oportunidad →
        </button>
      ) : null}
    </article>
  );
}
