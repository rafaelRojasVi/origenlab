import { useState } from "react";
import { InstitutionProfileCard } from "../components/institutionIntel/InstitutionProfileCard";
import { LicitacionIntelCard } from "../components/institutionIntel/LicitacionIntelCard";
import { ProspectQueueList } from "../components/institutionIntel/ProspectQueueList";

type PreviewTab = "licitacion" | "institucion" | "prospectos";

const TABS: { id: PreviewTab; label: string }[] = [
  { id: "licitacion", label: "Licitación" },
  { id: "institucion", label: "Institución" },
  { id: "prospectos", label: "Prospectos" },
];

/**
 * Dev-only preview of the institution/prospect-intel components, mock-backed
 * pending backend W1. Not linked from the sidebar nav (see dashboardNav.ts) —
 * reachable only via direct hash navigation (#/intel-preview) so it can't be
 * stumbled into from the real app's normal navigation flow.
 */
export function IntelPreviewPage() {
  const [tab, setTab] = useState<PreviewTab>("licitacion");
  const [institutionId, setInstitutionId] = useState("inst-talca-01");

  return (
    <div className="space-y-5">
      <div className="rounded-lg border border-dashed border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-950">
        <p className="font-semibold">Vista previa de desarrollo — no es parte de la navegación real.</p>
        <p className="mt-1 text-xs text-amber-900">
          Datos mock, pendiente del contrato de API de backend W1. Ver dashboard-data-requirements.md.
        </p>
      </div>

      <div className="flex gap-1.5 border-b border-[var(--color-border)]">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            className={`rounded-t-md px-4 py-2 text-sm font-semibold ${
              tab === t.id
                ? "border-b-2 border-brand-600 text-brand-700"
                : "text-[var(--color-muted)] hover:text-slate-700"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "licitacion" ? (
        <section className="max-w-2xl">
          <h2 className="mb-3 text-lg font-semibold text-slate-900">
            Licitación 1057898-51-LP26 — Hospital Regional de Talca
          </h2>
          <LicitacionIntelCard tenderCode="1057898-51-LP26" />
        </section>
      ) : null}

      {tab === "institucion" ? (
        <section className="max-w-2xl">
          <div className="mb-3 flex flex-wrap gap-1.5">
            <button
              type="button"
              onClick={() => setInstitutionId("inst-talca-01")}
              className={`rounded-full px-3 py-1 text-xs font-semibold ${
                institutionId === "inst-talca-01"
                  ? "bg-brand-600 text-white"
                  : "border border-[var(--color-border)] bg-white text-slate-700"
              }`}
            >
              Hospital Regional de Talca
            </button>
          </div>
          <InstitutionProfileCard institutionId={institutionId} />
        </section>
      ) : null}

      {tab === "prospectos" ? (
        <section className="max-w-3xl">
          <ProspectQueueList
            onOpenInstitution={(id) => {
              setInstitutionId(id);
              setTab("institucion");
            }}
          />
        </section>
      ) : null}
    </div>
  );
}
