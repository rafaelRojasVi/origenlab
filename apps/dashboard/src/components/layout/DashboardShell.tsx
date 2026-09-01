import { useState, type ReactNode } from "react";
import { getOperatorApiBaseUrl } from "../../api/operatorClient";
import { useDashboardData } from "../../context/DashboardDataContext";
import { dashboardSectionLabel, type DashboardSection } from "../../lib/dashboardNav";
import { backendChipClass, backendLabel, verdictTone } from "../../lib/verdictStyles";
import { DevLegacyPortWarning } from "../operator/DevLegacyPortWarning";
import { ContactProfilePanel } from "../commercial/ContactProfilePanel";
import { DashboardSidebar } from "./DashboardSidebar";

export function DashboardShell({
  section,
  onNavigate,
  children,
}: {
  section: DashboardSection;
  onNavigate: (section: DashboardSection) => void;
  children: ReactNode;
}) {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const {
    data,
    mirrorBackend,
    backend,
    devConfigWarning,
    refreshing,
    loadAll,
    contactEmail,
    setContactEmail,
  } = useDashboardData();

  const pageTitle = dashboardSectionLabel(section);
  const verdict = data?.operator.verdict;
  const apiBase = getOperatorApiBaseUrl() || "(proxy Vite)";

  return (
    <div className="flex min-h-screen bg-[var(--color-surface)]">
      <DashboardSidebar
        active={section}
        collapsed={sidebarCollapsed}
        onNavigate={onNavigate}
        onToggleCollapsed={() => setSidebarCollapsed((value) => !value)}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="border-b border-[var(--color-border)] bg-[var(--color-card)]/95 shadow-sm backdrop-blur-sm">
          <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span
                  className="inline-flex items-center rounded-full bg-slate-50 px-2 py-0.5 text-[10px] font-medium text-slate-600 ring-1 ring-slate-200"
                  data-testid="operator-center-chip"
                >
                  Centro operador
                </span>
              </div>
              <h1 className="truncate text-lg font-semibold text-slate-900 sm:text-xl">
                {pageTitle}
              </h1>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {verdict ? (
                <span
                  className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold ${verdictTone(verdict).badge}`}
                  data-testid="operator-verdict-chip"
                >
                  Estado: {verdictTone(verdict).label}
                </span>
              ) : null}
              {data ? (
                <span
                  className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ring-inset ${backendChipClass(data.health.backend)}`}
                >
                  {backendLabel(data.health.backend)}
                </span>
              ) : null}
              <button
                type="button"
                onClick={loadAll}
                disabled={refreshing}
                className="rounded-lg bg-brand-600 px-3.5 py-1.5 text-sm font-medium text-white shadow-sm transition-all hover:bg-brand-700 active:scale-95 disabled:opacity-50 disabled:active:scale-100 motion-reduce:transition-none motion-reduce:active:scale-100"
              >
                {refreshing ? "Actualizando…" : "Actualizar"}
              </button>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-[var(--color-border)]/70 px-4 py-1.5 text-[11px] text-[var(--color-muted)] sm:px-6">
            <span>Centro de comando operador</span>
            <span aria-hidden>·</span>
            <span>
              {section === "tenders"
                ? "No envía correos ni modifica datos comerciales"
                : section === "pipeline"
                  ? "No envía correos · los cambios de etapa quedan registrados en el CRM"
                  : section === "deals"
                    ? "No envía correos · promover a Ventas es la única escritura"
                    : "No envía correos ni modifica datos"}
            </span>
            <span className="hidden sm:inline" aria-hidden>
              ·
            </span>
            <code
              className="hidden rounded bg-slate-50 px-1.5 py-0.5 text-[10px] text-slate-700 ring-1 ring-slate-200 sm:inline"
              title="Base URL del API"
            >
              API {apiBase}
            </code>
          </div>
        </header>

        <main className="flex-1 px-4 py-5 sm:px-6 lg:px-8">
          <div key={section} className="mx-auto w-full max-w-[1600px] space-y-5 animate-fade-in-up">
            {devConfigWarning ? <DevLegacyPortWarning message={devConfigWarning} /> : null}
            {children}
          </div>
        </main>
      </div>

      <ContactProfilePanel
        email={contactEmail}
        open={contactEmail !== null}
        onClose={() => setContactEmail(null)}
        backend={backend}
        mirrorBackend={Boolean(mirrorBackend)}
      />
    </div>
  );
}
