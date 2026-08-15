import { useState } from "react";
import { InstitutionProfileCard } from "../components/institutionIntel/InstitutionProfileCard";
import { LicitacionIntelCard } from "../components/institutionIntel/LicitacionIntelCard";
import { ProspectQueueList } from "../components/institutionIntel/ProspectQueueList";

type PreviewTab = "licitacion" | "institucion" | "colas-w1";

const TABS: { id: PreviewTab; label: string }[] = [
  { id: "licitacion", label: "Licitación" },
  { id: "institucion", label: "Institución" },
  { id: "colas-w1", label: "Colas W1" },
];

/**
 * Dev-only integration lab for the institution/procurement-queue intel
 * components. Not linked from the sidebar nav (see dashboardNav.ts) —
 * reachable only via direct hash navigation (#/intel-preview) so it can't be
 * stumbled into from the real app's normal navigation flow.
 *
 * Boundary: the "Institución" and "Colas W1" tabs are backed by live W1
 * (`apps/api/docs/INSTITUTION_PROSPECT_API_CONTRACT.md`). The "Licitación"
 * tab's tender commercial terms (T1 / ANEXO) remain mock-backed — W1 does
 * not expose that data yet.
 */
export function IntelPreviewPage() {
  const [tab, setTab] = useState<PreviewTab>("licitacion");
  const [institutionId, setInstitutionId] = useState<string | null>(null);

  return (
    <div className="space-y-5">
      <div className="rounded-lg border border-dashed border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-950">
        <p className="font-semibold">Vista previa de desarrollo — no es parte de la navegación real.</p>
        <p className="mt-1 text-xs text-amber-900">
          Institución y Colas W1 usan datos reales del backend W1. La pestaña Licitación sigue siendo
          una demostración mock hasta que T1 / ANEXO se exponga por API.
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
          {institutionId ? (
            <InstitutionProfileCard institutionId={institutionId} />
          ) : (
            <div className="rounded-xl border border-dashed border-[var(--color-border)] bg-white px-4 py-5 text-sm text-[var(--color-muted)]">
              Selecciona una institución desde la pestaña Colas W1 para abrir su perfil real.
            </div>
          )}
        </section>
      ) : null}

      {tab === "colas-w1" ? (
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
