import { useMemo } from "react";
import { useDashboardData } from "../context/DashboardDataContext";
import { dashboardSectionToHash } from "../lib/dashboardHashRoute";
import type { DashboardSection } from "../lib/dashboardNav";
import {
  ACTIONABLE_OPPORTUNITIES_LABEL,
  ACTIONABLE_OPPORTUNITIES_NORMAL_HINT,
  ACTIONABLE_OPPORTUNITIES_STALE_HINT,
  ACTIONABLE_OPPORTUNITIES_UNAVAILABLE_HINT,
  summarizeProcurementStatus,
} from "../lib/procurementSummary";
import { computeTodaySummaryCounts } from "../lib/todaySummaryCounts";
import { summarizeCommercialWorkQueue } from "../lib/commercialWorkQueue";
import { CommercialWorkQueuePanel } from "../components/commercial/CommercialWorkQueuePanel";

const OPERATOR_SAFETY =
  "Este panel no envía correos ni aprueba contactos; las acciones comerciales se realizan dentro del ciclo de cada oportunidad.";

function navigateToSection(section: DashboardSection) {
  window.location.hash = dashboardSectionToHash(section);
}

function SummaryCard({
  label,
  value,
  displayValue,
  hint,
  section,
  needsAttention = false,
}: {
  label: string;
  value: number;
  displayValue?: string;
  hint?: string;
  section: DashboardSection;
  /** True when a non-zero count here means something the operator should look at. */
  needsAttention?: boolean;
}) {
  const shown = displayValue ?? String(value);
  const flagged = needsAttention && value > 0;
  return (
    <button
      type="button"
      onClick={() => navigateToSection(section)}
      className={`w-full rounded-xl border bg-[var(--color-card)] px-4 py-4 text-left shadow-sm transition-all duration-150 hover:-translate-y-0.5 hover:border-brand-600/60 hover:shadow-md active:translate-y-0 motion-reduce:transition-none motion-reduce:hover:translate-y-0 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600 ${
        flagged ? "border-amber-200" : "border-[var(--color-border)]"
      }`}
      aria-label={`${label}: ${shown}. Abrir sección.`}
    >
      <div className="flex items-center gap-1.5">
        {flagged ? (
          <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-500" aria-hidden />
        ) : null}
        <p className="text-xs font-medium text-[var(--color-muted)]">{label}</p>
      </div>
      <p className="mt-2 text-3xl font-semibold text-slate-900">{shown}</p>
      {hint ? <p className="mt-1 text-xs text-[var(--color-muted)]">{hint}</p> : null}
      <p className="mt-2 text-xs font-medium text-brand-700">Ver sección →</p>
    </button>
  );
}

export function TodaySummaryPage() {
  const {
    data,
    panelLoading,
    panelError,
    warm,
    procurementStatus,
    commercialDeals,
    catalogProducts,
    leadResearchSummary,
    commercialWorkQueue,
    commercialWorkQueueLoading,
    commercialWorkQueueError,
    loadCommercialWorkQueue,
    loadPanel,
    setContactEmail,
  } = useDashboardData();

  const opportunitySummary = useMemo(
    () => summarizeProcurementStatus(procurementStatus),
    [procurementStatus],
  );

  const counts = useMemo(
    () =>
      computeTodaySummaryCounts(
        warm?.items ?? [],
        opportunitySummary.value,
        commercialDeals?.items ?? [],
      ),
    [warm?.items, opportunitySummary.value, commercialDeals?.items],
  );

  const commercialWorkSummary = useMemo(
    () =>
      commercialWorkQueue
        ? summarizeCommercialWorkQueue(
            commercialWorkQueue,
          )
        : null,
    [commercialWorkQueue],
  );

  const showMainContent = !panelLoading || data != null;

  return (
    <div className="space-y-6">
      {panelLoading && !data ? (
        <div className="space-y-3" role="status" aria-live="polite">
          <div className="h-24 animate-pulse rounded-lg bg-slate-200/80" />
          <div className="h-16 animate-pulse rounded-lg bg-slate-100" />
        </div>
      ) : null}

      {panelError ? (
        <div
          className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-900"
          role="alert"
        >
          <p className="font-medium">No se pudo cargar el estado del operador</p>
          <p className="mt-1 break-words">{panelError}</p>
          <button
            type="button"
            onClick={() => void loadPanel()}
            className="mt-3 rounded-md border border-red-300 bg-white px-3 py-1.5 text-sm font-medium text-red-800 hover:bg-red-50"
          >
            Reintentar
          </button>
        </div>
      ) : null}

      {showMainContent ? (
        <>
          <header>
            <h1 className="text-xl font-semibold text-slate-900">Qué revisar hoy</h1>
            <p className="mt-2 text-sm text-[var(--color-muted)]">
              Prioriza clientes, proveedores, pagos/logística y licitaciones. {OPERATOR_SAFETY}
            </p>
          </header>

          {commercialWorkQueueError ? (
            <div
              className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-950"
              role="alert"
              data-testid="commercial-work-queue-error"
            >
              <p className="font-medium">
                No se pudo cargar el trabajo comercial
              </p>
              <p className="mt-1 break-words">
                {commercialWorkQueueError}
              </p>
              <button
                type="button"
                onClick={() =>
                  void loadCommercialWorkQueue()
                }
                className="mt-3 rounded-md border border-amber-300 bg-white px-3 py-1.5 text-sm font-medium text-amber-900 hover:bg-amber-50"
              >
                Reintentar
              </button>
            </div>
          ) : null}

          <section
            aria-labelledby="today-commercial-work-heading"
            data-testid="today-commercial-work"
          >
            <h2
              id="today-commercial-work-heading"
              className="text-lg font-semibold text-slate-900"
            >
              Trabajo comercial
            </h2>

            <p className="mt-1 text-sm text-[var(--color-muted)]">
              Seguimientos y decisiones humanas pendientes sobre
              oportunidades comerciales.
            </p>

            {commercialWorkQueueLoading &&
            !commercialWorkSummary ? (
              <p
                className="mt-4 text-sm text-[var(--color-muted)]"
                role="status"
              >
                Cargando trabajo comercial…
              </p>
            ) : (
              <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <SummaryCard
                  label="Seguimientos vencidos"
                  value={
                    commercialWorkSummary
                      ?.overdueTasks.length ?? 0
                  }
                  hint="Tareas con fecha anterior a hoy"
                  section="deals"
                  needsAttention
                />

                <SummaryCard
                  label="Para hoy"
                  value={
                    commercialWorkSummary
                      ?.todayTasks.length ?? 0
                  }
                  hint={`${
                    commercialWorkSummary
                      ?.upcomingTasks.length ?? 0
                  } próximos · ${
                    commercialWorkSummary
                      ?.unscheduledTasks.length ?? 0
                  } sin fecha`}
                  section="deals"
                  needsAttention
                />

                <SummaryCard
                  label="Revisión humana"
                  value={
                    commercialWorkSummary
                      ?.reviewCount ?? 0
                  }
                  hint="Oportunidades pendientes de decisión"
                  section="deals"
                  needsAttention
                />

                <SummaryCard
                  label="Cotizaciones por seguir"
                  value={
                    commercialWorkSummary
                      ?.quoteFollowupCount ?? 0
                  }
                  hint="Cotizaciones enviadas aún no descartadas"
                  section="deals"
                  needsAttention
                />
              </div>
            )}
          </section>

          {commercialWorkQueue ? (
            <CommercialWorkQueuePanel
              queue={commercialWorkQueue}
              onSelectContact={setContactEmail}
            />
          ) : null}

          {!opportunitySummary.available ? (
            <div
              className="rounded-xl border border-amber-200 bg-amber-50/90 px-4 py-4 text-sm text-amber-950"
              role="status"
              data-testid="today-procurement-status-unavailable"
            >
              <p className="font-semibold">{ACTIONABLE_OPPORTUNITIES_UNAVAILABLE_HINT}</p>
              <p className="mt-2">No significa que no existan oportunidades.</p>
            </div>
          ) : opportunitySummary.stale ? (
            <div
              className="rounded-xl border border-amber-200 bg-amber-50/90 px-4 py-4 text-sm text-amber-950"
              role="status"
              data-testid="today-procurement-status-stale"
            >
              <p className="font-semibold">{ACTIONABLE_OPPORTUNITIES_STALE_HINT}</p>
            </div>
          ) : null}

          <section aria-labelledby="today-queues-heading">
            <h2 id="today-queues-heading" className="text-lg font-semibold text-slate-900">
              Colas prioritarias
            </h2>
            <p className="mt-1 text-sm text-[var(--color-muted)]">
              Colas priorizadas según correos, oportunidades accionables y señales comerciales
              cargadas.
            </p>
            <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              <SummaryCard
                label="Clientes por responder"
                value={counts.clientOpportunities}
                hint="Oportunidad o respuesta de cliente"
                section="inbox"
              />
              <SummaryCard
                label="Proveedores pendientes"
                value={counts.supplierQuotesFollowups}
                hint="Cotización recibida o seguimiento"
                section="suppliers"
              />
              <SummaryCard
                label="Pagos y logística"
                value={counts.paymentsLogistics}
                hint="Administración de pago o transporte"
                section="payments-logistics"
              />
              <SummaryCard
                label="Negocios en curso"
                value={counts.dealEvidence}
                hint="Hilos ligados a un negocio en curso"
                section="deals"
              />
              <SummaryCard
                label="Bloqueos comerciales"
                value={counts.dealBlockers}
                hint="Negocios con bloqueos de margen"
                section="deals"
                needsAttention
              />
              <SummaryCard
                label={ACTIONABLE_OPPORTUNITIES_LABEL}
                value={counts.actionableOpportunities}
                displayValue={opportunitySummary.available ? undefined : "N/D"}
                hint={
                  !opportunitySummary.available
                    ? ACTIONABLE_OPPORTUNITIES_UNAVAILABLE_HINT
                    : opportunitySummary.stale
                      ? ACTIONABLE_OPPORTUNITIES_STALE_HINT
                      : ACTIONABLE_OPPORTUNITIES_NORMAL_HINT
                }
                section="tenders"
              />
              <SummaryCard
                label="Catálogo"
                value={catalogProducts?.total ?? 0}
                hint="Productos en catálogo operador"
                section="catalogo"
              />
              <SummaryCard
                label="Prospectos en revisión"
                value={leadResearchSummary?.review_count ?? 0}
                hint={
                  leadResearchSummary
                    ? `${leadResearchSummary.net_new_safe} nuevos seguros · revisar historial antes de contactar`
                    : "Sin resumen de prospectos cargado"
                }
                section="prospectos"
              />
            </div>
          </section>
        </>
      ) : null}
    </div>
  );
}
