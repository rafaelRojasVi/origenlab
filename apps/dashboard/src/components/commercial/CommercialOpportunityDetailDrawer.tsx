import {
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { fetchCommercialOpportunityDetail, OperatorApiError } from "../../api/operatorClient";
import type { CommercialOpportunityDetailResponse } from "../../api/commercialOpportunitiesTypes";
import {
  commercialOpportunityReviewLabel,
  commercialOpportunityStageLabel,
  commercialOpportunityTokenLabel,
  formatCommercialOpportunityDate,
} from "../../lib/commercialOpportunityFormat";
import { ContactEmailButton } from "./ContactEmailButton";
import { CommercialOpportunityOperationsPanel } from "./CommercialOpportunityOperationsPanel";
import { promoteSalesOpportunity } from "../../api/commercialOperationsClient";

function DetailRow({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  if (children == null || children === "" || children === "—") return null;

  return (
    <div className="flex flex-col gap-0.5 sm:flex-row sm:gap-2">
      <dt className="shrink-0 text-xs font-medium uppercase tracking-wide text-[var(--color-muted)] sm:w-40">
        {label}
      </dt>
      <dd className="min-w-0 break-words text-sm text-slate-800">
        {children}
      </dd>
    </div>
  );
}

function DrawerSection({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold text-slate-800">{title}</h3>
      {children}
    </section>
  );
}

function PromoteToCrmAction({
  opportunityId,
  suggestedTitle,
  promotedSalesOpportunityId,
  onOpenPipeline,
  onPromoted,
}: {
  opportunityId: string;
  suggestedTitle: string;
  promotedSalesOpportunityId: string | null;
  onOpenPipeline: () => void;
  onPromoted: (sourceOpportunityId: string, salesOpportunityId: string) => void;
}) {
  const [title, setTitle] = useState(suggestedTitle);
  const [selfAssign, setSelfAssign] = useState(true);
  const [ownerOverride, setOwnerOverride] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [localPromotedId, setLocalPromotedId] = useState<string | null>(promotedSalesOpportunityId);

  useEffect(() => {
    setLocalPromotedId(promotedSalesOpportunityId);
  }, [promotedSalesOpportunityId]);

  if (localPromotedId) {
    return (
      <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-3">
        <p className="text-sm font-medium text-emerald-900">Esta oportunidad ya está en el pipeline durable.</p>
        <button
          type="button"
          onClick={onOpenPipeline}
          className="mt-2 rounded-md border border-emerald-300 bg-white px-3 py-1.5 text-sm font-medium text-emerald-800"
        >
          Abrir en Pipeline
        </button>
      </div>
    );
  }

  async function promote() {
    const trimmedTitle = title.trim();
    if (!trimmedTitle) {
      setError("Escribe un título para la oportunidad.");
      return;
    }

    setSaving(true);
    setError(null);

    try {
      const result = await promoteSalesOpportunity(
        {
          source_opportunity_id: opportunityId,
          title: trimmedTitle,
          owner_key: selfAssign ? undefined : ownerOverride.trim() || undefined,
        },
        `promote:${crypto.randomUUID()}`,
      );
      setLocalPromotedId(result.sales_opportunity_id);
      onPromoted(opportunityId, result.sales_opportunity_id);
    } catch (reason: unknown) {
      setError(
        reason instanceof OperatorApiError && reason.status === 409
          ? "Esta oportunidad ya fue promovida al pipeline. Actualiza Negocios para verla marcada."
          : reason instanceof Error
            ? reason.message
            : "No se pudo promover la oportunidad.",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-2 rounded-lg border border-slate-200 bg-slate-50 p-3">
      <p className="text-sm font-medium text-slate-900">Promover a CRM</p>
      <p className="text-xs text-slate-500">Crea el registro durable de esta oportunidad en el pipeline comercial.</p>

      {error ? (
        <div role="alert" className="rounded-md border border-red-200 bg-red-50 px-2 py-1.5 text-xs text-red-800">
          {error}
        </div>
      ) : null}

      <label className="block space-y-1 text-xs font-medium text-slate-700">
        <span>Título</span>
        <input
          aria-label="Título de la oportunidad"
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          maxLength={500}
          className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
        />
      </label>

      <label className="flex items-center gap-2 text-xs text-slate-700">
        <input type="checkbox" checked={selfAssign} onChange={(event) => setSelfAssign(event.target.checked)} />
        Responsable: yo (operador autenticado)
      </label>

      {!selfAssign ? (
        <label className="block space-y-1 text-xs font-medium text-slate-700">
          <span>Asignar a (correo)</span>
          <input
            aria-label="Responsable"
            value={ownerOverride}
            onChange={(event) => setOwnerOverride(event.target.value)}
            maxLength={320}
            className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
          />
        </label>
      ) : null}

      <button
        type="button"
        disabled={saving}
        onClick={() => void promote()}
        className="rounded-md bg-brand-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
      >
        {saving ? "Promoviendo…" : "Promover a CRM"}
      </button>
    </div>
  );
}

function DetailBody({
  detail,
  onSelectContact,
  promotedSalesOpportunityId,
  onOpenPipeline,
  onPromoted,
}: {
  detail: CommercialOpportunityDetailResponse;
  onSelectContact: (email: string) => void;
  promotedSalesOpportunityId: string | null;
  onOpenPipeline: () => void;
  onPromoted: (sourceOpportunityId: string, salesOpportunityId: string) => void;
}) {
  const item = detail.opportunity;

  return (
    <div className="flex-1 space-y-6 overflow-y-auto px-4 py-4">
      <PromoteToCrmAction
        opportunityId={item.opportunity_id}
        suggestedTitle={
          item.account_display_domain
            ? `Oportunidad — ${item.account_display_domain}`
            : item.contact_display_email
              ? `Oportunidad — ${item.contact_display_email}`
              : "Nueva oportunidad"
        }
        promotedSalesOpportunityId={promotedSalesOpportunityId}
        onOpenPipeline={onOpenPipeline}
        onPromoted={onPromoted}
      />

      <CommercialOpportunityOperationsPanel
        opportunityId={item.opportunity_id}
      />

      <DrawerSection title="Estado comercial derivado">
        <dl className="space-y-2">
          <DetailRow label="Etapa">
            {commercialOpportunityStageLabel(item.canonical_stage)}
          </DetailRow>
          <DetailRow label="Etapa fuente">
            {commercialOpportunityTokenLabel(item.source_stage)}
          </DetailRow>
          <DetailRow label="Motivo">
            {commercialOpportunityTokenLabel(item.stage_reason_code)}
          </DetailRow>
          <DetailRow label="Confianza">
            {commercialOpportunityTokenLabel(item.stage_confidence)}
          </DetailRow>
          <DetailRow label="Revisión">
            {commercialOpportunityReviewLabel(item.review_status)}
          </DetailRow>
          <DetailRow label="Vigencia">
            {item.stage_is_current
              ? "Etapa vigente"
              : "No marcada como etapa vigente"}
          </DetailRow>
          <DetailRow label="Terminal">
            {item.stage_is_terminal ? "Sí" : "No"}
          </DetailRow>
          <DetailRow label="Evidencia de etapa">
            {item.stage_evidence_id ?? "—"}
          </DetailRow>
          <DetailRow label="Fecha evidencia">
            {formatCommercialOpportunityDate(item.stage_evidence_at)}
          </DetailRow>
          <DetailRow label="Primera actividad">
            {formatCommercialOpportunityDate(item.first_activity_at)}
          </DetailRow>
          <DetailRow label="Última actividad">
            {formatCommercialOpportunityDate(item.last_activity_at)}
          </DetailRow>
        </dl>
      </DrawerSection>

      <DrawerSection title="Cuenta / contacto">
        <dl className="space-y-2">
          <DetailRow label="Dominio">
            {item.account_display_domain ?? "—"}
          </DetailRow>

          {item.contact_display_email ? (
            <DetailRow label="Contacto">
              <ContactEmailButton
                email={item.contact_display_email}
                onSelect={onSelectContact}
              />
            </DetailRow>
          ) : null}

          <DetailRow label="Vínculo identidad">
            {commercialOpportunityTokenLabel(item.identity_link_status)}
          </DetailRow>
          <DetailRow label="Tipo registro">
            {commercialOpportunityTokenLabel(item.record_kind)}
          </DetailRow>
        </dl>
      </DrawerSection>

      <DrawerSection title={`Eventos (${detail.events.length})`}>
        {detail.events.length ? (
          <ul className="space-y-2">
            {detail.events.map((event) => (
              <li
                key={event.event_id}
                className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2"
              >
                <p className="text-sm font-medium text-slate-900">
                  {commercialOpportunityTokenLabel(
                    event.canonical_event_type,
                  )}
                </p>
                <p className="mt-1 text-xs text-slate-600">
                  {formatCommercialOpportunityDate(event.event_at)}
                  {" · "}
                  {commercialOpportunityTokenLabel(event.confidence)}
                  {event.operator_confirmed
                    ? " · confirmado por operador"
                    : ""}
                </p>
                <p className="mt-1 text-xs text-[var(--color-muted)]">
                  Fuente:{" "}
                  {commercialOpportunityTokenLabel(event.source_table)}
                  {" · registro "}
                  {event.source_record_id}
                  {event.source_email_id !== null
                    ? ` · correo #${event.source_email_id}`
                    : ""}
                  {event.source_attachment_id !== null
                    ? ` · adjunto #${event.source_attachment_id}`
                    : ""}
                </p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-[var(--color-muted)]">
            Sin eventos publicados.
          </p>
        )}
      </DrawerSection>

      <DrawerSection title={`Evidencia (${detail.evidence.length})`}>
        {detail.evidence.length ? (
          <ul className="space-y-2">
            {detail.evidence.map((evidence) => (
              <li
                key={evidence.evidence_id}
                className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2"
              >
                <p className="text-sm font-medium text-slate-900">
                  {commercialOpportunityTokenLabel(
                    evidence.evidence_type,
                  )}
                </p>
                <p className="mt-1 text-xs text-slate-600">
                  {commercialOpportunityTokenLabel(
                    evidence.reason_code,
                  )}
                  {" · "}
                  {commercialOpportunityTokenLabel(
                    evidence.confidence,
                  )}
                </p>
                <p className="mt-1 text-xs text-[var(--color-muted)]">
                  {formatCommercialOpportunityDate(
                    evidence.evidence_at,
                  )}
                </p>
                <p className="mt-1 text-xs text-[var(--color-muted)]">
                  Fuente:{" "}
                  {commercialOpportunityTokenLabel(
                    evidence.source_table,
                  )}
                  {" · registro "}
                  {evidence.source_record_id}
                  {evidence.source_email_id !== null
                    ? ` · correo #${evidence.source_email_id}`
                    : ""}
                  {evidence.source_attachment_id !== null
                    ? ` · adjunto #${evidence.source_attachment_id}`
                    : ""}
                </p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-[var(--color-muted)]">
            Sin evidencia adicional publicada.
          </p>
        )}
      </DrawerSection>

      <DrawerSection title={`Conflictos (${detail.conflicts.length})`}>
        {detail.conflicts.length ? (
          <ul className="space-y-2">
            {detail.conflicts.map((conflict) => (
              <li
                key={conflict.conflict_id}
                className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2"
              >
                <p className="text-sm font-medium text-amber-950">
                  {commercialOpportunityTokenLabel(
                    conflict.conflict_type,
                  )}
                </p>
                <p className="mt-1 text-xs text-amber-900">
                  {commercialOpportunityTokenLabel(
                    conflict.reason_code,
                  )}
                  {" · "}
                  {commercialOpportunityReviewLabel(
                    conflict.review_status,
                  )}
                </p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-[var(--color-muted)]">
            Sin conflictos asociados.
          </p>
        )}
      </DrawerSection>
    </div>
  );
}

export function CommercialOpportunityDetailDrawer({
  opportunityId,
  open,
  onClose,
  onSelectContact,
  promotedSalesOpportunityId,
  onOpenPipeline,
  onPromoted,
}: {
  opportunityId: string | null;
  open: boolean;
  onClose: () => void;
  onSelectContact: (email: string) => void;
  promotedSalesOpportunityId: string | null;
  onOpenPipeline: () => void;
  onPromoted: (sourceOpportunityId: string, salesOpportunityId: string) => void;
}) {
  const [detail, setDetail] =
    useState<CommercialOpportunityDetailResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || !opportunityId) {
      setDetail(null);
      setError(null);
      return;
    }

    let active = true;

    setLoading(true);
    setError(null);
    setDetail(null);

    void fetchCommercialOpportunityDetail(opportunityId)
      .then((result) => {
        if (active) setDetail(result);
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setError(
          reason instanceof Error
            ? reason.message
            : "No se pudo cargar el detalle.",
        );
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [open, opportunityId]);

  if (!open || !opportunityId) return null;

  return (
    <>
      <button
        type="button"
        className="fixed inset-0 z-40 hidden bg-slate-900/30 md:block"
        aria-label="Cerrar detalle de oportunidad"
        onClick={onClose}
      />

      <aside
        role="dialog"
        aria-modal="false"
        aria-labelledby="commercial-opportunity-detail-heading"
        data-testid="commercial-opportunity-detail-drawer"
        className="mt-4 flex w-full flex-col rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] shadow-sm md:fixed md:inset-y-0 md:right-0 md:z-50 md:mt-0 md:h-full md:max-w-xl md:rounded-none md:border-l md:border-t-0 md:shadow-xl"
      >
        <header className="flex items-start justify-between gap-3 border-b border-[var(--color-border)] px-4 py-4">
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase tracking-wide text-brand-600">
              Ciclo comercial · operación
            </p>
            <h2
              id="commercial-opportunity-detail-heading"
              className="mt-1 text-lg font-semibold text-slate-900"
            >
              {detail?.opportunity.account_display_domain ??
                detail?.opportunity.contact_display_email ??
                "Oportunidad comercial"}
            </h2>
            <p className="mt-1 break-all font-mono text-xs text-slate-500">
              {opportunityId}
            </p>
          </div>

          <button
            type="button"
            onClick={onClose}
            className="shrink-0 rounded-md border border-[var(--color-border)] px-2 py-1 text-sm text-slate-700 hover:bg-slate-50"
          >
            Cerrar
          </button>
        </header>

        {loading ? (
          <p className="px-4 py-6 text-sm text-slate-600">
            Cargando ciclo comercial…
          </p>
        ) : null}

        {error ? (
          <div className="m-4 rounded-lg border border-red-200 bg-red-50 px-3 py-3 text-sm text-red-800">
            {error}
          </div>
        ) : null}

        {!loading && !error && detail ? (
          <DetailBody
            detail={detail}
            onSelectContact={onSelectContact}
            promotedSalesOpportunityId={promotedSalesOpportunityId}
            onOpenPipeline={onOpenPipeline}
            onPromoted={onPromoted}
          />
        ) : null}

        <footer className="border-t border-[var(--color-border)] px-4 py-3 text-xs text-[var(--color-muted)]">
          PR3 permanece como evidencia derivada de solo lectura · las
          acciones humanas se guardan por separado · no envía correos ·
          provenance JSON interno no se muestra.
        </footer>
      </aside>
    </>
  );
}
