import { useCallback, useEffect, useMemo, useState } from "react";
import type {
  LeadProspectDetailResponseUi,
  LeadProspectListItemUi,
  LeadProspectsExportQuery,
  LeadProspectsListQuery,
  LeadResearchSummaryUi,
  ProspectExportQueue,
  ProspectOriginFilter,
} from "../api/leadIntelTypes";
import {
  downloadLeadProspectsExportCsv,
  fetchLeadProspectDetailMirror,
  fetchLeadProspectsMirror,
  fetchLeadResearchSummaryMirror,
} from "../api/mirrorLeadIntelClient";
import { ProspectosDrawer } from "../components/prospectos/ProspectosDrawer";
import { TechnicalDetailDisclosure } from "../components/operator/TechnicalDetailDisclosure";
import { formatMirrorLoadError } from "../lib/humanizeApiError";
import { leadProspectsQueryFromOrigin } from "../lib/prospectOriginQuery";
import { formatProspectosTableFooter } from "../lib/clientTablePagination";
import { useClientTablePagination } from "../lib/useClientTablePagination";
import { TablePaginationBar } from "../components/commercial/TablePaginationBar";
import {
  prospectContactCell,
  prospectClassificationLabel,
  prospectGmailHistoryCell,
  prospectOperationalNextAction,
  prospectOriginChip,
  prospectTableBadge,
} from "../lib/prospectLabels";

function formatLoadError(label: string, e: unknown): { message: string; detail: string | null } {
  return formatMirrorLoadError(label, e);
}

function KpiCard({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] px-4 py-3 shadow-sm">
      <p className="text-xs font-semibold uppercase tracking-wide text-[var(--color-muted)]">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-brand-900">{value}</p>
    </div>
  );
}

export function ProspectosPage() {
  const [summary, setSummary] = useState<LeadResearchSummaryUi | null>(null);
  const [searchInput, setSearchInput] = useState("");
  const [originFilter, setOriginFilter] = useState<ProspectOriginFilter>("");
  const [sector, setSector] = useState("");
  const [region, setRegion] = useState("");
  const [campaignBucket, setCampaignBucket] = useState("");
  const [minScore, setMinScore] = useState("");
  const [showBlocked, setShowBlocked] = useState(false);

  const [items, setItems] = useState<LeadProspectListItemUi[]>([]);
  const [total, setTotal] = useState(0);
  const [disclaimer, setDisclaimer] = useState("");
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [listErrorDetail, setListErrorDetail] = useState<string | null>(null);

  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [detail, setDetail] = useState<LeadProspectDetailResponseUi | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const buildQuery = useCallback((): LeadProspectsListQuery => {
    const base: LeadProspectsListQuery = {
      limit: 100,
      include_blocked: showBlocked || originFilter === "blocked",
    };
    if (searchInput.trim()) base.q = searchInput.trim();
    if (sector.trim()) base.sector = sector.trim();
    if (region.trim()) base.region = region.trim();
    if (campaignBucket) base.campaign_bucket = campaignBucket;
    if (minScore.trim()) base.min_score = Number(minScore);
    return leadProspectsQueryFromOrigin(originFilter, base);
  }, [searchInput, originFilter, sector, region, campaignBucket, minScore, showBlocked]);

  const loadList = useCallback(async (query: LeadProspectsListQuery) => {
    setListLoading(true);
    setListError(null);
    setListErrorDetail(null);
    try {
      const res = await fetchLeadProspectsMirror(query);
      setItems(res.items);
      setTotal(res.total);
      setDisclaimer(res.disclaimer);
    } catch (e) {
      const formatted = formatLoadError("No se pudieron cargar prospectos", e);
      setListError(formatted.message);
      setListErrorDetail(formatted.detail);
    } finally {
      setListLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchLeadResearchSummaryMirror()
      .then(setSummary)
      .catch(() => setSummary(null));
  }, []);

  useEffect(() => {
    void loadList(buildQuery());
  }, [loadList, buildQuery]);

  useEffect(() => {
    if (!selectedKey) {
      setDetail(null);
      return;
    }
    setDetailLoading(true);
    setDetailError(null);
    void fetchLeadProspectDetailMirror(selectedKey)
      .then(setDetail)
      .catch((e) => setDetailError(formatLoadError("Ficha", e).message))
      .finally(() => setDetailLoading(false));
  }, [selectedKey]);

  const priorHistoryWarning = useMemo(() => {
    if (!summary) return false;
    return (
      summary.same_domain_review >= 3 ||
      summary.gmail_historico >= 1 ||
      summary.followup_antiguo >= 1 ||
      summary.blocked_count >= 5
    );
  }, [summary]);

  const { pageSize, setPage, setPageSize, pagination } = useClientTablePagination(items, [
    searchInput,
    originFilter,
    sector,
    region,
    campaignBucket,
    minScore,
    showBlocked,
    items.length,
  ]);

  const pagedItems = pagination.slice;

  const listFooter = useMemo(
    () =>
      formatProspectosTableFooter({
        from: pagination.from,
        to: pagination.to,
        loaded: items.length,
        apiTotal: total,
        page: pagination.page,
        totalPages: pagination.totalPages,
      }),
    [
      pagination.from,
      pagination.to,
      pagination.page,
      pagination.totalPages,
      items.length,
      total,
    ],
  );

  const handleExport = useCallback(
    (exportQueue: ProspectExportQueue) => {
      const query: LeadProspectsExportQuery = { ...buildQuery(), export_queue: exportQueue };
      void downloadLeadProspectsExportCsv(query).catch((e) => {
        const formatted = formatLoadError("No se pudo exportar prospectos", e);
        setListError(formatted.message);
        setListErrorDetail(formatted.detail);
      });
    },
    [buildQuery],
  );

  return (
    <div className="space-y-6" data-testid="prospectos-page">
      <header>
        <h1 className="text-2xl font-semibold text-brand-900">Prospectos</h1>
        <p className="mt-1 text-sm text-[var(--color-muted)]">
          Mesa única de revisión comercial · DeepSearch, Gmail histórico, follow-up y casos activos ·
          solo lectura
        </p>
        {disclaimer ? <p className="mt-2 text-xs text-sky-900">{disclaimer}</p> : null}
      </header>

      {summary?.table_available ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" data-testid="prospectos-kpis">
          <KpiCard label="Total prospectos" value={summary.total} />
          <KpiCard label="Listos para contactar" value={summary.ready_to_contact} />
          <KpiCard label="Ya contactados" value={summary.already_contacted} />
          <KpiCard label="Falta email / enriquecer" value={summary.needs_email_enrichment} />
          <KpiCard label="Licitaciones" value={summary.tender_opportunity} />
          <KpiCard label="Revisar historial" value={summary.review_history} />
          <KpiCard label="Bloqueados" value={summary.blocked_count} />
          <KpiCard label="Follow-up a revisar" value={summary.followup_eligible} />
        </div>
      ) : null}

      {priorHistoryWarning ? (
        <p
          className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-950"
          data-testid="prospectos-prior-history-warning"
        >
          Hay prospectos con historial previo; revisar antes de contactar.
        </p>
      ) : null}

      <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-4 shadow-sm">
        <div className="flex flex-wrap gap-3">
          <input
            type="search"
            placeholder="Buscar organización o contacto"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            className="min-w-[200px] flex-1 rounded-md border border-[var(--color-border)] px-3 py-2 text-sm"
          />
          <select
            value={originFilter}
            onChange={(e) => setOriginFilter(e.target.value as ProspectOriginFilter)}
            className="rounded-md border border-[var(--color-border)] px-3 py-2 text-sm"
            aria-label="Origen"
            data-testid="prospectos-origin-filter"
          >
            <option value="">Todos los orígenes</option>
            <option value="deepsearch">Investigación DeepSearch</option>
            <option value="gmail_historico">Gmail histórico</option>
            <option value="followup_antiguo">Follow-up antiguo</option>
            <option value="caso_activo">Caso activo</option>
            <option value="legacy_2016_2019">Base antigua 2016–2019</option>
            <option value="same_domain_contacted_review">Mismo dominio / revisar historial</option>
            <option value="research_only_contact_needed">Falta email</option>
            <option value="public_tender_review">Licitación</option>
            <option value="blocked">Bloqueado</option>
          </select>
          <input
            placeholder="Sector"
            value={sector}
            onChange={(e) => setSector(e.target.value)}
            className="rounded-md border border-[var(--color-border)] px-3 py-2 text-sm"
          />
          <input
            placeholder="Región"
            value={region}
            onChange={(e) => setRegion(e.target.value)}
            className="rounded-md border border-[var(--color-border)] px-3 py-2 text-sm"
          />
          <select
            value={campaignBucket}
            onChange={(e) => setCampaignBucket(e.target.value)}
            className="rounded-md border border-[var(--color-border)] px-3 py-2 text-sm"
            aria-label="Bucket de campaña"
          >
            <option value="">Todos los buckets</option>
            <option value="private_lab">Laboratorio privado</option>
            <option value="public_tender">Licitación pública</option>
            <option value="university">Universidad</option>
            <option value="same_domain">Mismo dominio</option>
            <option value="active_case">Caso activo</option>
          </select>
          <input
            placeholder="Score mínimo"
            value={minScore}
            onChange={(e) => setMinScore(e.target.value)}
            className="w-28 rounded-md border border-[var(--color-border)] px-3 py-2 text-sm"
          />
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={showBlocked}
              onChange={(e) => setShowBlocked(e.target.checked)}
            />
            Mostrar bloqueados
          </label>
          <button
            type="button"
            onClick={() => void loadList(buildQuery())}
            className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700"
          >
            Aplicar filtros
          </button>
        </div>
        <p
          className="mt-3 text-xs text-[var(--color-muted)]"
          data-testid="prospectos-legacy-empty-hint"
        >
          Si Base antigua 2016–2019 aparece vacía, probablemente ese archivo histórico no está cargado
          en el espejo actual. Importar PST/cPanel/archivo antiguo antes de esperar resultados.
        </p>
        <div
          className="mt-4 border-t border-[var(--color-border)] pt-4"
          data-testid="prospectos-export-panel"
        >
          <p className="text-xs font-semibold uppercase tracking-wide text-[var(--color-muted)]">
            Reportes / Exportar
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => handleExport("ready_to_contact")}
              className="rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm hover:bg-slate-50"
              data-testid="prospectos-export-ready"
            >
              Exportar listos para contactar
            </button>
            <button
              type="button"
              onClick={() => handleExport("needs_email_enrichment")}
              className="rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm hover:bg-slate-50"
              data-testid="prospectos-export-needs-email"
            >
              Exportar falta email / enriquecer
            </button>
            <button
              type="button"
              onClick={() => handleExport("tender_opportunity")}
              className="rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm hover:bg-slate-50"
              data-testid="prospectos-export-tender"
            >
              Exportar licitaciones
            </button>
            <button
              type="button"
              onClick={() => handleExport("review_history")}
              className="rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm hover:bg-slate-50"
              data-testid="prospectos-export-review-history"
            >
              Exportar revisar historial
            </button>
            <button
              type="button"
              onClick={() => handleExport("already_contacted_followup_review")}
              className="rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm hover:bg-slate-50"
              data-testid="prospectos-export-followup"
            >
              Exportar follow-up a revisar
            </button>
            <button
              type="button"
              onClick={() => handleExport("all_visible")}
              className="rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm hover:bg-slate-50"
              data-testid="prospectos-export-filtered"
            >
              Exportar vista filtrada
            </button>
          </div>
        </div>
      </section>

      {listError ? (
        <div className="text-sm text-red-800" role="alert">
          <p className="font-medium">{listError}</p>
          {listErrorDetail ? <TechnicalDetailDisclosure detail={listErrorDetail} /> : null}
        </div>
      ) : null}

      <div className="overflow-x-auto rounded-xl border border-[var(--color-border)] bg-white shadow-sm">
        <table className="min-w-full text-left text-sm">
          <thead className="border-b border-[var(--color-border)] bg-slate-50 text-xs uppercase text-[var(--color-muted)]">
            <tr>
              <th className="px-4 py-3">Organización</th>
              <th className="px-4 py-3">Origen</th>
              <th className="px-4 py-3">Contacto</th>
              <th className="px-4 py-3">Estado</th>
              <th className="px-4 py-3">Score</th>
              <th className="px-4 py-3">Historial Gmail</th>
              <th className="px-4 py-3">Sector</th>
              <th className="px-4 py-3">Próxima acción</th>
            </tr>
          </thead>
          <tbody>
            {pagedItems.map((row) => {
              const origin = prospectOriginChip(row);
              const badge = prospectTableBadge(row);
              return (
                <tr
                  key={row.prospect_key}
                  className="cursor-pointer border-b border-[var(--color-border)] hover:bg-brand-50/40"
                  onClick={() => setSelectedKey(row.prospect_key)}
                >
                  <td className="px-4 py-3 font-medium">{row.organization_name}</td>
                  <td className="px-4 py-3">
                    <span
                      className={`inline-block rounded-full border px-2.5 py-0.5 text-xs font-semibold ${origin.className}`}
                      data-testid="prospect-origin-chip"
                    >
                      {origin.label}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-sm">{prospectContactCell(row)}</td>
                  <td className="px-4 py-3">
                    {badge ? (
                      <span
                        className={`inline-block rounded-full border px-2.5 py-0.5 text-xs font-semibold ${badge.className}`}
                      >
                        {badge.label}
                      </span>
                    ) : (
                      <span className="text-sm">{prospectClassificationLabel(row.classification)}</span>
                    )}
                  </td>
                  <td className="px-4 py-3">{row.final_score}</td>
                  <td className="px-4 py-3 max-w-[12rem] truncate text-xs text-[var(--color-muted)]">
                    {prospectGmailHistoryCell(row)}
                  </td>
                  <td className="px-4 py-3 max-w-[10rem] truncate">{row.sector ?? "—"}</td>
                  <td className="px-4 py-3 max-w-[16rem] truncate">{prospectOperationalNextAction(row)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {items.length > 0 && !listLoading ? (
          <TablePaginationBar
            page={pagination.page}
            totalPages={pagination.totalPages}
            pageSize={pageSize}
            onPageChange={setPage}
            onPageSizeChange={setPageSize}
            disabled={listLoading}
          />
        ) : null}
        <div
          className="border-t border-[var(--color-border)] px-4 py-2 text-xs text-[var(--color-muted)]"
          data-testid="prospectos-table-footer"
        >
          {listLoading ? (
            <p>Cargando…</p>
          ) : (
            <>
              <p>{listFooter.primary}</p>
              {listFooter.truncationNote ? (
                <p className="mt-1 text-amber-900">{listFooter.truncationNote}</p>
              ) : null}
            </>
          )}
        </div>
      </div>

      {selectedKey ? (
        <ProspectosDrawer
          detail={detail}
          loading={detailLoading}
          error={detailError}
          onClose={() => setSelectedKey(null)}
        />
      ) : null}
    </div>
  );
}
