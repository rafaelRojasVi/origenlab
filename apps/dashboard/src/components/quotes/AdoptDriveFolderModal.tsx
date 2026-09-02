import { useEffect, useRef, useState } from "react";
import { createManualSalesOpportunity, fetchSalesOpportunities } from "../../api/commercialOperationsClient";
import { adoptCustomerQuoteDriveFolder } from "../../api/customerQuoteClient";
import { describeCustomerQuoteCommandError } from "../../api/customerQuoteErrors";
import type { SalesOpportunityListItem } from "../../api/commercialOperationsTypes";
import type { CustomerQuote, DrivePendingQuoteItem } from "../../api/customerQuoteTypes";
import { newIdempotencyKey } from "../../lib/idempotencyKey";
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

/**
 * "Incorporar al CRM": attach an existing Drive-only folder to a new
 * durable quote under an existing or newly-created sales opportunity.
 * document_number is prefilled from the conservatively-parsed identifier
 * for the operator to confirm; quote_number is always a blank, explicit
 * input -- neither field is ever derived from the other, and nothing here
 * calls the Drive provider (the folder already exists; only its
 * folder_id/folder_web_url, already known from the drive-pending listing,
 * are recorded).
 */
export function AdoptDriveFolderModal({
  item,
  open,
  onClose,
  onAdopted,
}: {
  item: DrivePendingQuoteItem | null;
  open: boolean;
  onClose: () => void;
  onAdopted: (quote: CustomerQuote) => void;
}) {
  const picker = useExistingOpportunityPicker(open);

  const [tab, setTab] = useState<"existing" | "manual">("existing");
  const [selected, setSelected] = useState<SalesOpportunityListItem | null>(null);
  const [manual, setManual] = useState<ManualForm>(EMPTY_MANUAL_FORM);
  const [documentNumber, setDocumentNumber] = useState(item?.document_identifier ?? "");
  const [quoteNumber, setQuoteNumber] = useState("");
  // Crash-safety, mirroring NuevaCotizacionDialog: once manual creation
  // succeeds its id is cached so a retry never creates a second
  // opportunity, no matter which later step failed.
  const [createdOpportunityId, setCreatedOpportunityId] = useState<string | null>(null);
  const [resolvedOpportunity, setResolvedOpportunity] = useState<SalesOpportunityListItem | null>(null);
  const [opportunityKey, setOpportunityKey] = useState(() => newIdempotencyKey("opportunity"));
  const [adoptKey, setAdoptKey] = useState(() => newIdempotencyKey("adopt"));
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (open) {
      setDocumentNumber(item?.document_identifier ?? "");
    }
  }, [open, item]);

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

  if (!open || !item) return null;

  // Narrowed once, outside any closure: TS cannot carry `item`'s non-null
  // narrowing into the nested `submit` function below (it's a parameter,
  // not a const declared in this scope).
  const driveItem = item;

  function resetState() {
    setTab("existing");
    setSelected(null);
    setManual(EMPTY_MANUAL_FORM);
    setQuoteNumber("");
    setCreatedOpportunityId(null);
    setResolvedOpportunity(null);
    setSubmitError(null);
    setOpportunityKey(newIdempotencyKey("opportunity"));
    setAdoptKey(newIdempotencyKey("adopt"));
  }

  function handleClose() {
    resetState();
    onCloseRef.current();
  }

  const hasOrganization = manual.organizationName.trim() !== "";
  const hasOpportunitySource = tab === "existing" ? selected !== null : manual.title.trim() !== "" && hasOrganization;
  const canSubmit =
    hasOpportunitySource &&
    documentNumber.trim() !== "" &&
    quoteNumber.trim() !== "";

  async function submit() {
    if (!canSubmit || submitting) return;

    setSubmitting(true);
    setSubmitError(null);

    try {
      let opportunityId: string;

      if (tab === "existing") {
        opportunityId = selected!.sales_opportunity_id;
      } else {
        opportunityId = createdOpportunityId ?? "";
        if (!opportunityId) {
          const created = await createManualSalesOpportunity(
            {
              title: manual.title.trim(),
              organization_display_name: manual.organizationName.trim(),
              ...(manual.contactName.trim() ? { contact_display_name: manual.contactName.trim() } : {}),
              ...(manual.contactEmail.trim() ? { contact_email: manual.contactEmail.trim() } : {}),
            },
            opportunityKey,
          );
          opportunityId = created.sales_opportunity_id;
          setCreatedOpportunityId(opportunityId);
        }

        if (!resolvedOpportunity) {
          const refreshed = await fetchSalesOpportunities({
            sourceOpportunityId: [opportunityId],
            limit: 1,
          });
          const resolved = refreshed.items[0] ?? null;
          if (!resolved) {
            throw new Error("No pudimos confirmar la oportunidad creada. Reintenta.");
          }
          setResolvedOpportunity(resolved);
        }
      }

      const quote = await adoptCustomerQuoteDriveFolder(
        opportunityId,
        {
          document_number: documentNumber.trim(),
          quote_number: quoteNumber.trim(),
          folder_id: driveItem.folder_id,
          folder_web_url: driveItem.folder_web_url ?? "",
        },
        adoptKey,
      );

      resetState();
      onAdopted(quote);
    } catch (reason: unknown) {
      setSubmitError(describeCustomerQuoteCommandError(reason, "adopt"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <button
        type="button"
        className="fixed inset-0 z-40 bg-slate-900/30"
        aria-label="Cerrar Incorporar al CRM"
        onClick={handleClose}
      />

      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="adopt-drive-folder-heading"
        data-testid="adopt-drive-folder-modal"
        className="fixed inset-0 z-50 flex items-center justify-center p-4"
      >
        <div className="w-full max-w-lg rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] shadow-xl">
          <header className="flex items-start justify-between gap-3 border-b border-[var(--color-border)] px-4 py-4">
            <div>
              <h2 id="adopt-drive-folder-heading" className="text-lg font-semibold text-slate-900">
                Incorporar al CRM
              </h2>
              <p className="mt-1 text-sm text-slate-500">{item.folder_name}</p>
            </div>
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
            <div className="space-y-1">
              <label htmlFor="adopt-document-number" className="text-sm font-medium text-slate-700">
                Número de documento
              </label>
              <input
                id="adopt-document-number"
                type="text"
                value={documentNumber}
                onChange={(event) => setDocumentNumber(event.target.value)}
                className="w-full rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm text-slate-900"
              />
              <p className="text-xs text-slate-500">
                Identificador detectado en el nombre de la carpeta de Drive — confírmalo o corrígelo.
              </p>
            </div>

            <div className="space-y-1">
              <label htmlFor="adopt-quote-number" className="text-sm font-medium text-slate-700">
                Número de cotización
              </label>
              <input
                id="adopt-quote-number"
                type="text"
                value={quoteNumber}
                onChange={(event) => setQuoteNumber(event.target.value)}
                placeholder="Ej. 01191-24"
                className="w-full rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm text-slate-900"
              />
              <p className="text-xs text-slate-500">
                Nunca se deriva del número de documento — ingrésalo explícitamente.
              </p>
            </div>

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
                  <label htmlFor="adopt-title" className="text-sm font-medium text-slate-700">
                    Título
                  </label>
                  <input
                    id="adopt-title"
                    type="text"
                    value={manual.title}
                    onChange={(event) => setManual({ ...manual, title: event.target.value })}
                    className="w-full rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm text-slate-900"
                  />
                </div>
                <div className="space-y-1">
                  <label htmlFor="adopt-org" className="text-sm font-medium text-slate-700">
                    Organización
                  </label>
                  <input
                    id="adopt-org"
                    type="text"
                    value={manual.organizationName}
                    onChange={(event) => setManual({ ...manual, organizationName: event.target.value })}
                    className="w-full rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm text-slate-900"
                  />
                </div>
                <div className="space-y-1">
                  <label htmlFor="adopt-contact" className="text-sm font-medium text-slate-700">
                    Contacto
                  </label>
                  <input
                    id="adopt-contact"
                    type="text"
                    disabled={!hasOrganization}
                    value={manual.contactName}
                    onChange={(event) => setManual({ ...manual, contactName: event.target.value })}
                    className="w-full rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm text-slate-900 disabled:bg-slate-50 disabled:text-slate-400"
                  />
                </div>
                <div className="space-y-1">
                  <label htmlFor="adopt-email" className="text-sm font-medium text-slate-700">
                    Correo
                  </label>
                  <input
                    id="adopt-email"
                    type="email"
                    disabled={!hasOrganization}
                    value={manual.contactEmail}
                    onChange={(event) => setManual({ ...manual, contactEmail: event.target.value })}
                    className="w-full rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm text-slate-900 disabled:bg-slate-50 disabled:text-slate-400"
                  />
                </div>
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
              {submitting ? "Incorporando…" : "Incorporar al CRM"}
            </button>
          </footer>
        </div>
      </div>
    </>
  );
}
