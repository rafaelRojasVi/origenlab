import { useCallback, useEffect, useMemo, useState } from "react";
import type { ContactScope } from "../api/leadIntelTypes";
import type { LeadProspectListItemUi } from "../api/leadIntelTypes";
import type { GmailInteractionAuditSnapshot } from "../api/gmailInteractionAuditTypes";
import { fetchGmailInteractionAudit } from "../api/mirrorAuditClient";
import { fetchLeadProspectsMirror } from "../api/mirrorLeadIntelClient";
import { InstitutionDrawer } from "../components/contacts/InstitutionDrawer";
import { TechnicalDetailDisclosure } from "../components/operator/TechnicalDetailDisclosure";
import { TablePaginationBar } from "../components/commercial/TablePaginationBar";
import { useDashboardData } from "../context/DashboardDataContext";
import {
  buildCustomerInstitutionGroups,
  filterCustomerInstitutionGroups,
  institutionKpis,
  institutionStatusChips,
  INSTITUTION_TYPE_OPTIONS,
  type CustomerInstitutionGroup,
  type InstitutionType,
} from "../lib/customerInstitutionGroups";
import { institutionGmailHistorySummary } from "../lib/institutionMirrorDepth";
import { formatMirrorLoadError } from "../lib/humanizeApiError";
import { useClientTablePagination } from "../lib/useClientTablePagination";

const CUSTOMER_INSTITUTION_LIMIT = 100;
const SEARCH_DEBOUNCE_MS = 400;

const CONTACT_SCOPE_OPTIONS: ReadonlyArray<{ value: ContactScope; label: string }> = [
  { value: "contacted", label: "Contactados OrigenLab" },
  { value: "followup", label: "Seguimiento pendiente" },
  { value: "active", label: "Conversaciones activas" },
  { value: "deepsearch", label: "Investigación / DeepSearch" },
  { value: "blocked", label: "Bloqueados / revisar" },
];

function KpiCard({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] px-4 py-3 shadow-sm transition-all duration-150 hover:-translate-y-0.5 hover:shadow-md motion-reduce:transition-none motion-reduce:hover:translate-y-0">
      <p className="text-xs font-medium text-[var(--color-muted)]">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-slate-900">{value}</p>
    </div>
  );
}

function InstitutionGmailHistoryCell({
  group,
  auditSnapshot,
}: {
  group: CustomerInstitutionGroup;
  auditSnapshot: GmailInteractionAuditSnapshot | null;
}) {
  const summary = institutionGmailHistorySummary(group, auditSnapshot);
  if (summary.compactLine === "Sin historial en espejo") {
    return <span className="text-xs">{summary.compactLine}</span>;
  }
  return (
    <div className="space-y-0.5 text-xs" data-testid="institution-gmail-history-cell">
      <p>{summary.mirrorLine}</p>
      <p className="text-[var(--color-muted)]">{summary.detectedLine}</p>
      <p className="text-sky-900">{summary.sqliteLine}</p>
    </div>
  );
}

export function ContactsPage() {
  const { setContactEmail } = useDashboardData();
  const [searchInput, setSearchInput] = useState("");
  const [appliedSearch, setAppliedSearch] = useState("");
  const [contactScope, setContactScope] = useState<ContactScope>("contacted");
  const [institutionType, setInstitutionType] = useState<InstitutionType | "">("");
  const [sector, setSector] = useState("");
  const [region, setRegion] = useState("");
  const [minScore, setMinScore] = useState("");

  const [items, setItems] = useState<LeadProspectListItemUi[]>([]);
  const [mirrorTotal, setMirrorTotal] = useState(0);
  const [disclaimer, setDisclaimer] = useState("");
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [listErrorDetail, setListErrorDetail] = useState<string | null>(null);
  const [selectedGroup, setSelectedGroup] = useState<CustomerInstitutionGroup | null>(null);
  const [auditSnapshot, setAuditSnapshot] = useState<GmailInteractionAuditSnapshot | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setAppliedSearch(searchInput.trim());
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [searchInput]);

  const loadList = useCallback(async (searchOverride?: string) => {
    setListLoading(true);
    setListError(null);
    setListErrorDetail(null);
    try {
      const effectiveSearch = searchOverride ?? appliedSearch;
      const parsedMinScore = minScore.trim() ? Number(minScore) : undefined;
      const res = await fetchLeadProspectsMirror({
        limit: CUSTOMER_INSTITUTION_LIMIT,
        contact_scope: contactScope,
        include_blocked: contactScope === "blocked",
        q: effectiveSearch || undefined,
        sector: sector.trim() || undefined,
        region: region.trim() || undefined,
        min_score: Number.isFinite(parsedMinScore) ? parsedMinScore : undefined,
      });
      setItems(res.items);
      setMirrorTotal(res.total);
      setDisclaimer(res.disclaimer);
    } catch (e) {
      const formatted = formatMirrorLoadError("No se pudieron cargar instituciones", e);
      setListError("No se pudieron cargar las instituciones desde el espejo.");
      setListErrorDetail(formatted.detail ?? formatted.message);
    } finally {
      setListLoading(false);
    }
  }, [appliedSearch, contactScope, minScore, region, sector]);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  useEffect(() => {
    let cancelled = false;
    void fetchGmailInteractionAudit()
      .then((response) => {
        if (!cancelled && response.status === "ok" && response.snapshot) {
          setAuditSnapshot(response.snapshot);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setAuditSnapshot(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const allGroups = useMemo(() => buildCustomerInstitutionGroups(items), [items]);

  const filteredGroups = useMemo(
    () =>
      filterCustomerInstitutionGroups(allGroups, {
        institutionType,
        sector,
        region,
        minScore: minScore.trim() ? Number(minScore) : null,
      }),
    [allGroups, institutionType, sector, region, minScore],
  );

  const kpis = useMemo(() => institutionKpis(filteredGroups), [filteredGroups]);

  const { pageSize, setPage, setPageSize, pagination } = useClientTablePagination(filteredGroups, [
    appliedSearch,
    contactScope,
    institutionType,
    sector,
    region,
    minScore,
    filteredGroups.length,
  ]);

  const pagedGroups = pagination.slice;

  const handleRefresh = () => {
    const nextSearch = searchInput.trim();
    setAppliedSearch(nextSearch);
    void loadList(nextSearch);
  };

  return (
    <div className="space-y-6" data-testid="contacts-page">
      <header>
        <h1 className="text-2xl font-semibold text-slate-900">Clientes / instituciones</h1>
        <p className="mt-1 text-sm text-[var(--color-muted)]">
          Agrupa instituciones y contactos con historial OrigenLab publicado al espejo. DeepSearch queda
          separado en Investigación.
        </p>
        <p className="mt-1 text-xs text-amber-900" data-testid="institution-send-disclaimer">
          Este panel no autoriza envíos. Para preparar campañas usar contact-universe-review y revisión
          humana.
        </p>
        <p className="mt-1 text-xs text-[var(--color-muted)]" data-testid="institution-mirror-limit-note">
          Consulta hasta {CUSTOMER_INSTITUTION_LIMIT} filas por vista; el total del espejo puede ser mayor.
        </p>
        <p className="mt-1 text-xs text-[var(--color-muted)]" data-testid="institution-gmail-mirror-note">
          El historial Gmail depende de coincidencias publicadas al espejo; puede no incluir todo el
          buzón histórico.
        </p>
        {disclaimer ? <p className="mt-2 text-xs text-sky-900">{disclaimer}</p> : null}
      </header>

      <div
        className="flex flex-wrap gap-2"
        role="tablist"
        aria-label="Alcance de instituciones"
        data-testid="institution-scope-tabs"
      >
        {CONTACT_SCOPE_OPTIONS.map((option) => (
          <button
            key={option.value}
            type="button"
            role="tab"
            aria-selected={contactScope === option.value}
            data-testid={`institution-scope-${option.value}`}
            onClick={() => setContactScope(option.value)}
            className={`rounded-full border px-3 py-1.5 text-sm font-medium ${
              contactScope === option.value
                ? "border-brand-600 bg-brand-600 text-white"
                : "border-[var(--color-border)] bg-white text-slate-700 hover:bg-slate-50"
            }`}
          >
            {option.label}
          </button>
        ))}
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5" data-testid="institution-kpis">
        <KpiCard label="Instituciones" value={kpis.institutions} />
        <KpiCard label="Con historial Gmail en espejo" value={kpis.withGmailHistory} />
        <KpiCard label="Sin email / investigar" value={kpis.missingEmail} />
        <KpiCard label="Seguras para revisar" value={kpis.safeToReview} />
        <KpiCard label="Bloqueadas / revisar" value={kpis.blockedOrRisk} />
      </div>

      <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-4 shadow-sm">
        <div className="flex flex-wrap gap-3">
          <input
            type="search"
            placeholder="Buscar institución, dominio o email"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            className="min-w-[200px] flex-1 rounded-md border border-[var(--color-border)] px-3 py-2 text-sm"
            data-testid="institution-search"
          />
          <select
            value={institutionType}
            onChange={(e) => setInstitutionType(e.target.value as InstitutionType | "")}
            className="rounded-md border border-[var(--color-border)] px-3 py-2 text-sm"
            aria-label="Tipo de institución"
            data-testid="institution-type-filter"
          >
            {INSTITUTION_TYPE_OPTIONS.map((option) => (
              <option key={option.value || "all"} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <input
            placeholder="Sector"
            value={sector}
            onChange={(e) => setSector(e.target.value)}
            className="rounded-md border border-[var(--color-border)] px-3 py-2 text-sm"
            data-testid="institution-sector-filter"
          />
          <input
            placeholder="Región"
            value={region}
            onChange={(e) => setRegion(e.target.value)}
            className="rounded-md border border-[var(--color-border)] px-3 py-2 text-sm"
            data-testid="institution-region-filter"
          />
          <input
            placeholder="Score mínimo"
            value={minScore}
            onChange={(e) => setMinScore(e.target.value)}
            className="w-28 rounded-md border border-[var(--color-border)] px-3 py-2 text-sm"
            data-testid="institution-min-score-filter"
          />
          <button
            type="button"
            onClick={handleRefresh}
            className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700"
          >
            Actualizar datos
          </button>
        </div>
      </section>

      {listError ? (
        <div
          className="rounded-xl border border-red-200 bg-red-50 px-4 py-4 text-sm text-red-900"
          role="alert"
          data-testid="institution-load-error"
        >
          <p className="font-medium">{listError}</p>
          {listErrorDetail ? <TechnicalDetailDisclosure detail={listErrorDetail} /> : null}
        </div>
      ) : null}

      <div className="overflow-x-auto rounded-xl border border-[var(--color-border)] bg-white shadow-sm">
        {!listError && !listLoading && pagedGroups.length === 0 ? (
          <p className="px-4 py-10 text-center text-sm text-[var(--color-muted)]" role="status">
            No hay instituciones para mostrar con los filtros actuales.
          </p>
        ) : null}
        {!listError ? (
        <table className="min-w-full text-left text-sm">
          <thead className="border-b border-[var(--color-border)] bg-slate-50 text-xs uppercase text-[var(--color-muted)]">
            <tr>
              <th className="px-4 py-3">Institución</th>
              <th className="px-4 py-3">Contactos</th>
              <th className="px-4 py-3">Historial Gmail</th>
              <th className="px-4 py-3">Score</th>
              <th className="px-4 py-3">Sector / región</th>
              <th className="px-4 py-3">Estado</th>
              <th className="px-4 py-3">Próxima acción</th>
            </tr>
          </thead>
          <tbody>
            {pagedGroups.map((group) => {
              const chips = institutionStatusChips(group);
              return (
                <tr
                  key={group.key}
                  className="cursor-pointer border-b border-[var(--color-border)] hover:bg-brand-50/40"
                  onClick={() => setSelectedGroup(group)}
                  data-testid="institution-row"
                >
                  <td className="px-4 py-3">
                    <p className="font-medium">{group.institutionName}</p>
                    {group.domain ? (
                      <p className="text-xs text-[var(--color-muted)]">{group.domain}</p>
                    ) : null}
                  </td>
                  <td className="px-4 py-3">
                    {group.contactsWithEmail} con email
                    {group.contactsMissingEmail > 0 ? (
                      <span className="block text-xs text-amber-900">
                        {group.contactsMissingEmail} sin email
                      </span>
                    ) : null}
                  </td>
                  <td className="px-4 py-3 max-w-[14rem]">
                    <InstitutionGmailHistoryCell group={group} auditSnapshot={auditSnapshot} />
                  </td>
                  <td className="px-4 py-3">{group.maxFinalScore}</td>
                  <td className="px-4 py-3 max-w-[10rem] truncate">
                    {[group.sectors[0], group.regions[0]].filter(Boolean).join(" · ") || "—"}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-1">
                      {chips.map((chip) => (
                        <span
                          key={chip.code}
                          className={`inline-block rounded-full border px-2 py-0.5 text-xs font-semibold ${chip.className}`}
                        >
                          {chip.label}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="px-4 py-3 max-w-[14rem] truncate">{group.recommendedNextAction}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        ) : null}
        {filteredGroups.length > 0 && !listLoading && !listError ? (
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
          data-testid="institution-result-summary"
        >
          {listLoading ? (
            <p>Cargando…</p>
          ) : listError ? (
            <p>Sin datos de instituciones cargados.</p>
          ) : (
            <p>
              Mostrando {filteredGroups.length} de {mirrorTotal} coincidencia
              {mirrorTotal === 1 ? "" : "s"} del espejo
            </p>
          )}
        </div>
      </div>

      {selectedGroup ? (
        <InstitutionDrawer
          group={selectedGroup}
          auditSnapshot={auditSnapshot}
          onClose={() => setSelectedGroup(null)}
          onSelectEmail={setContactEmail}
        />
      ) : null}
    </div>
  );
}
