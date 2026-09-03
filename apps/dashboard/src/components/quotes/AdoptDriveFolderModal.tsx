import { useEffect, useRef, useState, type ReactNode } from "react";
import { createManualSalesOpportunity, fetchSalesOpportunities } from "../../api/commercialOperationsClient";
import { adoptCustomerQuoteDriveFolder, fetchDriveIntakeResolution } from "../../api/customerQuoteClient";
import { describeCustomerQuoteCommandError } from "../../api/customerQuoteErrors";
import type { SalesOpportunityListItem } from "../../api/commercialOperationsTypes";
import type {
  CustomerQuote,
  CustomerQuoteIntakeResolution,
  DrivePendingQuoteItem,
} from "../../api/customerQuoteTypes";
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

function EvidenceBadge({ ok, children }: { ok: boolean; children: ReactNode }) {
  return (
    <p className={`text-xs ${ok ? "text-emerald-700" : "text-amber-700"}`}>
      {ok ? "✓ " : "⚠ "}
      {children}
    </p>
  );
}

/**
 * "Incorporar al CRM" (CRM-Q2B): evidence-based resolution first, blank
 * form only as an explicit fallback. On open, resolves Drive-folder-name +
 * durable-CRM + Gmail evidence (read-only, never mutates anything) and
 * renders it as a confirmation the operator reviews/edits -- never an
 * empty form to fill from scratch. "Cambiar datos" switches to the
 * original manual existing/create-opportunity flow, unchanged, for when
 * the resolved proposal is wrong or nothing resolved at all.
 *
 * document_number is prefilled from evidence for the operator to confirm;
 * quote_number is always a blank, explicit, required input in every mode --
 * no evidence source in this slice can safely resolve it, and it must
 * never be derived from document_number.
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

  const [mode, setMode] = useState<"resolved" | "override">("resolved");
  const [resolution, setResolution] = useState<CustomerQuoteIntakeResolution | null>(null);
  const [resolutionLoading, setResolutionLoading] = useState(false);
  const [resolutionError, setResolutionError] = useState<string | null>(null);

  // Resolved-mode selections, seeded from the resolution once it loads.
  const [selectedOrganizationId, setSelectedOrganizationId] = useState<string | null>(null);
  const [organizationDisplayName, setOrganizationDisplayName] = useState("");
  const [selectedContactId, setSelectedContactId] = useState<string | null>(null);
  // Which contacts[] row is checked in the ambiguous-contact picker --
  // purely a UI selection index (contact_id alone can't disambiguate two
  // Gmail-only candidates, which both carry contact_id: null).
  const [selectedContactIndex, setSelectedContactIndex] = useState<number | null>(null);
  const [contactDisplayName, setContactDisplayName] = useState("");
  const [contactEmail, setContactEmail] = useState("");
  const [opportunityTitle, setOpportunityTitle] = useState("");
  const [existingOpportunityId, setExistingOpportunityId] = useState<string | null>(null);

  // Override-mode state (unchanged from the original manual flow).
  const [tab, setTab] = useState<"existing" | "manual">("existing");
  const [selected, setSelected] = useState<SalesOpportunityListItem | null>(null);
  const [manual, setManual] = useState<ManualForm>(EMPTY_MANUAL_FORM);

  const [documentNumber, setDocumentNumber] = useState(item?.document_identifier ?? "");
  const [quoteNumber, setQuoteNumber] = useState("");
  // Crash-safety, mirroring NuevaCotizacionDialog: once opportunity creation
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
    if (!open || !item) return;

    setDocumentNumber(item.document_identifier ?? "");
    setResolution(null);
    setResolutionError(null);
    setResolutionLoading(true);
    setMode("resolved");

    let cancelled = false;

    void fetchDriveIntakeResolution(item.folder_name)
      .then((result) => {
        if (cancelled) return;
        setResolution(result);

        if (result.document_number_candidate) {
          setDocumentNumber(result.document_number_candidate);
        }

        const org = result.organization;
        if (org) {
          setOrganizationDisplayName(org.display_name);
          if (org.confidence === "confirmed_durable_match") {
            setSelectedOrganizationId(org.organization_id);
          }
        } else {
          setMode("override");
        }

        // Exactly one contact candidate may be proposed automatically.
        // 2+ is ambiguous (whether 2+ durable contacts or 2+ Gmail-only
        // candidates) -- never silently choose the first; the operator
        // picks explicitly from the radio list rendered below.
        if (result.contacts.length === 1) {
          const contact = result.contacts[0];
          setContactDisplayName(contact.display_name ?? "");
          setContactEmail(contact.email ?? "");
          if (contact.confidence === "confirmed_durable_match") {
            setSelectedContactId(contact.contact_id);
          }
        }

        if (result.opportunity) {
          setOpportunityTitle(result.opportunity.title);
          if (result.opportunity.confidence === "confirmed_durable_match") {
            setExistingOpportunityId(result.opportunity.sales_opportunity_id);
          }
        }
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        setResolutionError(describeCustomerQuoteCommandError(reason, "adopt"));
        setMode("override");
      })
      .finally(() => {
        if (!cancelled) setResolutionLoading(false);
      });

    return () => {
      cancelled = true;
    };
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
    setMode("resolved");
    setResolution(null);
    setResolutionError(null);
    setSelectedOrganizationId(null);
    setOrganizationDisplayName("");
    setSelectedContactId(null);
    setSelectedContactIndex(null);
    setContactDisplayName("");
    setContactEmail("");
    setOpportunityTitle("");
    setExistingOpportunityId(null);
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

  function switchToOverride() {
    setMode("override");
    setSubmitError(null);
  }

  const hasManualOrganization = manual.organizationName.trim() !== "";
  const hasOverrideOpportunitySource =
    tab === "existing" ? selected !== null : manual.title.trim() !== "" && hasManualOrganization;

  // Ambiguous resolution must fail closed: an operator-visible picker with
  // nothing chosen yet must never leave enough "source" behind to slip
  // through and auto-create a new organization/opportunity.
  const organizationAmbiguous =
    resolution?.organization?.confidence === "possible_match" &&
    resolution.organization.alternates.length > 1;
  const opportunityAmbiguous = resolution?.opportunity?.confidence === "ambiguous_match";

  const hasResolvedOrganizationSource =
    selectedOrganizationId !== null ||
    (!organizationAmbiguous && organizationDisplayName.trim() !== "");

  const hasResolvedOpportunitySource =
    existingOpportunityId !== null ||
    (!opportunityAmbiguous && hasResolvedOrganizationSource);

  const hasOpportunitySource =
    mode === "override" ? hasOverrideOpportunitySource : hasResolvedOpportunitySource;

  const canSubmit =
    hasOpportunitySource &&
    documentNumber.trim() !== "" &&
    quoteNumber.trim() !== "" &&
    !resolutionLoading;

  async function submit() {
    if (!canSubmit || submitting) return;

    setSubmitting(true);
    setSubmitError(null);

    try {
      let opportunityId: string;

      if (mode === "resolved" && existingOpportunityId !== null) {
        opportunityId = existingOpportunityId;
      } else if (mode === "resolved") {
        opportunityId = createdOpportunityId ?? "";
        if (!opportunityId) {
          const created = await createManualSalesOpportunity(
            {
              title: (opportunityTitle.trim() || organizationDisplayName.trim()),
              ...(selectedOrganizationId
                ? { organization_id: selectedOrganizationId }
                : { organization_display_name: organizationDisplayName.trim() }),
              ...(selectedContactId
                ? { contact_id: selectedContactId }
                : contactDisplayName.trim()
                  ? { contact_display_name: contactDisplayName.trim() }
                  : {}),
              ...(contactEmail.trim() ? { contact_email: contactEmail.trim() } : {}),
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
      } else if (tab === "existing") {
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

  const organization = resolution?.organization ?? null;
  const contact = resolution?.contacts[0] ?? null;
  const opportunity = resolution?.opportunity ?? null;

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
        <div className="w-full max-w-lg max-h-[90vh] overflow-y-auto rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] shadow-xl">
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
            {resolutionLoading ? (
              <p className="text-sm text-slate-500">Buscando evidencia en el CRM y en correos…</p>
            ) : null}

            {resolutionError ? (
              <p role="alert" className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                No pudimos buscar evidencia automáticamente ({resolutionError}). Ingresa los datos manualmente.
              </p>
            ) : null}

            <div className="space-y-1">
              <label htmlFor="adopt-document-number" className="text-sm font-medium text-slate-700">
                Documento
              </label>
              <input
                id="adopt-document-number"
                type="text"
                value={documentNumber}
                onChange={(event) => setDocumentNumber(event.target.value)}
                className="w-full rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm text-slate-900"
              />
              {resolution?.document_number_conflict ? (
                <EvidenceBadge ok={false}>Ya existe una cotización con este número de documento.</EvidenceBadge>
              ) : documentNumber.trim() ? (
                <EvidenceBadge ok={true}>Detectado desde Drive</EvidenceBadge>
              ) : null}
            </div>

            {mode === "resolved" ? (
              <div className="space-y-4 rounded-lg border border-[var(--color-border)] bg-slate-50 p-3">
                <div className="space-y-1">
                  <span className="text-sm font-medium text-slate-700">Organización</span>
                  {organization?.confidence === "confirmed_durable_match" ? (
                    <>
                      <p className="text-sm text-slate-900">{organization.display_name}</p>
                      <EvidenceBadge ok={true}>Coincidencia encontrada</EvidenceBadge>
                    </>
                  ) : organization?.confidence === "possible_match" && organization.alternates.length > 1 ? (
                    <div className="space-y-1">
                      <EvidenceBadge ok={false}>Elige la organización correcta</EvidenceBadge>
                      {organization.alternates.map((alternate) => (
                        <label key={alternate.organization_id} className="flex items-center gap-2 text-sm">
                          <input
                            type="radio"
                            name="adopt-organization-alternate"
                            checked={selectedOrganizationId === alternate.organization_id}
                            onChange={() => {
                              setSelectedOrganizationId(alternate.organization_id);
                              setOrganizationDisplayName(alternate.display_name);
                            }}
                          />
                          {alternate.display_name}
                        </label>
                      ))}
                    </div>
                  ) : (
                    <>
                      <input
                        aria-label="Organización"
                        type="text"
                        value={organizationDisplayName}
                        onChange={(event) => setOrganizationDisplayName(event.target.value)}
                        className="w-full rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm text-slate-900"
                      />
                      {organization?.evidence[0] ? (
                        <EvidenceBadge ok={false}>{organization.evidence[0].detail}</EvidenceBadge>
                      ) : (
                        <EvidenceBadge ok={false}>No existe una organización CRM todavía — se creará una nueva</EvidenceBadge>
                      )}
                    </>
                  )}
                </div>

                <div className="space-y-1">
                  <span className="text-sm font-medium text-slate-700">Contacto</span>
                  {resolution && resolution.contacts.length === 1 && resolution.contacts[0].confidence === "confirmed_durable_match" ? (
                    <>
                      <p className="text-sm text-slate-900">{contact?.display_name ?? contact?.email ?? "—"}</p>
                      <EvidenceBadge ok={true}>Contacto existente en el CRM</EvidenceBadge>
                    </>
                  ) : resolution && resolution.contacts.length > 1 ? (
                    <div className="space-y-1">
                      <EvidenceBadge ok={false}>Elige el contacto correcto</EvidenceBadge>
                      {resolution.contacts.map((candidate, index) => (
                        <label
                          key={candidate.contact_id ?? `${candidate.email ?? candidate.display_name ?? "contact"}-${index}`}
                          className="flex items-center gap-2 text-sm"
                        >
                          <input
                            type="radio"
                            name="adopt-contact-alternate"
                            checked={selectedContactIndex === index}
                            onChange={() => {
                              setSelectedContactIndex(index);
                              setContactDisplayName(candidate.display_name ?? "");
                              setContactEmail(candidate.email ?? "");
                              setSelectedContactId(candidate.contact_id);
                            }}
                          />
                          {candidate.display_name ?? candidate.email ?? "Contacto sin nombre"}
                        </label>
                      ))}
                    </div>
                  ) : (
                    <>
                      <input
                        aria-label="Nombre de contacto"
                        type="text"
                        placeholder="Nombre de contacto"
                        value={contactDisplayName}
                        onChange={(event) => setContactDisplayName(event.target.value)}
                        className="mb-1 w-full rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm text-slate-900"
                      />
                      <input
                        aria-label="Correo de contacto"
                        type="email"
                        placeholder="Correo de contacto"
                        value={contactEmail}
                        onChange={(event) => setContactEmail(event.target.value)}
                        className="w-full rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm text-slate-900"
                      />
                      {contact?.evidence[0] ? (
                        <EvidenceBadge ok={false}>{contact.evidence[0].detail}</EvidenceBadge>
                      ) : null}
                    </>
                  )}
                </div>

                <div className="space-y-1">
                  <span className="text-sm font-medium text-slate-700">Oportunidad</span>
                  {existingOpportunityId !== null ? (
                    <>
                      <p className="text-sm text-slate-900">{opportunity?.title}</p>
                      <EvidenceBadge ok={true}>Oportunidad existente encontrada</EvidenceBadge>
                    </>
                  ) : opportunity?.confidence === "ambiguous_match" ? (
                    <div className="space-y-1">
                      <EvidenceBadge ok={false}>Hay varias oportunidades activas — elige la correcta</EvidenceBadge>
                      {opportunity.alternates.map((alternate) => (
                        <label key={alternate.sales_opportunity_id} className="flex items-center gap-2 text-sm">
                          <input
                            type="radio"
                            name="adopt-opportunity-alternate"
                            checked={existingOpportunityId === alternate.sales_opportunity_id}
                            onChange={() => {
                              setExistingOpportunityId(alternate.sales_opportunity_id);
                              setOpportunityTitle(alternate.title);
                            }}
                          />
                          {alternate.title}
                        </label>
                      ))}
                    </div>
                  ) : (
                    <>
                      <p className="text-xs text-slate-500">No existe una oportunidad CRM todavía — se creará automáticamente:</p>
                      <input
                        aria-label="Título de la oportunidad"
                        type="text"
                        value={opportunityTitle}
                        onChange={(event) => setOpportunityTitle(event.target.value)}
                        className="w-full rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm text-slate-900"
                      />
                    </>
                  )}
                </div>

                <button
                  type="button"
                  onClick={switchToOverride}
                  className="text-sm font-medium text-brand-700 hover:text-brand-800"
                >
                  Cambiar datos
                </button>
              </div>
            ) : (
              <div className="space-y-3">
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
                      {picker.visibleItems.map((opp) => (
                        <li key={opp.sales_opportunity_id}>
                          <button
                            type="button"
                            onClick={() => setSelected(opp)}
                            className={`w-full rounded-md border px-3 py-2 text-left text-sm ${
                              selected?.sales_opportunity_id === opp.sales_opportunity_id
                                ? "border-brand-600 bg-brand-50"
                                : "border-[var(--color-border)] bg-white hover:bg-slate-50"
                            }`}
                          >
                            <div className="font-medium text-slate-900">{opp.title}</div>
                            <div className="text-xs text-slate-500">
                              {opp.organization_display_name ?? "—"}
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
                        disabled={!hasManualOrganization}
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
                        disabled={!hasManualOrganization}
                        value={manual.contactEmail}
                        onChange={(event) => setManual({ ...manual, contactEmail: event.target.value })}
                        className="w-full rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm text-slate-900 disabled:bg-slate-50 disabled:text-slate-400"
                      />
                    </div>
                  </div>
                )}
              </div>
            )}

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
              <EvidenceBadge ok={false}>No pudimos determinarlo con seguridad — ingrésalo explícitamente</EvidenceBadge>
              <p className="text-xs text-slate-500">Nunca se deriva del número de documento.</p>
            </div>

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
