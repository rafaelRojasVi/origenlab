import { useMemo } from "react";
import { AutomationHealthCard } from "../components/operator/AutomationHealthCard";
import { DailyCoreRunNote } from "../components/operator/DailyCoreRunNote";
import { OperatorWarningsList } from "../components/operator/OperatorWarningsList";
import { useDashboardData } from "../context/DashboardDataContext";
import { dashboardSectionToHash } from "../lib/dashboardHashRoute";
import { humanizeOperatorWarning } from "../lib/humanizeOperatorWarning";
import {
  ACTIONABLE_OPPORTUNITIES_LABEL,
  ACTIONABLE_OPPORTUNITIES_STALE_HINT,
  ACTIONABLE_OPPORTUNITIES_UNAVAILABLE_HINT,
  summarizeProcurementStatus,
} from "../lib/procurementSummary";
import { backendLabel, verdictTone } from "../lib/verdictStyles";

const WARNINGS_PREVIEW = 5;

function MirrorReadinessNote({
  readiness,
  mirrorBackend,
}: {
  readiness: string;
  mirrorBackend: boolean;
}) {
  if (!mirrorBackend) {
    return (
      <p className="text-sm text-[var(--color-muted)]">
        Preparación de salida: <span className="font-medium text-slate-800">{readiness}</span>
      </p>
    );
  }
  return (
    <p className="text-sm text-[var(--color-muted)]">
      Espejo Postgres / preparación salida:{" "}
      <span className="font-medium text-slate-800">{readiness}</span>
      <span className="mt-1 block text-xs text-sky-800">
        Refleja la última sincronización al espejo Postgres, no la aprobación de envío en SQLite.
      </span>
    </p>
  );
}

function displayWarnings(warnings: string[]): { display: string; parseText: string }[] {
  return warnings.map((warning) => ({
    display: humanizeOperatorWarning(warning),
    parseText: warning,
  }));
}

function StatTile({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] px-4 py-3 shadow-sm transition-all duration-150 hover:-translate-y-0.5 hover:shadow-md motion-reduce:transition-none motion-reduce:hover:translate-y-0">
      <p className="text-xs font-medium text-[var(--color-muted)]">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-slate-900">{value}</p>
      {hint ? <p className="mt-1 text-xs text-[var(--color-muted)]">{hint}</p> : null}
    </div>
  );
}

export function SystemPage() {
  const {
    data,
    warm,
    procurementStatus,
    commercialDeals,
    leadResearchSummary,
    mirrorBackend,
    setContactEmail,
  } = useDashboardData();

  const opportunitySummary = useMemo(
    () => summarizeProcurementStatus(procurementStatus),
    [procurementStatus],
  );

  const tone = data ? verdictTone(data.operator.verdict) : null;
  const warnings = data?.operator.warnings ?? [];
  const warningsMore = Math.max(0, warnings.length - WARNINGS_PREVIEW);
  const displayWarningLines = useMemo(() => displayWarnings(warnings), [warnings]);

  const showProspectPriorHistoryWarning =
    leadResearchSummary != null &&
    (leadResearchSummary.same_domain_review >= 3 || leadResearchSummary.blocked_count >= 5);

  return (
    <div className="space-y-6">
      {data && tone ? (
        <section
          className={`rounded-xl border px-5 py-5 shadow-sm ${tone.banner}`}
          aria-labelledby="system-status-heading"
          data-testid="system-status-section"
        >
          <h2 id="system-status-heading" className="text-lg font-semibold text-slate-900">
            Estado del sistema
          </h2>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <span className={`rounded-md px-3 py-1 text-sm font-bold uppercase tracking-wide ${tone.badge}`}>
              {tone.label}
            </span>
            <span className="text-sm font-medium text-[var(--color-muted)]">Estado operador</span>
          </div>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <p className="text-sm">
              <span className="text-[var(--color-muted)]">Datos del panel:</span>{" "}
              <code className="text-xs">{data.health.mode}</code>
              {" · "}
              {data.health.ok ? "ok" : "degradado"}
            </p>
            {data.operator.campaign_mode ? (
              <p className="text-sm">
                <span className="text-[var(--color-muted)]">Campaña:</span>{" "}
                <span className="font-medium">{data.operator.campaign_mode}</span>
              </p>
            ) : null}
            {data.operator.operator_focus ? (
              <p className="text-sm">
                <span className="text-[var(--color-muted)]">Foco:</span>{" "}
                <span className="font-medium">{data.operator.operator_focus}</span>
              </p>
            ) : null}
            {warnings.length > 0 ? (
              <p className="text-sm">
                <span className="text-[var(--color-muted)]">Advertencias activas:</span>{" "}
                <span className="font-medium">{warnings.length}</span>
              </p>
            ) : null}
          </div>
          <div className="mt-3">
            <MirrorReadinessNote
              readiness={data.operator.outbound_readiness}
              mirrorBackend={mirrorBackend}
            />
          </div>
          <DailyCoreRunNote dailyCoreRun={data.operator.daily_core_run} showSafetyNote={false} />
          {data.health.backend === "sqlite" ? (
            <p className="mt-3 text-sm text-[var(--color-muted)]" role="status">
              El servidor usa SQLite local (la ruta no se muestra en el panel).
            </p>
          ) : null}
        </section>
      ) : null}

      <div className="space-y-3">
        {warnings.length > 0 ? (
          <OperatorWarningsList
            title="Atención"
            subtitle="Revisiones operativas o técnicas que conviene mirar antes de actuar. El panel sigue siendo solo lectura."
            showListSafetyNote={false}
            warnings={displayWarningLines.slice(0, WARNINGS_PREVIEW)}
            moreCount={warningsMore}
            onContactSelect={setContactEmail}
          />
        ) : !showProspectPriorHistoryWarning ? (
          <section className="rounded-lg border border-slate-200 bg-slate-50/80 px-4 py-4">
            <h2 className="text-sm font-semibold text-slate-900">Atención</h2>
            <p className="mt-1 text-sm text-[var(--color-muted)]" role="status">
              Sin advertencias por ahora.
            </p>
          </section>
        ) : null}

        {showProspectPriorHistoryWarning ? (
          <p
            className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-950"
            data-testid="system-prospect-prior-history-warning"
          >
            Hay prospectos con historial previo; revisar antes de contactar.{" "}
            <button
              type="button"
              className="font-medium text-brand-800 underline"
              onClick={() => {
                window.location.hash = dashboardSectionToHash("prospectos");
              }}
            >
              Ver Prospectos
            </button>
          </p>
        ) : null}
      </div>

      <section aria-labelledby="system-activity-heading">
        <h2 id="system-activity-heading" className="text-lg font-semibold text-slate-900">
          Actividad del sistema
        </h2>
        <p className="mt-1 text-sm text-[var(--color-muted)]">
          Volumen total por fuente en esta sesión del panel — no solo lo que requiere acción hoy.
        </p>
        <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <StatTile label="Casos tibios cargados" value={warm?.items.length ?? 0} />
          <StatTile
            label={ACTIONABLE_OPPORTUNITIES_LABEL}
            value={opportunitySummary.available ? opportunitySummary.value : "N/D"}
            hint={
              !opportunitySummary.available
                ? ACTIONABLE_OPPORTUNITIES_UNAVAILABLE_HINT
                : opportunitySummary.stale
                  ? ACTIONABLE_OPPORTUNITIES_STALE_HINT
                  : undefined
            }
          />
          <StatTile
            label="Negocios en espejo"
            value={commercialDeals?.table_available ? commercialDeals.total : "—"}
            hint={commercialDeals?.table_available ? undefined : "aún no sincronizado"}
          />
          <StatTile label="Prospectos en revisión" value={leadResearchSummary?.review_count ?? "—"} />
          <StatTile label="Prospectos bloqueados" value={leadResearchSummary?.blocked_count ?? "—"} />
          <StatTile
            label="Prospectos — total espejo"
            value={leadResearchSummary?.table_available ? leadResearchSummary.total : "—"}
            hint={leadResearchSummary?.table_available ? undefined : "aún no sincronizado"}
          />
        </div>
      </section>

      <section
        className="space-y-3"
        aria-labelledby="system-automation-heading"
        data-testid="system-automation-section"
      >
        <div>
          <h2 id="system-automation-heading" className="text-lg font-semibold text-slate-900">
            Automatización operador
          </h2>
          <p className="mt-1 text-sm text-[var(--color-muted)]">
            Este panel observa la automatización; no ejecuta refresh, mirror ni envíos.
          </p>
        </div>
        <AutomationHealthCard variant="detailed" />
      </section>

      <details className="group rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] px-5 py-4 shadow-sm">
        <summary className="cursor-pointer select-none text-sm font-semibold text-slate-900">
          Referencia y alcance de datos
        </summary>
        <div className="mt-4 space-y-4 text-sm">
          <p className="text-[var(--color-muted)]">
            Servicio API:{" "}
            <span className="font-medium text-slate-700">{data?.health.service ?? "—"}</span>
            {" · "}
            Fuente de datos:{" "}
            <span className="font-medium text-slate-700">
              {data?.health.backend ? backendLabel(data.health.backend) : "—"}
            </span>
          </p>

          <div className="rounded-lg border border-sky-100 bg-sky-50/80 px-4 py-4 text-sky-950">
            <h3 className="font-semibold text-sky-900">Alcance del correo en el panel</h3>
            <ul className="mt-2 list-disc space-y-2 pl-5">
              <li>
                El archivo SQLite operativo conserva el historial completo del buzón (del orden de{" "}
                <strong>216&nbsp;000</strong> mensajes indexados).
              </li>
              <li>
                El subconjunto <strong>canónico de Gmail</strong> para{" "}
                <code className="text-xs">contacto@origenlab.cl</code> es mucho menor (aprox.{" "}
                <strong>1&nbsp;100</strong> mensajes útiles para operación diaria).
              </li>
              <li>
                La cola de casos tibios del dashboard usa casos recientes enriquecidos o filtrados
                desde ese subconjunto canónico — <strong>no</strong> todo el archivo histórico.
              </li>
            </ul>
          </div>

          <p className="text-[var(--color-muted)]">
            Lead intelligence — Fuente: CSV DeepSearch / Phase 10B · Modo: solo lectura · no envía
            correos.
          </p>

          <footer className="border-t border-[var(--color-border)] pt-4 text-xs text-[var(--color-muted)]">
            Rutas de solo lectura: estado del operador, casos tibios, oportunidades accionables,
            negocios comerciales y contactos.
          </footer>
        </div>
      </details>
    </div>
  );
}
