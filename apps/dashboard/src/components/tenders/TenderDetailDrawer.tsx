import { useEffect, useState } from "react";
import type { LicitacionIntel } from "../../api/institutionIntel/types";
import { institutionIntelAdapter } from "../../api/institutionIntel/adapter";
import { LicitacionIntelBody } from "../institutionIntel/LicitacionIntelCard";
import { EligibilityBadge } from "../institutionIntel/IntelBadges";
import { TenderAnnexUploadPanel } from "./TenderAnnexUploadPanel";

/**
 * Loading -> exactly one of three terminal phases per selected tenderCode:
 *  - "error": the `getLicitacionIntel` request itself rejected (network/5xx/
 *    non-404 API error) — a transport failure, distinct from a healthy "not
 *    actionable" answer.
 *  - "not-found": W1 answered fine but this tender_code is no longer in
 *    current_opportunity_queue (404 from the merged tender-detail route,
 *    which the adapter maps to `null`) — e.g. closed/expired/no longer
 *    eligible. Not an error; W1 is healthy.
 *  - "ready": got a real `LicitacionIntel`. T1-not-published vs
 *    T1-published-but-incomplete are then distinguished *within* that data by
 *    `LicitacionIntelBody` itself (`Availed` status on `terms`/`coverage`).
 */
type DrawerPhase = "loading" | "error" | "not-found" | "ready";

/**
 * Master/detail drawer for one selected tender (right-hand column of
 * TendersPage's `.split` layout). Owns the ONE fetch of the merged W1+T1
 * `GET /operator/procurement/tenders/{tender_code}` response per tender
 * selection — the commercial-term body is `LicitacionIntelBody`, a
 * presentation-only component that receives this drawer's already-fetched
 * data and performs no fetch of its own, so selecting a tender never issues
 * more than one `getLicitacionIntel` request (previously issued two: one
 * here, one inside the old self-fetching `LicitacionIntelCard`).
 *
 * No contact/outreach affordance is rendered anywhere in this component.
 */
export function TenderDetailDrawer({ tenderCode }: { tenderCode: string | null }) {
  const [intel, setIntel] = useState<LicitacionIntel | null>(null);
  const [phase, setPhase] = useState<DrawerPhase>("loading");

  useEffect(() => {
    if (!tenderCode) {
      setIntel(null);
      return;
    }
    let cancelled = false;
    setPhase("loading");
    setIntel(null);
    institutionIntelAdapter
      .getLicitacionIntel(tenderCode)
      .then((data) => {
        if (cancelled) return;
        if (data === null) {
          setPhase("not-found");
          return;
        }
        setIntel(data);
        setPhase("ready");
      })
      .catch(() => {
        if (cancelled) return;
        setPhase("error");
      });
    return () => {
      cancelled = true;
    };
  }, [tenderCode]);

  if (!tenderCode) {
    return (
      <div
        className="flex h-40 items-center justify-center rounded-xl border border-dashed border-[var(--color-border)] bg-[var(--color-card)] px-5 text-center text-sm text-[var(--color-muted)]"
        data-testid="tender-detail-empty"
      >
        Selecciona una licitación de la lista para ver sus términos comerciales.
      </div>
    );
  }

  if (phase === "loading") {
    return (
      <div
        className="h-40 animate-pulse rounded-xl bg-slate-100"
        role="status"
        aria-live="polite"
        data-testid="tender-detail-loading"
      />
    );
  }

  if (phase === "error") {
    return (
      <div
        className="rounded-xl border border-red-200 bg-red-50 px-5 py-4 text-sm text-red-900"
        role="alert"
        data-testid="tender-detail-error"
      >
        <p className="font-semibold">No se pudo cargar la información de esta licitación</p>
        <p className="mt-1 text-xs text-red-800">
          Hubo un problema de comunicación con el servidor — intenta de nuevo.
        </p>
      </div>
    );
  }

  if (phase === "not-found") {
    return (
      <div
        className="rounded-xl border border-amber-200 bg-amber-50 px-5 py-4 text-sm text-amber-950"
        role="status"
        data-testid="tender-detail-not-actionable"
      >
        <p className="font-semibold">Esta licitación ya no está entre las oportunidades accionables</p>
        <p className="mt-1 text-xs text-amber-900">
          W1 respondió correctamente, pero este código de licitación ya no forma parte de la cola de
          oportunidades accionables (current_opportunity_queue) — puede haberse cerrado, vencido, o dejado
          de cumplir los criterios de elegibilidad.
        </p>
      </div>
    );
  }

  if (!intel) {
    // Unreachable in practice: phase only becomes "ready" together with intel
    // being set in the same then() callback. Kept as a type-safe fallback
    // rather than a non-null assertion below.
    return null;
  }

  return (
    <div
      className="overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] shadow-sm"
      data-testid="tender-detail-drawer"
    >
      <div className="border-b border-[var(--color-border)] px-5 py-4">
        <p className="text-[11px] font-bold uppercase tracking-wide text-brand-700">Licitación · solo lectura</p>
        <h2 className="mt-1 text-lg font-bold tracking-tight text-slate-900">{intel.buyerDisplayName || "—"}</h2>
        <p className="font-mono text-xs text-[var(--color-muted)]">{tenderCode}</p>
        <div className="mt-2.5 flex flex-wrap items-center gap-2">
          <EligibilityBadge status={intel.eligibilityStatus} />
        </div>
      </div>
      <div className="px-5 py-4">
        <LicitacionIntelBody intel={intel} />
      </div>
      <div className="border-t border-[var(--color-border)] px-5 py-4">
        <TenderAnnexUploadPanel tenderCode={tenderCode} />
      </div>
    </div>
  );
}
