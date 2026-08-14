import { useMemo } from "react";
import { useDashboardData } from "../context/DashboardDataContext";
import { dashboardSectionToHash } from "../lib/dashboardHashRoute";
import type { DashboardSection } from "../lib/dashboardNav";
import { isEquipmentFeedUnavailable } from "../lib/equipmentFeedStatus";
import { computeTodaySummaryCounts } from "../lib/todaySummaryCounts";

const READ_ONLY_SAFETY =
  "Solo lectura: este panel no envía correos ni aprueba contactos.";

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
    equipment,
    commercialDeals,
    catalogProducts,
    leadResearchSummary,
    loadPanel,
  } = useDashboardData();

  const equipmentFeedUnavailable = isEquipmentFeedUnavailable(equipment?.meta ?? null);

  const counts = useMemo(
    () =>
      computeTodaySummaryCounts(
        warm?.items ?? [],
        equipment?.items.length ?? 0,
        commercialDeals?.items ?? [],
        equipmentFeedUnavailable,
      ),
    [warm?.items, equipment?.items.length, commercialDeals?.items, equipmentFeedUnavailable],
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
              Prioriza clientes, proveedores, pagos/logística y licitaciones. {READ_ONLY_SAFETY}
            </p>
          </header>

          {equipmentFeedUnavailable ? (
            <div
              className="rounded-xl border border-amber-200 bg-amber-50/90 px-4 py-4 text-sm text-amber-950"
              role="status"
              data-testid="today-equipment-feed-unavailable"
            >
              <p className="font-semibold">Fuente de licitaciones no disponible</p>
              <p className="mt-2">
                La cola de licitaciones/equipos puede estar vacía o desactualizada. Revisa la
                generación de{" "}
                <code className="text-xs">equipment_first_operator_queue</code> desde CLI si
                necesitas actualizar reportes.
              </p>
            </div>
          ) : null}

          <section aria-labelledby="today-queues-heading">
            <h2 id="today-queues-heading" className="text-lg font-semibold text-slate-900">
              Colas prioritarias
            </h2>
            <p className="mt-1 text-sm text-[var(--color-muted)]">
              Colas priorizadas según correos, oportunidades de equipos y señales comerciales
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
                label="Licitaciones / equipos"
                value={counts.tendersEquipment}
                displayValue={equipmentFeedUnavailable ? "N/D" : undefined}
                hint={
                  equipmentFeedUnavailable
                    ? "Fuente de licitaciones no disponible (modo reducido)"
                    : "Cola de oportunidades de equipos"
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
