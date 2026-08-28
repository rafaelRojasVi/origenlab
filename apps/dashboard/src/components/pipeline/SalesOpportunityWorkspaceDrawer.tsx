import { useEffect, useRef, useState, type ReactNode } from "react";
import { fetchSalesOpportunity, transitionSalesOpportunityStage } from "../../api/commercialOperationsClient";
import { OperatorApiError } from "../../api/operatorClient";
import type { SalesOpportunity, SalesOpportunityListItem, SalesOpportunityStage } from "../../api/commercialOperationsTypes";
import { formatCommercialOpportunityDate } from "../../lib/commercialOpportunityFormat";
import { StageChangeMenu } from "./StageChangeMenu";
import { SalesOpportunityWorkPanel } from "./SalesOpportunityWorkPanel";

const CONFLICT_MESSAGE =
  "Esta oportunidad cambió en otra sesión. Actualizamos el estado con la versión más reciente.";

/**
 * Merge a freshly fetched server record onto local state, but only if the
 * server record is at least as new as what we already have. A background
 * refresh (or a re-fetch after a 409) can resolve *after* a faster local
 * mutation has already settled `core` to a newer version — merging
 * unconditionally in that ordering would silently revert the drawer to
 * stale stage/version data. Comparing `version` makes the merge a no-op
 * whenever `incoming` is older than what's already on screen.
 */
function mergeIfNewer(
  current: SalesOpportunityListItem | null,
  incoming: SalesOpportunity,
): SalesOpportunityListItem | null {
  if (!current || incoming.version < current.version) return current;
  return { ...current, ...incoming };
}

function DetailRow({ label, children }: { label: string; children: ReactNode }) {
  if (children == null || children === "") return null;
  return (
    <div className="flex flex-col gap-0.5 sm:flex-row sm:gap-2">
      <dt className="shrink-0 text-xs font-medium uppercase tracking-wide text-[var(--color-muted)] sm:w-32">{label}</dt>
      <dd className="min-w-0 break-words text-sm text-slate-800">{children}</dd>
    </div>
  );
}

export function SalesOpportunityWorkspaceDrawer({
  item,
  open,
  onClose,
  onStageChanged,
}: {
  item: SalesOpportunityListItem | null;
  open: boolean;
  onClose: () => void;
  onStageChanged: () => void;
}) {
  const [core, setCore] = useState<SalesOpportunityListItem | null>(item);
  const [stagePending, setStagePending] = useState(false);
  const [stageError, setStageError] = useState<string | null>(null);
  const [mounted, setMounted] = useState(open);
  const [entered, setEntered] = useState(false);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (open && item) {
      setCore(item);
      setStageError(null);

      void fetchSalesOpportunity(item.sales_opportunity_id)
        .then((result) => {
          setCore((current) => mergeIfNewer(current, result.item));
        })
        .catch(() => undefined);
    }
  }, [open, item]);

  useEffect(() => {
    if (!open && !mounted) {
      setCore(null);
    }
  }, [open, mounted]);

  useEffect(() => {
    if (open) {
      setMounted(true);
      const raf = requestAnimationFrame(() => setEntered(true));
      return () => cancelAnimationFrame(raf);
    }

    setEntered(false);
    const timeout = setTimeout(() => setMounted(false), 200);
    return () => clearTimeout(timeout);
  }, [open]);

  // Deps include both [open] and [core]: `onClose` is read via `onCloseRef`
  // so that a parent re-render with a new inline `onClose` (e.g. right
  // after `onStageChanged()` fires) doesn't tear down and re-run this
  // effect — which would re-capture `previouslyFocused` and steal focus
  // back to the close button mid-interaction. But we must also key off `core`
  // to handle persistent-mount usage where the drawer is mounted once with
  // `item=null` and `core` only gets populated by the background fetch;
  // without this, focus runs before the close button exists.
  useEffect(() => {
    if (!open || !core) return;

    previouslyFocused.current = document.activeElement as HTMLElement | null;
    closeButtonRef.current?.focus();

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onCloseRef.current();
    }

    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      previouslyFocused.current?.focus();
    };
  }, [open, core]);

  if (!mounted || !core || core.sales_opportunity_id !== item?.sales_opportunity_id) return null;

  async function changeStage(nextStage: SalesOpportunityStage) {
    if (!core) return;

    setStageError(null);
    setStagePending(true);
    const previous = core;
    setCore({ ...core, stage: nextStage });

    try {
      const updated = await transitionSalesOpportunityStage(core.sales_opportunity_id, {
        stage: nextStage,
        expected_version: core.version,
      });

      setCore((current) =>
        current
          ? { ...current, stage: updated.stage, version: updated.version, updated_at: updated.updated_at, stage_updated_at: updated.updated_at }
          : current,
      );
      onStageChanged();
    } catch (reason: unknown) {
      setCore(previous);
      setStageError(
        reason instanceof OperatorApiError && reason.status === 409
          ? CONFLICT_MESSAGE
          : reason instanceof Error
            ? reason.message
            : "No pudimos cambiar la etapa. Reintenta.",
      );

      // `previous` is exactly the stale state that caused this failure (a
      // 409 in particular). The conflict message claims we've updated to
      // the latest version, so make that true: pull the real current
      // record so a retry sends a fresh `expected_version` instead of
      // repeating the same conflict. Best-effort — if this also fails,
      // the rolled-back `previous` state stands.
      try {
        const refreshed = await fetchSalesOpportunity(core.sales_opportunity_id);
        setCore((current) => mergeIfNewer(current, refreshed.item));
      } catch {
        // Ignore — leave the rollback from above in place.
      }
    } finally {
      setStagePending(false);
    }
  }

  return (
    <>
      <button
        type="button"
        className={
          "fixed inset-0 z-40 hidden md:block motion-safe:transition-opacity motion-safe:duration-200 " +
          (entered ? "bg-slate-900/30 pointer-events-auto" : "bg-slate-900/0 pointer-events-none")
        }
        aria-label="Cerrar oportunidad"
        onClick={onClose}
      />

      <aside
        role="dialog"
        aria-modal="false"
        aria-labelledby="sales-opportunity-workspace-heading"
        data-testid="sales-opportunity-workspace-drawer"
        className={
          "mt-4 flex w-full flex-col rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] shadow-sm motion-safe:transition-all motion-safe:duration-200 md:fixed md:inset-y-0 md:right-0 md:z-50 md:mt-0 md:h-full md:max-w-xl md:rounded-none md:border-l md:border-t-0 md:shadow-xl " +
          (entered ? "opacity-100 md:translate-x-0 pointer-events-auto" : "opacity-0 md:translate-x-full pointer-events-none")
        }
      >
        <header className="flex items-start justify-between gap-3 border-b border-[var(--color-border)] px-4 py-4">
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase tracking-wide text-brand-600">Oportunidad de venta</p>
            <h2 id="sales-opportunity-workspace-heading" className="mt-1 text-lg font-semibold text-slate-900">
              {core.account_display_domain ?? core.contact_display_email ?? core.title}
            </h2>
            <p className="mt-1 text-sm text-slate-700">{core.title}</p>
          </div>
          <button
            type="button"
            ref={closeButtonRef}
            onClick={onClose}
            className="shrink-0 rounded-md border border-[var(--color-border)] px-2 py-1 text-sm text-slate-700 hover:bg-slate-50"
          >
            Cerrar
          </button>
        </header>

        <div className="flex-1 space-y-6 overflow-y-auto px-4 py-4">
          {stageError ? (
            <div role="alert" className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
              {stageError}
            </div>
          ) : null}

          <section className="space-y-2">
            <h3 className="text-sm font-semibold text-slate-800">Etapa</h3>
            <StageChangeMenu stage={core.stage} disabled={stagePending} onChange={(next) => void changeStage(next)} />
          </section>

          <dl className="space-y-2">
            <DetailRow label="Responsable">{core.owner_key}</DetailRow>
            <DetailRow label="Contacto">{core.contact_display_email ?? "—"}</DetailRow>
            <DetailRow label="Creada">{formatCommercialOpportunityDate(core.created_at)}</DetailRow>
            <DetailRow label="Actualizada">{formatCommercialOpportunityDate(core.updated_at)}</DetailRow>
          </dl>

          <p className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">
            El sistema sugirió esta oportunidad a partir de evidencia de correo; este registro ahora es de gestión
            humana.
          </p>

          <SalesOpportunityWorkPanel salesOpportunityId={core.sales_opportunity_id} />
        </div>
      </aside>
    </>
  );
}
