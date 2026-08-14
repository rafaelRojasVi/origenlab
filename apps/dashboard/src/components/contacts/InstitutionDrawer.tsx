import type { GmailInteractionAuditSnapshot } from "../../api/gmailInteractionAuditTypes";
import type { CustomerInstitutionGroup } from "../../lib/customerInstitutionGroups";
import { formatDashboardDateTime } from "../../lib/dashboardDateFormat";
import { institutionGmailHistorySummary } from "../../lib/institutionMirrorDepth";
import {
  hasProspectEmail,
  parseRiskFlagChips,
  prospectBuyerTypeLabel,
  prospectCampaignBucketLabel,
  prospectClassificationLabel,
  prospectContactCell,
  prospectSourceTypeLabel,
} from "../../lib/prospectLabels";
import { institutionStatusChips } from "../../lib/customerInstitutionGroups";
import { ContactEmailButton } from "../commercial/ContactEmailButton";

export function InstitutionDrawer({
  group,
  auditSnapshot,
  onClose,
  onSelectEmail,
}: {
  group: CustomerInstitutionGroup;
  auditSnapshot?: GmailInteractionAuditSnapshot | null;
  onClose: () => void;
  onSelectEmail?: (email: string) => void;
}) {
  const chips = institutionStatusChips(group);
  const gmailSummary = institutionGmailHistorySummary(group, auditSnapshot);
  const riskRows = group.rows.flatMap((row) => parseRiskFlagChips(row.risk_flags));

  return (
    <div
      className="fixed inset-0 z-40 flex justify-end bg-black/30"
      role="dialog"
      aria-modal="true"
      aria-labelledby="institution-drawer-title"
      onClick={onClose}
      data-testid="institution-drawer"
    >
      <div
        className="h-full w-full max-w-xl overflow-y-auto border-l border-[var(--color-border)] bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-[var(--color-border)] bg-white px-5 py-4">
          <h2 id="institution-drawer-title" className="text-lg font-semibold text-slate-900">
            Institución compradora
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
          <section data-testid="institution-summary">
            <p className="text-base font-semibold text-slate-900">{group.institutionName}</p>
            {group.domain ? (
              <p className="mt-1 text-sm text-[var(--color-muted)]">{group.domain}</p>
            ) : null}
            <dl className="mt-3 grid gap-2 text-sm sm:grid-cols-2">
              <div>
                <dt className="text-[var(--color-muted)]">Sector</dt>
                <dd>{group.sectors.join(" · ") || "—"}</dd>
              </div>
              <div>
                <dt className="text-[var(--color-muted)]">Región</dt>
                <dd>{group.regions.join(" · ") || "—"}</dd>
              </div>
              <div>
                <dt className="text-[var(--color-muted)]">Tipo comprador</dt>
                <dd>
                  {group.buyerTypes.map((value) => prospectBuyerTypeLabel(value)).join(" · ") || "—"}
                </dd>
              </div>
              <div>
                <dt className="text-[var(--color-muted)]">Score máximo</dt>
                <dd>{group.maxFinalScore}</dd>
              </div>
            </dl>
            {chips.length ? (
              <ul className="mt-3 flex flex-wrap gap-2" data-testid="institution-status-chips">
                {chips.map((chip) => (
                  <li
                    key={chip.code}
                    className={`rounded-full border px-2.5 py-0.5 text-xs font-semibold ${chip.className}`}
                  >
                    {chip.label}
                  </li>
                ))}
              </ul>
            ) : null}
          </section>

          <section data-testid="institution-contacts">
            <h3 className="text-sm font-semibold text-slate-900">Contactos y correos</h3>
            <div className="mt-2 overflow-x-auto rounded-lg border border-[var(--color-border)]">
              <table className="min-w-full text-left text-sm">
                <thead className="border-b border-[var(--color-border)] bg-slate-50 text-xs uppercase text-[var(--color-muted)]">
                  <tr>
                    <th className="px-3 py-2">Contacto</th>
                    <th className="px-3 py-2">Score</th>
                    <th className="px-3 py-2">Estado</th>
                  </tr>
                </thead>
                <tbody>
                  {group.rows.map((row) => (
                    <tr key={row.prospect_key} className="border-b border-[var(--color-border)]">
                      <td className="px-3 py-2">
                        {hasProspectEmail(row) && onSelectEmail ? (
                          <ContactEmailButton
                            email={row.email!}
                            label={prospectContactCell(row)}
                            onSelect={onSelectEmail}
                          />
                        ) : (
                          <span>{prospectContactCell(row)}</span>
                        )}
                      </td>
                      <td className="px-3 py-2">{row.final_score}</td>
                      <td className="px-3 py-2 text-xs">
                        {prospectClassificationLabel(row.classification)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section data-testid="institution-gmail-history">
            <h3 className="text-sm font-semibold text-slate-900">Historial Gmail</h3>
            <dl className="mt-2 grid gap-1 text-sm">
              <div>
                <dt className="text-[var(--color-muted)]">En espejo</dt>
                <dd>{gmailSummary.mirrorLine}</dd>
              </div>
              <div>
                <dt className="text-[var(--color-muted)]">Gmail detectado</dt>
                <dd>{gmailSummary.detectedLine}</dd>
              </div>
              <div>
                <dt className="text-[var(--color-muted)]">SQLite/Gmail publicado</dt>
                <dd data-testid="institution-sqlite-audit-line">{gmailSummary.sqliteLine}</dd>
              </div>
              {group.hasGmailHistory ? (
                <>
                  <div>
                    <dt className="text-[var(--color-muted)]">Último contacto</dt>
                    <dd>{formatDashboardDateTime(group.latestGmailLastContactedAt)}</dd>
                  </div>
                  <div>
                    <dt className="text-[var(--color-muted)]">Último asunto (redactado)</dt>
                    <dd>{group.latestSafeSubject ?? "—"}</dd>
                  </div>
                </>
              ) : null}
            </dl>
          </section>

          <section data-testid="institution-prospect-rows">
            <h3 className="text-sm font-semibold text-slate-900">Prospectos / fuentes</h3>
            <ul className="mt-2 space-y-2 text-sm">
              {group.rows.map((row) => (
                <li
                  key={row.prospect_key}
                  className="rounded-md border border-[var(--color-border)] bg-slate-50/60 px-3 py-2"
                >
                  <p className="font-medium">{prospectSourceTypeLabel(row.source_type)}</p>
                  <p className="text-[var(--color-muted)]">
                    {prospectCampaignBucketLabel(row.campaign_bucket)} · {row.status}
                  </p>
                </li>
              ))}
            </ul>
          </section>

          <section data-testid="institution-next-action">
            <h3 className="text-sm font-semibold text-slate-900">Próxima acción recomendada</h3>
            <p className="mt-1 text-sm">{group.recommendedNextAction}</p>
          </section>

          {(group.anyBlocked || group.anyRisk || riskRows.length > 0) && (
            <section data-testid="institution-safety-notes">
              <h3 className="text-sm font-semibold text-slate-900">Seguridad y riesgo</h3>
              {riskRows.length ? (
                <ul className="mt-2 flex flex-wrap gap-2">
                  {riskRows.map((chip) => (
                    <li
                      key={`${chip.code}-${chip.label}`}
                      className="rounded-full border border-amber-200 bg-amber-50 px-2.5 py-0.5 text-xs font-medium text-amber-950"
                    >
                      {chip.label}
                    </li>
                  ))}
                </ul>
              ) : null}
              {group.anyBlocked ? (
                <p className="mt-2 text-sm text-red-900">Hay prospectos bloqueados en esta institución.</p>
              ) : null}
            </section>
          )}

          <p
            className="rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 text-xs text-sky-950"
            data-testid="institution-readonly-note"
          >
            No enviar desde este panel. Revisar historial antes de contactar.
          </p>
        </div>
      </div>
    </div>
  );
}
