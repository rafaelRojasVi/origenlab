import { useMemo, useState } from "react";

import type { CommercialWorkQueueResponse } from "../../api/commercialOperationsTypes";
import { summarizeCommercialWorkQueue } from "../../lib/commercialWorkQueue";
import { dashboardSectionToHash } from "../../lib/dashboardHashRoute";
import { CommercialOpportunityDetailDrawer } from "./CommercialOpportunityDetailDrawer";
import { CommercialFollowupCard } from "./workQueue/CommercialFollowupCard";
import { CommercialReviewCard } from "./workQueue/CommercialReviewCard";
import { CommercialTaskCard } from "./workQueue/CommercialTaskCard";

const MAX_VISIBLE_ITEMS = 5;

function QueueSection({
  title,
  count,
  emptyMessage,
  children,
}: {
  title: string;
  count: number;
  emptyMessage: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-4 shadow-sm">
      <div className="flex items-center justify-between gap-3">
        <h3 className="font-semibold text-slate-900">{title}</h3>
        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-700">
          {count}
        </span>
      </div>

      {count > 0 ? (
        <div className="mt-3 space-y-2">{children}</div>
      ) : (
        <p className="mt-3 text-sm text-[var(--color-muted)]">
          {emptyMessage}
        </p>
      )}
    </section>
  );
}

export function CommercialWorkQueuePanel({
  queue,
  onSelectContact,
}: {
  queue: CommercialWorkQueueResponse;
  onSelectContact: (email: string) => void;
}) {
  const summary = useMemo(
    () => summarizeCommercialWorkQueue(queue),
    [queue],
  );

  const [selectedOpportunityId, setSelectedOpportunityId] =
    useState<string | null>(null);

  return (
    <>
      <section
        className="space-y-4"
        aria-labelledby="commercial-priority-heading"
        data-testid="commercial-work-queue-panel"
      >
        <div>
          <h2
            id="commercial-priority-heading"
            className="text-lg font-semibold text-slate-900"
          >
            Prioridad comercial
          </h2>

          <p className="mt-1 text-sm text-[var(--color-muted)]">
            Registros que requieren seguimiento o decisión del operador.
            Esta vista prioriza; las acciones se realizan dentro del ciclo
            de cada oportunidad.
          </p>
        </div>

        <div className="grid gap-4 xl:grid-cols-2">
          <QueueSection
            title="Seguimientos vencidos"
            count={summary.overdueTasks.length}
            emptyMessage="No hay tareas vencidas."
          >
            {summary.overdueTasks
              .slice(0, MAX_VISIBLE_ITEMS)
              .map((item) => (
                <CommercialTaskCard
                  key={item.task.task_id}
                  item={item}
                  urgency="overdue"
                  onOpenOpportunity={setSelectedOpportunityId}
                />
              ))}
          </QueueSection>

          <QueueSection
            title="Para hoy"
            count={summary.todayTasks.length}
            emptyMessage="No hay tareas con vencimiento hoy."
          >
            {summary.todayTasks
              .slice(0, MAX_VISIBLE_ITEMS)
              .map((item) => (
                <CommercialTaskCard
                  key={item.task.task_id}
                  item={item}
                  urgency="today"
                  onOpenOpportunity={setSelectedOpportunityId}
                />
              ))}
          </QueueSection>

          <QueueSection
            title="Revisión humana"
            count={queue.review_opportunities.length}
            emptyMessage="No hay oportunidades pendientes de revisión."
          >
            {queue.review_opportunities
              .slice(0, MAX_VISIBLE_ITEMS)
              .map((item) => (
                <CommercialReviewCard
                  key={item.opportunity_id}
                  item={item}
                  onOpenOpportunity={setSelectedOpportunityId}
                />
              ))}
          </QueueSection>

          <QueueSection
            title="Cotizaciones por seguir"
            count={queue.quote_followups.length}
            emptyMessage="No hay cotizaciones pendientes de seguimiento."
          >
            {queue.quote_followups
              .slice(0, MAX_VISIBLE_ITEMS)
              .map((item) => (
                <CommercialFollowupCard
                  key={item.opportunity_id}
                  item={item}
                  onOpenOpportunity={setSelectedOpportunityId}
                />
              ))}
          </QueueSection>
        </div>

        {summary.upcomingTasks.length > 0 ||
        summary.unscheduledTasks.length > 0 ? (
          <p className="text-xs text-[var(--color-muted)]">
            Además: {summary.upcomingTasks.length} tarea(s) próxima(s) ·{" "}
            {summary.unscheduledTasks.length} sin fecha.
          </p>
        ) : null}
      </section>

      <CommercialOpportunityDetailDrawer
        opportunityId={selectedOpportunityId}
        open={selectedOpportunityId !== null}
        onClose={() => setSelectedOpportunityId(null)}
        onSelectContact={onSelectContact}
        promotedSalesOpportunityId={null}
        onOpenPipeline={() => {
          window.location.hash = dashboardSectionToHash("pipeline");
        }}
      />
    </>
  );
}
