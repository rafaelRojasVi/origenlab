import { useEffect, useRef, useState } from "react";
import { createManualSalesOpportunity } from "../../api/commercialOperationsClient";
import { createCustomerQuote } from "../../api/customerQuoteClient";
import type { SalesOpportunity, SalesOpportunityListItem } from "../../api/commercialOperationsTypes";
import type { CustomerQuoteGlobalItem } from "../../api/customerQuoteTypes";
import { newIdempotencyKey } from "../../lib/idempotencyKey";
import { createErrorMessage } from "./driveWorkspaceUi";
import { useExistingOpportunityPicker } from "./useExistingOpportunityPicker";

interface ManualForm {
  title: string;
  organizationName: string;
  contactName: string;
  contactEmail: string;
}

const EMPTY_MANUAL_FORM: ManualForm = {
  title: "",
  organizationName: "",
  contactName: "",
  contactEmail: "",
};

function assembleCreatedItem(
  quote: CustomerQuoteGlobalItem["quote"],
  identity: {
    organization_display_name: string | null;
    contact_display_name: string | null;
    contact_primary_email: string | null;
  },
  context: {
    stage: CustomerQuoteGlobalItem["sales_opportunity_stage"];
    owner_key: string;
    next_task_title: string | null;
    next_task_due_at: string | null;
  },
): CustomerQuoteGlobalItem {
  return {
    quote,
    sales_opportunity_stage: context.stage,
    sales_opportunity_owner_key: context.owner_key,
    organization_display_name: identity.organization_display_name,
    contact_display_name: identity.contact_display_name,
    contact_primary_email: identity.contact_primary_email,
    next_task_title: context.next_task_title,
    next_task_due_at: context.next_task_due_at,
  };
}

export function NuevaCotizacionDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (item: CustomerQuoteGlobalItem) => void;
}) {
  const picker = useExistingOpportunityPicker(open);

  const [tab, setTab] = useState<"existing" | "manual">("existing");
  const [selected, setSelected] = useState<SalesOpportunityListItem | null>(null);
  const [manual, setManual] = useState<ManualForm>(EMPTY_MANUAL_FORM);
  // Crash-safety: once the manual-create command succeeds, its result is
  // cached here so a retry after a quote-creation failure only replays the
  // quote step — it must never call createManualSalesOpportunity again.
  const [createdOpportunity, setCreatedOpportunity] = useState<SalesOpportunity | null>(null);
  const [opportunityKey, setOpportunityKey] = useState(() => newIdempotencyKey("opportunity"));
  const [quoteKey, setQuoteKey] = useState(() => newIdempotencyKey("quote"));
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!open) return;

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") handleClose();
    }

    document.addEventListener("keydown", onKeyDown);
    closeButtonRef.current?.focus();
    return () => document.removeEventListener("keydown", onKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  if (!open) return null;

  function resetState() {
    setTab("existing");
    setSelected(null);
    setManual(EMPTY_MANUAL_FORM);
    setCreatedOpportunity(null);
    setSubmitError(null);
    setOpportunityKey(newIdempotencyKey("opportunity"));
    setQuoteKey(newIdempotencyKey("quote"));
  }

  function handleClose() {
    resetState();
    onCloseRef.current();
  }

  const hasOrganization = manual.organizationName.trim() !== "";
  const canSubmit =
    tab === "existing" ? selected !== null : manual.title.trim() !== "" && hasOrganization;

  async function submit() {
    if (!canSubmit || submitting) return;

    setSubmitting(true);
    setSubmitError(null);

    try {
      let opportunityId: string;
      let identity: {
        organization_display_name: string | null;
        contact_display_name: string | null;
        contact_primary_email: string | null;
      };
      let context: {
        stage: CustomerQuoteGlobalItem["sales_opportunity_stage"];
        owner_key: string;
        next_task_title: string | null;
        next_task_due_at: string | null;
      };

      if (tab === "existing") {
        opportunityId = selected!.sales_opportunity_id;
        identity = {
          organization_display_name: selected!.organization_display_name,
          contact_display_name: selected!.contact_display_name,
          contact_primary_email: selected!.contact_primary_email,
        };
        context = {
          stage: selected!.stage,
          owner_key: selected!.owner_key,
          next_task_title: selected!.next_task_title,
          next_task_due_at: selected!.next_task_due_at,
        };
      } else {
        let opportunity = createdOpportunity;
        if (!opportunity) {
          opportunity = await createManualSalesOpportunity(
            {
              title: manual.title.trim(),
              organization_display_name: manual.organizationName.trim(),
              ...(manual.contactName.trim() ? { contact_display_name: manual.contactName.trim() } : {}),
              ...(manual.contactEmail.trim() ? { contact_email: manual.contactEmail.trim() } : {}),
            },
            opportunityKey,
          );
          // Durably created even if the quote step below now fails — never
          // re-create it on a retry of this submit.
          setCreatedOpportunity(opportunity);
        }
        opportunityId = opportunity.sales_opportunity_id;
        identity = {
          organization_display_name: manual.organizationName.trim(),
          contact_display_name: manual.contactName.trim() || null,
          contact_primary_email: manual.contactEmail.trim() || null,
        };
        context = {
          stage: opportunity.stage,
          owner_key: opportunity.owner_key,
          next_task_title: null,
          next_task_due_at: null,
        };
      }

      const quote = await createCustomerQuote(opportunityId, quoteKey);
      const item = assembleCreatedItem(quote, identity, context);
      resetState();
      onCreated(item);
    } catch (reason: unknown) {
      setSubmitError(createErrorMessage(reason));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <button
        type="button"
        className="fixed inset-0 z-40 bg-slate-900/30"
        aria-label="Cerrar Nueva Cotización"
        onClick={handleClose}
      />

      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="nueva-cotizacion-heading"
        data-testid="nueva-cotizacion-dialog"
        className="fixed inset-0 z-50 flex items-center justify-center p-4"
      >
        <div className="w-full max-w-lg rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] shadow-xl">
          <header className="flex items-start justify-between gap-3 border-b border-[var(--color-border)] px-4 py-4">
            <h2 id="nueva-cotizacion-heading" className="text-lg font-semibold text-slate-900">
              Nueva Cotización
            </h2>
            <button
              type="button"
              ref={closeButtonRef}
              onClick={handleClose}
              className="shrink-0 rounded-md border border-[var(--color-border)] px-2 py-1 text-sm text-slate-700 hover:bg-slate-50"
            >
              Cerrar
            </button>
          </header>

          <div className="space-y-4 px-4 py-4">
            <div role="tablist" aria-label="Origen de la oportunidad" className="flex gap-1 border-b border-slate-200">
              {(["existing", "manual"] as const).map((candidate) => (
                <button
                  key={candidate}
                  type="button"
                  role="tab"
                  aria-selected={tab === candidate}
                  onClick={() => setTab(candidate)}
                  className={`rounded-t-md px-3 py-1.5 text-sm font-medium ${
                    tab === candidate
                      ? "border-b-2 border-brand-600 text-brand-800"
                      : "border-b-2 border-transparent text-slate-500 hover:text-slate-700"
                  }`}
                >
                  {candidate === "existing" ? "Oportunidad existente" : "Oportunidad nueva"}
                </button>
              ))}
            </div>

            {tab === "existing" ? (
              <div className="space-y-2">
                <input
                  type="search"
                  aria-label="Buscar oportunidad"
                  placeholder="Buscar por título, cliente o contacto…"
                  value={picker.searchText}
                  onChange={(event) => picker.setSearchText(event.target.value)}
                  className="w-full rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm text-slate-900 placeholder:text-slate-400"
                />
                {picker.error ? (
                  <p role="alert" className="text-sm text-amber-900">{picker.error}</p>
                ) : null}
                <ul className="max-h-56 space-y-1 overflow-y-auto">
                  {picker.visibleItems.map((opportunity) => (
                    <li key={opportunity.sales_opportunity_id}>
                      <button
                        type="button"
                        onClick={() => setSelected(opportunity)}
                        className={`w-full rounded-md border px-3 py-2 text-left text-sm ${
                          selected?.sales_opportunity_id === opportunity.sales_opportunity_id
                            ? "border-brand-600 bg-brand-50"
                            : "border-[var(--color-border)] bg-white hover:bg-slate-50"
                        }`}
                      >
                        <div className="font-medium text-slate-900">{opportunity.title}</div>
                        <div className="text-xs text-slate-500">
                          {opportunity.organization_display_name ?? "—"}
                        </div>
                      </button>
                    </li>
                  ))}
                  {!picker.loading && picker.visibleItems.length === 0 ? (
                    <li className="px-3 py-2 text-sm text-slate-500">Sin oportunidades para mostrar.</li>
                  ) : null}
                </ul>
              </div>
            ) : (
              <div className="space-y-3">
                <div className="space-y-1">
                  <label htmlFor="nueva-cotizacion-title" className="text-sm font-medium text-slate-700">
                    Título
                  </label>
                  <input
                    id="nueva-cotizacion-title"
                    type="text"
                    value={manual.title}
                    onChange={(event) => setManual({ ...manual, title: event.target.value })}
                    className="w-full rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm text-slate-900"
                  />
                </div>
                <div className="space-y-1">
                  <label htmlFor="nueva-cotizacion-org" className="text-sm font-medium text-slate-700">
                    Organización
                  </label>
                  <input
                    id="nueva-cotizacion-org"
                    type="text"
                    value={manual.organizationName}
                    onChange={(event) => setManual({ ...manual, organizationName: event.target.value })}
                    className="w-full rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm text-slate-900"
                  />
                </div>
                <div className="space-y-1">
                  <label htmlFor="nueva-cotizacion-contact" className="text-sm font-medium text-slate-700">
                    Contacto
                  </label>
                  <input
                    id="nueva-cotizacion-contact"
                    type="text"
                    disabled={!hasOrganization}
                    value={manual.contactName}
                    onChange={(event) => setManual({ ...manual, contactName: event.target.value })}
                    className="w-full rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm text-slate-900 disabled:bg-slate-50 disabled:text-slate-400"
                  />
                </div>
                <div className="space-y-1">
                  <label htmlFor="nueva-cotizacion-email" className="text-sm font-medium text-slate-700">
                    Correo
                  </label>
                  <input
                    id="nueva-cotizacion-email"
                    type="email"
                    disabled={!hasOrganization}
                    value={manual.contactEmail}
                    onChange={(event) => setManual({ ...manual, contactEmail: event.target.value })}
                    className="w-full rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm text-slate-900 disabled:bg-slate-50 disabled:text-slate-400"
                  />
                </div>
                <p className="text-xs text-slate-500">
                  Estos campos quedan guardados como contexto de la oportunidad durable — no crean un
                  cliente ni un contacto maestro en el CRM.
                </p>
              </div>
            )}

            {submitError ? (
              <p role="alert" className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                {submitError}
              </p>
            ) : null}
          </div>

          <footer className="flex items-center justify-end gap-2 border-t border-[var(--color-border)] px-4 py-3">
            <button
              type="button"
              onClick={handleClose}
              className="rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
            >
              Cancelar
            </button>
            <button
              type="button"
              onClick={() => void submit()}
              disabled={!canSubmit || submitting}
              className="rounded-md bg-brand-600 px-4 py-1.5 text-sm font-semibold text-white hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {submitting ? "Creando…" : "Crear cotización"}
            </button>
          </footer>
        </div>
      </div>
    </>
  );
}
