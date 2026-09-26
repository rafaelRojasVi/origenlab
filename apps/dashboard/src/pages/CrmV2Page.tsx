/**
 * The V2 CRM browser — the first searchable operator surface over the durable core.
 *
 * Four cards, one page: contacts, organizations, prospects and evidence. Everything here
 * reads `/v2/*`, which is the **durable** V2 core — not the `mirror*` and `leadIntel*`
 * projections the older pages read, which may be dropped and rebuilt at any time.
 *
 * **It is the technical console, not the operator's entry point.** Contacto 360 and
 * Institución 360 (`#/contactos`, `#/instituciones`) are where a contact or an institution
 * is actually worked; this page is the raw, searchable, paged view of the same rows, and
 * every name in it links out to the corresponding 360 screen rather than opening a second,
 * smaller copy of it in a drawer.
 *
 * It is read-only, and structurally so: there is no command client imported here, and the
 * proxy allows no POST under `/v2`. Confirming an organization, naming a person or merging
 * two identities are durable commands that belong to the V2 command boundary, which does
 * not exist yet. Until it does, this page is where an operator *looks*, and the review
 * queue it shows is worked through the migration tools.
 */

import { useCallback, useEffect, useState } from "react";

import {
  fetchV2Contacts,
  fetchV2Evidence,
  fetchV2Organizations,
  fetchV2Prospects,
} from "../api/v2Client";
import type {
  V2Contact,
  V2EvidenceItem,
  V2Opportunity,
  V2Organization,
  V2Page,
} from "../api/v2Types";
import { V2EmptyState } from "../components/v2/V2EmptyState";
import { V2PageHeader } from "../components/v2/V2PageHeader";
import {
  CRM_V2_TABS,
  EVIDENCE_RESOLUTIONS,
  EVIDENCE_SOURCE_KINDS,
  confirmationLabel,
  pageFooter,
  resolutionLabel,
  searchPlaceholder,
  sourceKindLabel,
  usageLabel,
  type CrmV2Tab,
} from "../lib/crmV2Browser";
import { contactAddressesRedacted } from "../crm/redaction";
import { useAuthSession } from "../context/AuthSessionContext";
import { formatMirrorLoadError } from "../lib/humanizeApiError";
import { openV2Detail } from "../lib/v2DeepLink";

const PAGE_SIZE = 50;

type AnyPage =
  | { kind: "contacts"; page: V2Page<V2Contact> }
  | { kind: "organizations"; page: V2Page<V2Organization> }
  | { kind: "prospects"; page: V2Page<V2Opportunity> }
  | { kind: "evidence"; page: V2Page<V2EvidenceItem> };

function Badge({ tone, children }: { tone: "neutral" | "warn" | "ok"; children: React.ReactNode }) {
  const palette = {
    neutral: "bg-slate-100 text-slate-700",
    warn: "bg-amber-100 text-amber-800",
    ok: "bg-emerald-100 text-emerald-800",
  }[tone];
  return (
    <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${palette}`}>
      {children}
    </span>
  );
}

function ConfirmationBadge({ confirmation }: { confirmation: string }) {
  return (
    <Badge tone={confirmation === "confirmed" ? "ok" : "warn"}>
      {confirmationLabel(confirmation)}
    </Badge>
  );
}

export function CrmV2Page() {
  const [tab, setTab] = useState<CrmV2Tab>("contacts");
  const [query, setQuery] = useState("");
  const [resolution, setResolution] = useState("");
  const [sourceKind, setSourceKind] = useState("");
  const [offset, setOffset] = useState(0);
  const [result, setResult] = useState<AnyPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const definition = CRM_V2_TABS.find((entry) => entry.id === tab)!;
  const { session } = useAuthSession();
  const addressesRedacted = contactAddressesRedacted(session);
  const definitionLabel = definition.label;

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const trimmed = query.trim();
    try {
      if (tab === "contacts") {
        const page = await fetchV2Contacts({ q: trimmed || undefined, limit: PAGE_SIZE, offset });
        setResult({ kind: "contacts", page });
      } else if (tab === "organizations") {
        const page = await fetchV2Organizations({ q: trimmed || undefined, limit: PAGE_SIZE, offset });
        setResult({ kind: "organizations", page });
      } else if (tab === "prospects") {
        const page = await fetchV2Prospects({ limit: PAGE_SIZE, offset });
        setResult({ kind: "prospects", page });
      } else {
        const page = await fetchV2Evidence({
          q: trimmed || undefined,
          resolution: resolution || undefined,
          sourceKind: sourceKind || undefined,
          limit: PAGE_SIZE,
          offset,
        });
        setResult({ kind: "evidence", page });
      }
    } catch (caught) {
      setResult(null);
      setError(formatMirrorLoadError(definitionLabel, caught).message);
    } finally {
      setLoading(false);
    }
  }, [tab, query, resolution, sourceKind, offset, definitionLabel]);

  useEffect(() => {
    void load();
  }, [load]);

  function switchTab(next: CrmV2Tab) {
    setTab(next);
    setOffset(0);
    setQuery("");
    setResolution("");
    setSourceKind("");
    setResult(null);
  }

  const page = result?.page;

  return (
    <div className="space-y-4">
      <V2PageHeader
        title="CRM V2 (núcleo durable)"
        subtitle="Consola técnica sobre el núcleo durable V2: filas crudas y su procedencia. Para trabajar un contacto o una institución, sus nombres abren Contacto 360 e Institución 360."
      />

      <nav className="flex flex-wrap gap-2" aria-label="Tarjetas del CRM V2">
        {CRM_V2_TABS.map((entry) => (
          <button
            key={entry.id}
            type="button"
            onClick={() => switchTab(entry.id)}
            aria-current={entry.id === tab ? "page" : undefined}
            className={`rounded-lg px-3 py-1.5 text-sm font-medium ${
              entry.id === tab
                ? "bg-slate-900 text-white"
                : "border border-slate-300 text-slate-700"
            }`}
          >
            {entry.label}
          </button>
        ))}
      </nav>

      <p className="text-sm text-[var(--color-muted)]">{definition.description}</p>

      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          setOffset(0);
          void load();
        }}
      >
        {definition.searchable ? (
          <label className="flex flex-col text-xs text-[var(--color-muted)]">
            Buscar
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={searchPlaceholder(tab, addressesRedacted)}
              className="mt-1 w-64 rounded-md border border-slate-300 px-2 py-1 text-sm text-slate-900"
            />
          </label>
        ) : null}

        {tab === "evidence" ? (
          <>
            <label className="flex flex-col text-xs text-[var(--color-muted)]">
              Resolución
              <select
                value={resolution}
                onChange={(event) => {
                  setResolution(event.target.value);
                  setOffset(0);
                }}
                className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm text-slate-900"
              >
                <option value="">Todas</option>
                {EVIDENCE_RESOLUTIONS.map((value) => (
                  <option key={value} value={value}>
                    {resolutionLabel(value)}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col text-xs text-[var(--color-muted)]">
              Procedencia
              <select
                value={sourceKind}
                onChange={(event) => {
                  setSourceKind(event.target.value);
                  setOffset(0);
                }}
                className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm text-slate-900"
              >
                <option value="">Todas</option>
                {EVIDENCE_SOURCE_KINDS.map((value) => (
                  <option key={value} value={value}>
                    {sourceKindLabel(value)}
                  </option>
                ))}
              </select>
            </label>
          </>
        ) : null}

        <button
          type="submit"
          className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white"
        >
          Buscar
        </button>
      </form>

      {error ? (
        <p className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
          {error}
        </p>
      ) : null}
      {loading ? <p className="text-sm text-[var(--color-muted)]">Cargando…</p> : null}

      {page && page.items.length === 0 && !loading ? (
        <V2EmptyState
          title="Sin filas"
          description={
            tab === "prospects"
              ? "Sin datos: falta migrar el histórico V1. crm.opportunity está vacía porque las oportunidades durables de V1 aún no se migran — no porque no haya trabajo."
              : "Ninguna fila coincide con la búsqueda."
          }
        />
      ) : null}

      {result?.kind === "contacts" && result.page.items.length > 0 ? (
        <table className="w-full table-auto text-left text-sm" data-testid="v2-contacts-table">
          <thead className="text-xs uppercase text-[var(--color-muted)]">
            <tr>
              <th className="py-2">Dirección</th>
              <th>Uso</th>
              <th>Estado</th>
              <th>Institución</th>
            </tr>
          </thead>
          <tbody>
            {result.page.items.map((row) => (
              <tr key={row.contact_point_id} className="border-t border-slate-200">
                <td className="py-2">
                  <button
                    type="button"
                    className="break-all text-left font-medium text-slate-900 underline decoration-dotted"
                    onClick={() => openV2Detail("contactos", row.contact_point_id)}
                  >
                    {row.address}
                  </button>
                </td>
                <td>{usageLabel(row.usage)}</td>
                <td>
                  <ConfirmationBadge confirmation={row.confirmation} />
                </td>
                <td className="text-[var(--color-muted)]">{row.organization_name ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}

      {result?.kind === "organizations" && result.page.items.length > 0 ? (
        <table className="w-full table-auto text-left text-sm" data-testid="v2-organizations-table">
          <thead className="text-xs uppercase text-[var(--color-muted)]">
            <tr>
              <th className="py-2">Nombre</th>
              <th>Tipo</th>
              <th>Estado</th>
              <th>Canales</th>
            </tr>
          </thead>
          <tbody>
            {result.page.items.map((row) => (
              <tr key={row.organization_id} className="border-t border-slate-200">
                <td className="py-2">
                  <button
                    type="button"
                    className="text-left font-medium text-slate-900 underline decoration-dotted"
                    onClick={() => openV2Detail("instituciones", row.organization_id)}
                  >
                    {row.name}
                  </button>
                </td>
                <td>{row.kind === "unknown" ? "Sin clasificar" : row.kind}</td>
                <td>
                  <ConfirmationBadge confirmation={row.confirmation} />
                </td>
                <td>{row.contact_point_count.toLocaleString("es-CL")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}

      {result?.kind === "prospects" && result.page.items.length > 0 ? (
        <table className="w-full table-auto text-left text-sm" data-testid="v2-prospects-table">
          <thead className="text-xs uppercase text-[var(--color-muted)]">
            <tr>
              <th className="py-2">Oportunidad</th>
              <th>Etapa</th>
              <th>Institución</th>
            </tr>
          </thead>
          <tbody>
            {result.page.items.map((row) => (
              <tr key={row.opportunity_id} className="border-t border-slate-200">
                <td className="py-2 font-medium text-slate-900">{row.title}</td>
                <td>{row.stage}</td>
                <td className="text-[var(--color-muted)]">{row.organization_name ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}

      {result?.kind === "evidence" && result.page.items.length > 0 ? (
        <table className="w-full table-auto text-left text-sm" data-testid="v2-evidence-table">
          <thead className="text-xs uppercase text-[var(--color-muted)]">
            <tr>
              <th className="py-2">Observación</th>
              <th>Tipo</th>
              <th>Resolución</th>
              <th>Procedencia</th>
            </tr>
          </thead>
          <tbody>
            {result.page.items.map((row) => (
              <tr key={row.assertion_id} className="border-t border-slate-200">
                <td className="break-all py-2 text-slate-900">{row.value_norm}</td>
                <td>{row.kind}</td>
                <td>
                  <Badge tone={row.resolution === "promoted" ? "ok" : "warn"}>
                    {resolutionLabel(row.resolution)}
                  </Badge>
                </td>
                <td className="text-[var(--color-muted)]">{sourceKindLabel(row.source_kind)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}

      {page && page.total > 0 ? (
        <div className="flex items-center justify-between text-sm text-[var(--color-muted)]">
          <span data-testid="v2-page-footer">
            {pageFooter(page.total, page.limit || PAGE_SIZE, page.offset)}
          </span>
          <span className="flex gap-2">
            <button
              type="button"
              disabled={page.offset <= 0}
              onClick={() => setOffset(Math.max(0, page.offset - PAGE_SIZE))}
              className="rounded-md border border-slate-300 px-2 py-1 disabled:opacity-40"
            >
              Anterior
            </button>
            <button
              type="button"
              disabled={page.offset + PAGE_SIZE >= page.total}
              onClick={() => setOffset(page.offset + PAGE_SIZE)}
              className="rounded-md border border-slate-300 px-2 py-1 disabled:opacity-40"
            >
              Siguiente
            </button>
          </span>
        </div>
      ) : null}
    </div>
  );
}
