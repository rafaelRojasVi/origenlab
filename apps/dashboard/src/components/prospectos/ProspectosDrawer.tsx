import { useMemo, useState } from "react";
import type { LeadProspectDetailResponseUi } from "../../api/leadIntelTypes";
import {
  decisionBannerClassName,
  evidenceSourceLabel,
  hasProspectEmail,
  parseRiskFlagChips,
  prospectBuyerTypeLabel,
  prospectCampaignBucketLabel,
  prospectClassificationLabel,
  prospectDecisionBanner,
  prospectSourceTypeLabel,
} from "../../lib/prospectLabels";
import { buildMessagePreview, buildPorQueImporta, shouldShowMessageSection } from "../../lib/prospectMessaging";

function RiskFlagChips({ flags }: { flags: string | null | undefined }) {
  const chips = parseRiskFlagChips(flags);
  if (!chips.length) return null;
  return (
    <ul className="mt-2 flex flex-wrap gap-2" data-testid="prospect-risk-chips">
      {chips.map((chip) => (
        <li
          key={chip.code}
          title={chip.code}
          className="rounded-full border border-amber-200 bg-amber-50 px-2.5 py-0.5 text-xs font-medium text-amber-950"
        >
          {chip.label}
        </li>
      ))}
    </ul>
  );
}

function EvidenceBlock({
  source,
  evidenceUrl,
  evidenceNote,
}: {
  source: string | null | undefined;
  evidenceUrl: string | null | undefined;
  evidenceNote: string | null | undefined;
}) {
  const label = evidenceSourceLabel(source, evidenceUrl);
  if (!evidenceUrl && !evidenceNote) {
    return <p className="mt-1 text-sm text-[var(--color-muted)]">Sin evidencia pública registrada.</p>;
  }
  return (
    <div className="mt-1 space-y-2" data-testid="prospect-evidence-block">
      <p className="text-sm">
        <span className="text-[var(--color-muted)]">Fuente: </span>
        <span className="font-medium">{label}</span>
      </p>
      {evidenceUrl ? (
        <a
          href={evidenceUrl}
          target="_blank"
          rel="noopener noreferrer"
          title={evidenceUrl}
          className="inline-flex items-center rounded-md border border-brand-200 bg-brand-50 px-3 py-1.5 text-sm font-medium text-brand-800 hover:bg-brand-100"
          data-testid="prospect-evidence-link"
        >
          Abrir evidencia
        </a>
      ) : null}
      {evidenceNote ? (
        <p className="text-sm text-[var(--color-muted)]">{evidenceNote}</p>
      ) : null}
    </div>
  );
}

export function ProspectosDrawer({
  detail,
  loading,
  error,
  onClose,
}: {
  detail: LeadProspectDetailResponseUi | null;
  loading: boolean;
  error: string | null;
  onClose: () => void;
}) {
  const [techOpen, setTechOpen] = useState(false);
  const p = detail?.prospect;

  const decision = useMemo(
    () => (p ? prospectDecisionBanner(p.classification, p.is_blocked) : null),
    [p],
  );

  const porQue = useMemo(
    () => (p ? buildPorQueImporta(p, detail) : null),
    [p, detail],
  );

  const messagePreview = useMemo(
    () => (p ? buildMessagePreview(p, detail?.recommendation) : null),
    [p, detail?.recommendation],
  );

  const primaryEvidenceUrl =
    p?.evidence_url ?? detail?.evidence?.[0]?.evidence_url ?? null;
  const primaryEvidenceNote =
    p?.evidence_note ?? detail?.evidence?.[0]?.evidence_note ?? null;
  const primarySource = p?.source ?? detail?.evidence?.[0]?.source ?? null;

  const isGmailOrigin =
    p?.source_type === "gmail_historico" || p?.source_type === "followup_antiguo";
  const isDeepsearchOrigin = !p?.source_type || p.source_type === "deepsearch";

  return (
    <div
      className="fixed inset-0 z-40 flex justify-end bg-black/30"
      role="dialog"
      aria-modal="true"
      aria-labelledby="prospect-drawer-title"
      onClick={onClose}
    >
      <div
        className="h-full w-full max-w-lg overflow-y-auto border-l border-[var(--color-border)] bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-[var(--color-border)] bg-white px-5 py-4">
          <h2 id="prospect-drawer-title" className="text-lg font-semibold text-slate-900">
            Ficha del prospecto
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-[var(--color-border)] px-3 py-1.5 text-sm hover:bg-slate-50"
          >
            Cerrar
          </button>
        </div>

        <div className="space-y-5 px-5 py-5">
          {loading ? <p className="text-sm text-[var(--color-muted)]">Cargando ficha…</p> : null}
          {error ? (
            <p
              className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-900"
              role="alert"
            >
              {error}
            </p>
          ) : null}

          {p && decision ? (
            <>
              <p
                className={`rounded-lg border px-3 py-2 text-sm font-medium ${decisionBannerClassName(decision.tone)}`}
                data-testid={decision.testId}
              >
                {decision.label}
              </p>

              <section data-testid="prospect-origin-section">
                <h3 className="text-sm font-semibold text-slate-900">Origen del prospecto</h3>
                <dl className="mt-2 grid gap-1 text-sm">
                  <div>
                    <dt className="text-[var(--color-muted)]">Origen</dt>
                    <dd data-testid="prospect-source-type-label">
                      {prospectSourceTypeLabel(p.source_type)}
                    </dd>
                  </div>
                  {p.dataset_label ? (
                    <div>
                      <dt className="text-[var(--color-muted)]">Fuente / dataset</dt>
                      <dd>{p.dataset_label}</dd>
                    </div>
                  ) : null}
                </dl>
              </section>

              {isGmailOrigin ? (
                <section data-testid="prospect-gmail-history">
                  <h3 className="text-sm font-semibold text-slate-900">Historial Gmail</h3>
                  <dl className="mt-2 grid gap-1 text-sm">
                    <div>
                      <dt className="text-[var(--color-muted)]">Primer contacto</dt>
                      <dd>{p.gmail_first_contacted_at ?? "—"}</dd>
                    </div>
                    <div>
                      <dt className="text-[var(--color-muted)]">Último contacto</dt>
                      <dd>{p.gmail_last_contacted_at ?? "—"}</dd>
                    </div>
                    <div>
                      <dt className="text-[var(--color-muted)]">Enviados / recibidos</dt>
                      <dd>
                        {p.gmail_sent_count ?? "—"} / {p.gmail_received_count ?? "—"}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-[var(--color-muted)]">Último asunto (redactado)</dt>
                      <dd>{p.gmail_latest_subject_safe ?? "—"}</dd>
                    </div>
                  </dl>
                </section>
              ) : null}

              <section>
                <h3 className="text-sm font-semibold text-slate-900">Organización y contacto</h3>
                <p className="mt-1 text-base font-medium">{p.organization_name}</p>
                <dl className="mt-2 grid gap-1 text-sm">
                  <div>
                    <dt className="text-[var(--color-muted)]">Contacto</dt>
                    <dd>
                      {p.contact_name?.trim() || "—"}
                      {hasProspectEmail(p) ? (
                        <span className="mt-0.5 block text-[var(--color-muted)]">{p.email}</span>
                      ) : (
                        <span className="mt-0.5 block text-amber-900">Sin email — investigar contacto</span>
                      )}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-[var(--color-muted)]">Sector / región</dt>
                    <dd>
                      {[p.sector, p.region].filter(Boolean).join(" · ") || "—"}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-[var(--color-muted)]">Tipo comprador</dt>
                    <dd data-testid="prospect-buyer-type-label">{prospectBuyerTypeLabel(p.buyer_type)}</dd>
                  </div>
                  <div>
                    <dt className="text-[var(--color-muted)]">Score / confianza</dt>
                    <dd>
                      {p.final_score} · {p.confidence ?? "—"}
                    </dd>
                  </div>
                </dl>
              </section>

              <section>
                <h3 className="text-sm font-semibold text-slate-900">Próxima acción</h3>
                <p className="mt-1 text-sm" data-testid="prospect-next-action">
                  {p.recommended_next_action ?? "Revisar ficha y decidir contacto manualmente."}
                </p>
              </section>

              {porQue ? (
                <section data-testid="prospect-por-que-importa">
                  <h3 className="text-sm font-semibold text-slate-900">Por qué importa</h3>
                  {porQue.queHaceEvidencia ? (
                    <div className="mt-2">
                      <p className="text-xs font-semibold uppercase tracking-wide text-[var(--color-muted)]">
                        Qué hace / evidencia
                      </p>
                      <p className="mt-1 text-sm">{porQue.queHaceEvidencia}</p>
                    </div>
                  ) : null}
                  {porQue.necesidadProbable ? (
                    <div className="mt-3">
                      <p className="text-xs font-semibold uppercase tracking-wide text-[var(--color-muted)]">
                        Necesidad probable
                      </p>
                      <p className="mt-1 text-sm">{porQue.necesidadProbable}</p>
                    </div>
                  ) : null}
                  <div className="mt-3">
                    <p className="text-xs font-semibold uppercase tracking-wide text-[var(--color-muted)]">
                      Por qué es interesante
                    </p>
                    <p className="mt-1 text-sm">{porQue.porQueInteresante}</p>
                  </div>
                </section>
              ) : null}

              <section>
                <h3 className="text-sm font-semibold text-slate-900">Ángulo recomendado</h3>
                <p className="mt-1 text-sm">{p.spanish_message_angle ?? "—"}</p>
                {p.product_angle && p.product_angle !== p.spanish_message_angle ? (
                  <p className="mt-2 text-sm text-[var(--color-muted)]">
                    Productos: {p.product_angle.replaceAll(";", " · ")}
                  </p>
                ) : null}
              </section>

              {isDeepsearchOrigin ? (
                <section>
                  <h3 className="text-sm font-semibold text-slate-900">Evidencia pública (DeepSearch)</h3>
                  <EvidenceBlock
                    source={primarySource}
                    evidenceUrl={primaryEvidenceUrl}
                    evidenceNote={primaryEvidenceNote}
                  />
                  <p className="mt-2 text-sm text-[var(--color-muted)]">
                    Sector: {p.sector ?? "—"} · Región: {p.region ?? "—"} · Score: {p.final_score}
                  </p>
                </section>
              ) : (
                <section>
                  <h3 className="text-sm font-semibold text-slate-900">Evidencia</h3>
                  <EvidenceBlock
                    source={primarySource}
                    evidenceUrl={primaryEvidenceUrl}
                    evidenceNote={primaryEvidenceNote}
                  />
                </section>
              )}

              <section>
                <h3 className="text-sm font-semibold text-slate-900">Estado de seguridad</h3>
                <p className="mt-1 text-sm">{prospectClassificationLabel(p.classification)}</p>
                <RiskFlagChips flags={p.risk_flags} />
                {p.block_or_review_reason ? (
                  <p className="mt-2 text-sm text-[var(--color-muted)]">{p.block_or_review_reason}</p>
                ) : null}
                <p className="mt-2 text-xs text-sky-900">No enviar sin revisión humana desde este panel.</p>
              </section>

              {messagePreview ? (
                <section data-testid="prospect-message-section">
                  <h3 className="text-sm font-semibold text-slate-900">{messagePreview.title}</h3>
                  {messagePreview.note ? (
                    <p className="mt-1 text-sm text-[var(--color-muted)]">{messagePreview.note}</p>
                  ) : null}
                  {shouldShowMessageSection(messagePreview) && messagePreview.subject && messagePreview.body ? (
                    <>
                      <p className="mt-2 text-xs text-sky-900">
                        Borrador sugerido — solo vista / copiar. No hay envío desde este panel.
                      </p>
                      <pre
                        className="mt-2 whitespace-pre-wrap rounded-lg border border-[var(--color-border)] bg-slate-50 p-3 text-xs"
                        data-testid="prospect-message-preview"
                      >
                        {messagePreview.subject}
                        {"\n\n"}
                        {messagePreview.body}
                      </pre>
                    </>
                  ) : null}
                </section>
              ) : null}

              <details
                className="rounded-lg border border-[var(--color-border)] bg-slate-50/80"
                open={techOpen}
                onToggle={(e) => setTechOpen((e.target as HTMLDetailsElement).open)}
              >
                <summary className="cursor-pointer px-3 py-2 text-sm font-medium text-slate-800">
                  Detalles técnicos
                </summary>
                <dl className="space-y-2 border-t border-[var(--color-border)] px-3 py-3 text-xs text-slate-700">
                  <div>
                    <dt className="text-[var(--color-muted)]">Clasificación (código)</dt>
                    <dd>{p.classification}</dd>
                  </div>
                  <div>
                    <dt className="text-[var(--color-muted)]">Estado</dt>
                    <dd>{p.status}</dd>
                  </div>
                  <div>
                    <dt className="text-[var(--color-muted)]">Bucket campaña</dt>
                    <dd>{prospectCampaignBucketLabel(p.campaign_bucket)}</dd>
                  </div>
                  <div>
                    <dt className="text-[var(--color-muted)]">Dominio</dt>
                    <dd>{p.domain ?? "—"}</dd>
                  </div>
                  {parseRiskFlagChips(p.risk_flags).length ? (
                    <div>
                      <dt className="text-[var(--color-muted)]">Señales (detalle)</dt>
                      <dd>{parseRiskFlagChips(p.risk_flags).map((c) => c.label).join(" · ")}</dd>
                    </div>
                  ) : null}
                </dl>
              </details>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}
