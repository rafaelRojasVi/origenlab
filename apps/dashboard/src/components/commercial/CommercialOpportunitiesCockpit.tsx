import {
  useEffect,
  useState,
} from "react";
import { fetchCommercialOpportunities } from "../../api/operatorClient";
import type { CommercialOpportunitiesResponse } from "../../api/commercialOpportunitiesTypes";
import {
  COMMERCIAL_OPPORTUNITY_REVIEW_OPTIONS,
  COMMERCIAL_OPPORTUNITY_STAGE_OPTIONS,
  commercialOpportunityReviewLabel,
  commercialOpportunitySourceLabel,
  commercialOpportunityStageLabel,
  commercialOpportunityTokenLabel,
  formatCommercialOpportunityDate,
} from "../../lib/commercialOpportunityFormat";
import { ContactEmailButton } from "./ContactEmailButton";
import { CommercialOpportunityDetailDrawer } from "./CommercialOpportunityDetailDrawer";
import {
  ServerPaginationBar,
  type ServerPageSizeOption,
} from "./ServerPaginationBar";
import { TableSection } from "./TableSection";

export function CommercialOpportunitiesCockpit({
  onSelectContact,
}: {
  onSelectContact: (email: string) => void;
}) {
  const [data, setData] =
    useState<CommercialOpportunitiesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [stage, setStage] = useState("");
  const [reviewStatus, setReviewStatus] = useState("");
  const [limit, setLimit] = useState<ServerPageSizeOption>(25);
  const [offset, setOffset] = useState(0);
  const [refreshKey, setRefreshKey] = useState(0);

  const [selectedOpportunityId, setSelectedOpportunityId] =
    useState<string | null>(null);

  useEffect(() => {
    let active = true;

    setLoading(true);
    setError(null);

    void fetchCommercialOpportunities({
      limit,
      offset,
      canonical_stage: stage || undefined,
      review_status: reviewStatus || undefined,
    })
      .then((result) => {
        if (active) setData(result);
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setError(
          reason instanceof Error
            ? reason.message
            : "No se pudieron cargar las oportunidades.",
        );
        setData(null);
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [limit, offset, stage, reviewStatus, refreshKey]);

  const items = data?.items ?? [];
  const total = data?.meta.total_count ?? 0;
  const effectiveLimit = data?.meta.limit ?? limit;
  const effectiveOffset = data?.meta.offset ?? offset;

  const page =
    Math.floor(effectiveOffset / Math.max(effectiveLimit, 1)) + 1;
  const totalPages = Math.max(
    1,
    Math.ceil(total / Math.max(effectiveLimit, 1)),
  );

  const showEmpty =
    !loading && !error && data !== null && items.length === 0;

  return (
    <>
      <TableSection
        title="Oportunidades comerciales"
        subtitle="Ciclo PR3 derivado de evidencia comercial · separado del registro financiero de negocios."
        dataSourceLabel={
          data
            ? commercialOpportunitySourceLabel(data.meta.data_source)
            : "PR3 · solo lectura"
        }
        loading={loading}
        error={error}
        onRetry={() => setRefreshKey((value) => value + 1)}
        empty={showEmpty}
        emptyMessage="No hay oportunidades para los filtros seleccionados."
        reducedNote="La etapa describe evidencia comercial observada. No constituye aprobación para contactar, enviar correo o modificar estado."
      >
        <div className="space-y-3">
          <div className="flex flex-wrap items-end gap-3 rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] px-3 py-3 shadow-sm">
            <label className="space-y-1 text-xs font-medium text-slate-700">
              <span>Etapa</span>
              <select
                aria-label="Etapa"
                value={stage}
                onChange={(event) => {
                  setStage(event.target.value);
                  setOffset(0);
                }}
                className="block rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm"
              >
                <option value="">Todas</option>
                {COMMERCIAL_OPPORTUNITY_STAGE_OPTIONS.map((value) => (
                  <option key={value} value={value}>
                    {commercialOpportunityStageLabel(value)}
                  </option>
                ))}
              </select>
            </label>

            <label className="space-y-1 text-xs font-medium text-slate-700">
              <span>Revisión</span>
              <select
                aria-label="Revisión"
                value={reviewStatus}
                onChange={(event) => {
                  setReviewStatus(event.target.value);
                  setOffset(0);
                }}
                className="block rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm"
              >
                <option value="">Todas</option>
                {COMMERCIAL_OPPORTUNITY_REVIEW_OPTIONS.map((value) => (
                  <option key={value} value={value}>
                    {commercialOpportunityReviewLabel(value)}
                  </option>
                ))}
              </select>
            </label>

            <button
              type="button"
              onClick={() => setRefreshKey((value) => value + 1)}
              disabled={loading}
              className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
            >
              {loading ? "Actualizando…" : "Actualizar"}
            </button>

            {data ? (
              <p className="ml-auto text-xs text-[var(--color-muted)]">
                {total.toLocaleString("es-CL")} oportunidad(es)
              </p>
            ) : null}
          </div>

          {!loading && !error && items.length ? (
            <div className="overflow-x-auto rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] shadow-sm">
              <table className="min-w-full text-left text-sm">
                <thead className="bg-slate-50 text-xs uppercase tracking-wide text-[var(--color-muted)]">
                  <tr>
                    <th className="px-3 py-2 font-medium">
                      Cuenta / contacto
                    </th>
                    <th className="px-3 py-2 font-medium">Etapa</th>
                    <th className="px-3 py-2 font-medium">Revisión</th>
                    <th className="px-3 py-2 font-medium">
                      Última actividad
                    </th>
                    <th className="px-3 py-2 font-medium">Origen</th>
                    <th className="px-3 py-2 text-right font-medium">
                      Detalle
                    </th>
                  </tr>
                </thead>

                <tbody>
                  {items.map((item) => (
                    <tr
                      key={item.opportunity_id}
                      className="border-t border-[var(--color-border)] hover:bg-slate-50/80"
                    >
                      <td className="px-3 py-2 align-top">
                        <p className="font-medium text-slate-900">
                          {item.account_display_domain ?? "Sin dominio"}
                        </p>

                        {item.contact_display_email ? (
                          <div className="mt-1 text-xs">
                            <ContactEmailButton
                              email={item.contact_display_email}
                              onSelect={onSelectContact}
                            />
                          </div>
                        ) : (
                          <p className="mt-1 text-xs text-[var(--color-muted)]">
                            Sin contacto visible
                          </p>
                        )}
                      </td>

                      <td className="px-3 py-2 align-top">
                        <p
                          className="font-medium text-slate-900"
                          title={item.canonical_stage}
                        >
                          {commercialOpportunityStageLabel(
                            item.canonical_stage,
                          )}
                        </p>
                        <p className="mt-1 text-xs text-[var(--color-muted)]">
                          {commercialOpportunityTokenLabel(
                            item.stage_confidence,
                          )}
                        </p>
                        <p
                          className={`mt-1 text-xs font-medium ${
                            item.stage_is_current
                              ? "text-emerald-700"
                              : "text-amber-700"
                          }`}
                        >
                          {item.stage_is_current
                            ? "Etapa vigente"
                            : "No marcada como etapa vigente"}
                        </p>
                      </td>

                      <td className="px-3 py-2 align-top text-xs">
                        <span title={item.review_status}>
                          {commercialOpportunityReviewLabel(
                            item.review_status,
                          )}
                        </span>
                      </td>

                      <td className="whitespace-nowrap px-3 py-2 align-top text-xs text-slate-700">
                        {formatCommercialOpportunityDate(
                          item.last_activity_at,
                        )}
                      </td>

                      <td className="px-3 py-2 align-top text-xs text-slate-700">
                        <p>
                          {commercialOpportunityTokenLabel(
                            item.source_kind,
                          )}
                        </p>
                        <p
                          className="mt-1 max-w-[14rem] truncate font-mono text-[10px] text-[var(--color-muted)]"
                          title={item.source_key}
                        >
                          {item.source_key}
                        </p>
                      </td>

                      <td className="px-3 py-2 text-right align-top">
                        <button
                          type="button"
                          onClick={() =>
                            setSelectedOpportunityId(
                              item.opportunity_id,
                            )
                          }
                          className="rounded-md border border-brand-200 bg-brand-50 px-2.5 py-1 text-xs font-medium text-brand-800 hover:bg-brand-100"
                        >
                          Ver ciclo
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>

              <ServerPaginationBar
                page={page}
                totalPages={totalPages}
                pageSize={limit}
                onPageChange={(nextPage) => {
                  setOffset((nextPage - 1) * limit);
                }}
                onPageSizeChange={(nextLimit) => {
                  setLimit(nextLimit);
                  setOffset(0);
                }}
              />

              <p
                className="border-t border-[var(--color-border)] px-3 py-2 text-xs text-[var(--color-muted)]"
                data-testid="commercial-opportunities-footer"
              >
                Mostrando {effectiveOffset + 1}–
                {Math.min(effectiveOffset + items.length, total)} de{" "}
                {total.toLocaleString("es-CL")} · paginación del servidor
              </p>
            </div>
          ) : null}
        </div>
      </TableSection>

      <CommercialOpportunityDetailDrawer
        opportunityId={selectedOpportunityId}
        open={selectedOpportunityId !== null}
        onClose={() => setSelectedOpportunityId(null)}
        onSelectContact={onSelectContact}
      />
    </>
  );
}
