import type { WarmCaseItem } from "../../api/commercialTypes";
import { formatDashboardDateTime } from "../../lib/dashboardDateFormat";
import { dashboardSectionToHash } from "../../lib/dashboardHashRoute";
import type { DashboardSection } from "../../lib/dashboardNav";
import { buildWarmCaseDetailView } from "../../lib/warmCaseDetailStrategy";
import { TokenLabel } from "../operator/TokenLabel";
import { ContactEmailButton } from "./ContactEmailButton";
import { CopyTextButton } from "./CopyTextButton";

export function CaseDetailDrawer({
  item,
  open,
  onClose,
  onContactSelect,
}: {
  item: WarmCaseItem | null;
  open: boolean;
  onClose: () => void;
  onContactSelect: (email: string) => void;
}) {
  if (!open || !item) {
    return null;
  }

  const detail = buildWarmCaseDetailView(item);

  const goToSection = (section: DashboardSection) => {
    window.location.hash = dashboardSectionToHash(section);
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="presentation">
      <button
        type="button"
        className="absolute inset-0 bg-slate-900/30"
        aria-label="Cerrar detalle del caso"
        onClick={onClose}
      />
      <aside
        className="relative z-10 flex h-full w-full max-w-lg flex-col border-l border-[var(--color-border)] bg-[var(--color-card)] shadow-xl"
        role="dialog"
        aria-labelledby="case-detail-heading"
        aria-modal="true"
      >
        <header className="flex items-start justify-between gap-3 border-b border-[var(--color-border)] px-4 py-4">
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase tracking-wide text-brand-600">
              Caso tibio · solo lectura
            </p>
            <h2 id="case-detail-heading" className="mt-1 text-lg font-semibold text-slate-900">
              {detail.caseTitle}
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="shrink-0 rounded-md border border-[var(--color-border)] px-2 py-1 text-sm text-slate-700 hover:bg-slate-50"
          >
            Cerrar
          </button>
        </header>

        <div className="flex-1 space-y-5 overflow-y-auto px-4 py-4">
          <div className="flex flex-wrap gap-2">
            <TokenLabel token={item.category} kind="warm_category" />
            <TokenLabel token={item.status} kind="warm_status" />
          </div>

          <section className="space-y-2">
            <h3 className="text-sm font-semibold text-slate-800">Contacto</h3>
            <div className="flex flex-wrap items-center gap-2">
              <ContactEmailButton email={item.contact_email} onSelect={onContactSelect} />
              <CopyTextButton label="Copiar correo" value={item.contact_email} />
            </div>
            <p className="text-sm text-slate-700">
              <span className="text-[var(--color-muted)]">Organización:</span>{" "}
              {item.account_name?.trim() || "—"}
            </p>
            {item.last_seen_at ? (
              <p className="text-xs text-[var(--color-muted)]">
                Última actividad: {formatDashboardDateTime(item.last_seen_at)}
              </p>
            ) : null}
          </section>

          <section className="space-y-2">
            <h3 className="text-sm font-semibold text-slate-800">Contexto</h3>
            <p className="text-sm font-medium text-slate-800">{detail.safeSubject}</p>
            {detail.safeSnippet ? (
              <p className="text-sm text-[var(--color-muted)]">{detail.safeSnippet}</p>
            ) : (
              <p className="text-sm text-[var(--color-muted)]">Sin vista previa disponible.</p>
            )}
            <p className="text-sm text-slate-700">
              <span className="text-[var(--color-muted)]">Señal de equipo:</span>{" "}
              {detail.equipmentSignal}
            </p>
          </section>

          <section className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-3 space-y-2">
            <h3 className="text-sm font-semibold text-slate-900">Qué pasó</h3>
            <p className="text-sm text-slate-800">{detail.inferredSummary}</p>
          </section>

          <section className="rounded-lg border border-brand-200 bg-brand-50/60 px-3 py-3 space-y-2">
            <h3 className="text-sm font-semibold text-slate-900">Estrategia recomendada</h3>
            <p className="text-sm text-slate-800">{detail.recommendedStrategy}</p>
          </section>

          <section className="space-y-1">
            <h3 className="text-sm font-semibold text-slate-800">Próxima acción</h3>
            <p className="text-sm text-slate-800">{detail.nextActionLabel}</p>
          </section>

          {detail.linkedSection && detail.linkedSectionLabel ? (
            <section className="space-y-2">
              <h3 className="text-sm font-semibold text-slate-800">Sección sugerida</h3>
              <button
                type="button"
                className="rounded-md border border-brand-300 bg-white px-3 py-2 text-sm font-medium text-brand-800 hover:bg-brand-50"
                onClick={() => goToSection(detail.linkedSection!)}
              >
                Ir a {detail.linkedSectionLabel}
              </button>
            </section>
          ) : null}
        </div>

        <footer className="border-t border-[var(--color-border)] px-4 py-3 text-xs text-[var(--color-muted)]">
          Resumen de solo lectura · no envía correos · vistas previas redactadas
        </footer>
      </aside>
    </div>
  );
}
