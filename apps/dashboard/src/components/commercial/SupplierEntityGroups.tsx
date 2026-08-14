import { useEffect, useMemo, useState } from "react";
import type { WarmCaseItem } from "../../api/commercialTypes";
import type { GmailInteractionAuditSnapshot } from "../../api/gmailInteractionAuditTypes";
import type { ApiBackend } from "../../api/operatorTypes";
import { emailDomain } from "../../lib/clientTableView";
import {
  SUPPLIER_AUDIT_MISSING_LABEL,
  supplierAuditDetailLabel,
} from "../../lib/supplierMirrorDepth";
import {
  groupSupplierWarmCases,
  type SupplierEntityGroup,
} from "../../lib/supplierEntityGrouping";
import { WarmCasesTable } from "./WarmCasesTable";

function roleBadgeClassName(badge: SupplierEntityGroup["roleBadge"]): string {
  if (badge === "Cotización recibida") {
    return "bg-teal-100 text-teal-900";
  }
  if (badge === "Seguimiento") {
    return "bg-amber-100 text-amber-950";
  }
  return "bg-slate-100 text-slate-700";
}

function SupplierEntityCard({
  group,
  active,
  onSelect,
}: {
  group: SupplierEntityGroup;
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      aria-label={`${group.label}, ${group.summaryLabel}`}
      onClick={onSelect}
      data-testid="supplier-entity-card"
      className={`w-full rounded-lg border px-2.5 py-2 text-left transition-colors motion-reduce:transition-none ${
        active
          ? "border-brand-500 bg-brand-50/80 ring-1 ring-brand-500/40"
          : "border-[var(--color-border)] bg-[var(--color-card)] hover:border-brand-400/50 hover:bg-brand-50/30"
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <p className="truncate text-sm font-semibold text-slate-900">{group.label}</p>
        <span
          className={`shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-medium ${roleBadgeClassName(group.roleBadge)}`}
        >
          {group.roleBadge}
        </span>
      </div>
      <p className="mt-1 text-[11px] font-medium text-brand-800">{group.summaryLabel}</p>
      <p className="mt-0.5 text-[10px] text-[var(--color-muted)]">
        {group.latestActivityLabel ?? "Sin actividad reciente"}
      </p>
      <p className="mt-0.5 line-clamp-1 text-[10px] text-slate-500" title={group.latestSubject}>
        {group.latestSubject}
      </p>
    </button>
  );
}

function KpiCard({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] px-4 py-3 shadow-sm transition-all duration-150 hover:-translate-y-0.5 hover:shadow-md motion-reduce:transition-none motion-reduce:hover:translate-y-0">
      <p className="text-xs font-medium text-[var(--color-muted)]">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-slate-900">{value}</p>
    </div>
  );
}

function supplierKpis(groups: SupplierEntityGroup[], allItems: WarmCaseItem[]) {
  return {
    providers: groups.length,
    quotes: groups.reduce((sum, group) => sum + group.quoteCount, 0),
    followups: groups.reduce((sum, group) => sum + group.followupCount, 0),
    activeThreads: allItems.length,
  };
}

function filterSupplierGroups(groups: SupplierEntityGroup[], search: string): SupplierEntityGroup[] {
  const query = search.trim().toLowerCase();
  if (!query) {
    return groups;
  }
  return groups.filter((group) => {
    if (group.label.toLowerCase().includes(query)) {
      return true;
    }
    if (group.latestSubject.toLowerCase().includes(query)) {
      return true;
    }
    return group.items.some((row) => {
      const email = row.contact_email?.toLowerCase() ?? "";
      const domain = emailDomain(row.contact_email).toLowerCase();
      return (
        email.includes(query) ||
        domain.includes(query) ||
        (row.account_name?.toLowerCase().includes(query) ?? false) ||
        (row.subject?.toLowerCase().includes(query) ?? false)
      );
    });
  });
}

export function SupplierEntityGroups({
  backend,
  allItems,
  auditSnapshot,
  meta,
  loading,
  error,
  onRetry,
  onContactSelect,
}: {
  backend: ApiBackend;
  allItems: WarmCaseItem[];
  auditSnapshot?: GmailInteractionAuditSnapshot | null;
  meta: {
    data_source: "sqlite" | "postgres_mirror";
    reduced_mode: boolean;
    note: string;
    count: number;
  } | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  onContactSelect: (email: string) => void;
}) {
  const groups = useMemo(
    () => groupSupplierWarmCases(allItems, auditSnapshot),
    [allItems, auditSnapshot],
  );
  const [searchInput, setSearchInput] = useState("");
  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null);

  const filteredGroups = useMemo(
    () => filterSupplierGroups(groups, searchInput),
    [groups, searchInput],
  );

  useEffect(() => {
    if (filteredGroups.length === 0) {
      setSelectedGroupId(null);
      return;
    }
    if (!selectedGroupId || !filteredGroups.some((group) => group.id === selectedGroupId)) {
      setSelectedGroupId(filteredGroups[0].id);
    }
  }, [filteredGroups, selectedGroupId]);

  const selectedGroup: SupplierEntityGroup | null = useMemo(() => {
    if (!selectedGroupId) {
      return null;
    }
    return filteredGroups.find((group) => group.id === selectedGroupId) ?? null;
  }, [filteredGroups, selectedGroupId]);

  const kpis = useMemo(() => supplierKpis(groups, allItems), [groups, allItems]);

  if (loading || error || allItems.length === 0) {
    return (
      <WarmCasesTable
        backend={backend}
        items={allItems}
        meta={meta}
        loading={loading}
        error={error}
        onRetry={onRetry}
        onContactSelect={onContactSelect}
        title="Proveedores"
        subtitle="Cotizaciones, seguimientos y hilos de proveedores. Solo lectura."
        showViewPresets={false}
        initialFilters={{ preset: "todo", hideInternalContacts: false }}
      />
    );
  }

  return (
    <div className="space-y-6" data-testid="suppliers-workspace">
      <header data-testid="suppliers-page-intro">
        <p className="text-sm text-[var(--color-muted)]">
          Cotizaciones, seguimientos y hilos de proveedores. Solo lectura.
        </p>
      </header>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" data-testid="supplier-kpis">
        <KpiCard label="Proveedores" value={kpis.providers} />
        <KpiCard label="Cotizaciones recibidas" value={kpis.quotes} />
        <KpiCard label="Seguimientos" value={kpis.followups} />
        <KpiCard label="Hilos activos" value={kpis.activeThreads} />
      </div>

      <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-4 shadow-sm">
        <input
          type="search"
          placeholder="Buscar proveedor, email, dominio o asunto"
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          className="w-full rounded-md border border-[var(--color-border)] px-3 py-2 text-sm"
          data-testid="supplier-search"
        />
      </section>

      <div className="grid gap-4 lg:grid-cols-[minmax(220px,280px)_1fr]">
        <aside
          className="space-y-2 lg:max-h-[calc(100vh-14rem)] lg:overflow-y-auto"
          aria-label="Lista de proveedores"
        >
          <div className="space-y-2" data-testid="supplier-entity-cards">
            {filteredGroups.map((group) => (
              <SupplierEntityCard
                key={group.id}
                group={group}
                active={selectedGroupId === group.id}
                onSelect={() => setSelectedGroupId(group.id)}
              />
            ))}
          </div>
          {filteredGroups.length === 0 ? (
            <p className="text-sm text-[var(--color-muted)]" role="status">
              Ningún proveedor coincide con la búsqueda.
            </p>
          ) : null}
        </aside>

        <section
          className="min-w-0 rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-4 shadow-sm"
          data-testid="supplier-detail-panel"
        >
          {selectedGroup ? (
            <>
              <div className="border-b border-[var(--color-border)] pb-4">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <h2 className="text-lg font-semibold text-slate-900" data-testid="supplier-detail-title">
                    {selectedGroup.label}
                  </h2>
                  <span
                    className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${roleBadgeClassName(selectedGroup.roleBadge)}`}
                  >
                    {selectedGroup.roleBadge}
                  </span>
                </div>
                <p className="mt-1 text-sm text-brand-800">{selectedGroup.summaryLabel}</p>
                <p className="mt-1 text-xs text-[var(--color-muted)]">
                  Última actividad · {selectedGroup.latestActivityLabel ?? "sin fecha"}
                </p>
                <p className="mt-1 text-xs text-slate-600">{selectedGroup.latestSubject}</p>
                <p
                  className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-950"
                  data-testid="supplier-mirror-scope-note"
                >
                  Este panel muestra casos tibios del espejo, no todo el historial Gmail.
                </p>
                <p
                  className="mt-2 rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 text-xs text-sky-950"
                  data-testid="supplier-gmail-hint"
                >
                  {selectedGroup.auditDomain
                    ? supplierAuditDetailLabel(selectedGroup.auditDomain)
                    : SUPPLIER_AUDIT_MISSING_LABEL}
                </p>
                <p
                  className="mt-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-800"
                  data-testid="supplier-readonly-note"
                >
                  Solo lectura: revisar historial antes de contactar.
                </p>
              </div>
              <div className="mt-4">
                <WarmCasesTable
                  backend={backend}
                  items={selectedGroup.items}
                  meta={meta}
                  loading={false}
                  error={null}
                  onRetry={onRetry}
                  onContactSelect={onContactSelect}
                  title="Hilos del proveedor"
                  subtitle="Cola de solo lectura · solo asunto y vista previa (sin cuerpo del correo)."
                  showViewPresets={false}
                  initialFilters={{ preset: "todo", hideInternalContacts: false }}
                />
              </div>
            </>
          ) : (
            <p className="text-sm text-[var(--color-muted)]" role="status">
              Selecciona un proveedor para revisar sus hilos.
            </p>
          )}
        </section>
      </div>
    </div>
  );
}
