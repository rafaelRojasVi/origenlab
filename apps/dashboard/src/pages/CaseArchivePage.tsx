/**
 * Archivo de casos — one commercial case with all its quotations and revisions.
 *
 * Each case shows two separate facts: its **CRM status** (from the import plan until the CRM
 * holds the case — the page says which) and, per document, its **archive status** (where the
 * bytes are in Drive). A document is never shown as CRM-ready because it is in Drive. Pending
 * reasons, number collisions, missing organizations and revisions are shown on the case itself.
 * Each revision links to its Drive file (case folder or legacy folder) and its Gmail message.
 *
 * **Read-only.** One GET; the route exists only when the API runs with ORIGENLAB_V2_CASE_ARCHIVE_DIR.
 */

import { useEffect, useMemo, useState } from "react";

import {
  fetchCaseArchive,
  type ArchiveCase,
  type ArchiveStatus,
  type CaseArchiveView,
  type CrmStatus,
  type DriveLink,
} from "../api/caseArchive";
import { V2Chip } from "../components/v2/V2Chip";
import { V2EmptyState } from "../components/v2/V2EmptyState";
import { V2PageHeader } from "../components/v2/V2PageHeader";
import { V2Panel } from "../components/v2/V2Panel";
import { formatMirrorLoadError } from "../lib/humanizeApiError";
import {
  ARCHIVE_STATUS_LABELS,
  ARCHIVE_STATUS_TONES,
  CRM_STATUS_LABELS,
  EMPTY_CASE_FILTER,
  FLAG_LABELS,
  crmChip,
  filterCases,
  shortSha,
  type CaseArchiveFilter,
} from "../lib/caseArchive";

const PAGE_SIZE = 25;

function date(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleDateString("es-CL", { dateStyle: "medium" });
}

function ExtLink({ url, label }: { url: string | null | undefined; label: string }) {
  if (!url) return <span className="text-slate-400">{label}: sin enlace</span>;
  return (
    <a href={url} target="_blank" rel="noreferrer noopener" className="text-sky-700 underline">
      {label}
    </a>
  );
}

function DriveLinks({ file, legacy }: { file: DriveLink | null; legacy: DriveLink[] }) {
  return (
    <div className="space-y-0.5 text-xs">
      {file && <div><ExtLink url={file.url} label="Drive (caso)" /></div>}
      {legacy.map((l) => (
        <div key={l.id} title={l.path ?? undefined}>
          <ExtLink url={l.url} label="Drive (carpeta antigua)" />
        </div>
      ))}
      {!file && legacy.length === 0 && <span className="text-slate-400">sin archivo en Drive</span>}
    </div>
  );
}

function Tiles({ view }: { view: CaseArchiveView }) {
  const c = view.counts;
  const tiles: [string, number | undefined][] = [
    ["Casos", c.cases],
    [CRM_STATUS_LABELS.ready_to_import, c.by_crm_status.ready_to_import],
    [CRM_STATUS_LABELS.pending_organization_confirmation, c.by_crm_status.pending_organization_confirmation],
    [CRM_STATUS_LABELS.held, c.by_crm_status.held],
    [ARCHIVE_STATUS_LABELS.archived_verified, c.by_archive_status.archived_verified],
    [ARCHIVE_STATUS_LABELS.legacy_only, c.by_archive_status.legacy_only],
    [ARCHIVE_STATUS_LABELS.not_archived, c.by_archive_status.not_archived],
    ["Casos con número compartido", c.cases_with_collisions],
    ["Documentos antiguos sin caso", c.unplaced_legacy_documents],
  ];
  return (
    <div className="grid grid-cols-3 gap-2 sm:grid-cols-5 lg:grid-cols-9" data-testid="case-archive-counts">
      {tiles.map(([label, value]) => (
        <div key={label} className="rounded border border-slate-200 bg-white p-2">
          <div className="text-xl font-semibold text-slate-900">{(value ?? 0).toLocaleString("es-CL")}</div>
          <div className="text-xs text-slate-600">{label}</div>
        </div>
      ))}
    </div>
  );
}

function Filters({ value, onChange }: { value: CaseArchiveFilter; onChange: (f: CaseArchiveFilter) => void }) {
  const input = "rounded border border-slate-300 px-2 py-1 text-sm";
  return (
    <div className="flex flex-wrap items-end gap-2" data-testid="case-archive-filters">
      <label className="text-xs text-slate-600">
        Buscar (n.º, destinatario, institución)
        <input aria-label="Buscar" className={`${input} block`} value={value.text}
          onChange={(e) => onChange({ ...value, text: e.target.value })} />
      </label>
      <label className="text-xs text-slate-600">
        Estado CRM
        <select aria-label="Estado CRM" className={`${input} block`} value={value.crm}
          onChange={(e) => onChange({ ...value, crm: e.target.value as CrmStatus | "all" })}>
          <option value="all">Todos</option>
          {(Object.keys(CRM_STATUS_LABELS) as CrmStatus[]).map((k) => <option key={k} value={k}>{CRM_STATUS_LABELS[k]}</option>)}
        </select>
      </label>
      <label className="text-xs text-slate-600">
        Estado de archivo
        <select aria-label="Estado de archivo" className={`${input} block`} value={value.archive}
          onChange={(e) => onChange({ ...value, archive: e.target.value as ArchiveStatus | "all" })}>
          <option value="all">Todos</option>
          {(Object.keys(ARCHIVE_STATUS_LABELS) as ArchiveStatus[]).map((k) => <option key={k} value={k}>{ARCHIVE_STATUS_LABELS[k]}</option>)}
        </select>
      </label>
      <label className="text-xs text-slate-600">
        Alerta
        <select aria-label="Alerta" className={`${input} block`} value={value.flag}
          onChange={(e) => onChange({ ...value, flag: e.target.value })}>
          <option value="all">Todas</option>
          {Object.entries(FLAG_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      </label>
    </div>
  );
}

function CaseCard({ c }: { c: ArchiveCase }) {
  const chip = crmChip(c);
  return (
    <article className="rounded border border-slate-200 bg-white p-3" data-testid="case-archive-card">
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="font-semibold text-slate-900">{c.folder_name ?? c.case_key}</h3>
          <div className="text-xs text-slate-500">
            {c.printed_organization ?? "sin institución impresa"} · caso <span className="font-mono">{c.case_key.slice(0, 8)}</span>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-1">
          <span className="text-xs text-slate-500">CRM ({c.crm.source === "import_plan" ? "plan" : "CRM"}):</span>
          <V2Chip tone={chip.tone}>{chip.label}</V2Chip>
          <span className="ml-2 text-xs text-slate-500">Drive:</span>
          {c.archive.folder ? (
            <ExtLink url={c.archive.folder.url} label="carpeta del caso" />
          ) : (
            <V2Chip tone="neutral">{c.archive.planned_folder_action === "create_case_folder" ? "carpeta por crear" : "sin carpeta"}</V2Chip>
          )}
        </div>
      </header>
      {(c.flags.length > 0 || c.crm.reasons.length > 0) && (
        <div className="mt-2 space-y-1">
          <div className="flex flex-wrap gap-1">
            {c.flags.map((f) => (
              <V2Chip key={f} tone={f === "number_collision" || f === "missing_organization" || f === "pending_document" ? "warn" : "neutral"}>
                {FLAG_LABELS[f] ?? f}
              </V2Chip>
            ))}
          </div>
          {c.crm.reasons.length > 0 && (
            <ul className="list-disc pl-5 text-xs text-amber-900" data-testid="case-archive-reasons">
              {c.crm.reasons.map((r) => <li key={r.code}>Pendiente: {r.label}</li>)}
            </ul>
          )}
        </div>
      )}
      <table className="mt-2 w-full text-left text-sm [&_td]:px-2 [&_td]:py-1 [&_th]:px-2">
        <thead className="text-xs text-slate-500">
          <tr><th>Cotización</th><th>Rev.</th><th>Enviada</th><th>Estado del documento</th><th>Archivo</th><th>Drive</th><th>Gmail</th><th>SHA-256</th></tr>
        </thead>
        <tbody>
          {c.quotes.flatMap((q) =>
            q.revisions.map((r) => (
              <tr key={r.document_sha256} className="border-t border-slate-100 align-top">
                <td className="font-mono text-xs">
                  {q.quote_number}
                  {q.collision && (
                    <div className="mt-0.5"><V2Chip tone="danger">
                      {q.collision.kind === "several_cases"
                        ? `número compartido por ${q.collision.cases.length} casos`
                        : "número compartido con un documento sin caso"}
                    </V2Chip></div>
                  )}
                </td>
                <td>r{r.revision_no}</td>
                <td className="text-xs">{date(r.sent_at)}</td>
                <td className="text-xs">{r.lifecycle_label}</td>
                <td><V2Chip tone={ARCHIVE_STATUS_TONES[r.archive.status]}>{ARCHIVE_STATUS_LABELS[r.archive.status]}</V2Chip></td>
                <td><DriveLinks file={r.archive.file} legacy={r.archive.legacy_files} /></td>
                <td className="text-xs"><ExtLink url={r.gmail_url} label="Gmail" /></td>
                <td className="font-mono text-xs" title={r.document_sha256}>{shortSha(r.document_sha256)}</td>
              </tr>
            )),
          )}
        </tbody>
      </table>
    </article>
  );
}

export function CaseArchivePage() {
  const [view, setView] = useState<CaseArchiveView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<CaseArchiveFilter>(EMPTY_CASE_FILTER);
  const [limit, setLimit] = useState(PAGE_SIZE);

  useEffect(() => {
    let live = true;
    fetchCaseArchive()
      .then((v) => live && setView(v))
      .catch((e: unknown) => live && setError(formatMirrorLoadError("Archivo de casos", e).message));
    return () => {
      live = false;
    };
  }, []);

  const cases = useMemo(() => (view ? filterCases(view.cases, filter) : []), [view, filter]);

  if (error) {
    return (
      <V2EmptyState title="No se pudo leer el archivo de casos"
        description={`${error} — la ruta existe sólo si la API se inició con ORIGENLAB_V2_CASE_ARCHIVE_DIR.`} />
    );
  }
  if (!view) return <p className="p-4 text-sm text-slate-600">Cargando…</p>;

  return (
    <div className="space-y-4 p-4" data-testid="case-archive">
      <V2PageHeader
        title="Archivo de casos"
        subtitle={`Un caso por oportunidad, con todas sus cotizaciones y revisiones · inventario Drive ${date(view.source.inventory_finished_at)}`}
      />
      <p className="rounded bg-amber-50 p-2 text-sm text-amber-900">
        Sólo lectura. El estado CRM viene del plan de importación ({view.source.plan_version ?? "—"}): el CRM aún no
        tiene cotizaciones. El estado de archivo dice dónde están los bytes en Drive y nunca cambia el estado CRM.
      </p>
      <Tiles view={view} />
      <Filters value={filter} onChange={(f) => { setFilter(f); setLimit(PAGE_SIZE); }} />
      <V2Panel title="Casos" count={cases.length} testId="case-archive-cases">
        {cases.length ? (
          <div className="space-y-3">
            {cases.slice(0, limit).map((c) => <CaseCard key={c.case_key} c={c} />)}
            {cases.length > limit && (
              <button type="button" className="rounded border border-slate-300 px-3 py-1 text-sm"
                onClick={() => setLimit(limit + PAGE_SIZE)}>
                Mostrar más ({cases.length - limit} restantes)
              </button>
            )}
          </div>
        ) : (
          <p className="text-sm text-slate-500">Ningún caso con estos filtros.</p>
        )}
      </V2Panel>
      <V2Panel title="Documentos en carpetas antiguas sin caso" count={view.unplaced_legacy_documents.length}
        testId="case-archive-unplaced">
        <table className="w-full text-left text-sm [&_td]:px-2 [&_td]:py-1 [&_th]:px-2">
          <thead className="text-xs text-slate-500">
            <tr><th>Archivo</th><th>N.º impreso</th><th>Clasificación</th><th>Motivo</th><th>Drive</th></tr>
          </thead>
          <tbody>
            {view.unplaced_legacy_documents.map((u) => (
              <tr key={u.drive_id} className="border-t border-slate-100 align-top">
                <td className="text-xs">{u.path.replace(/^Cotizaciones\//, "")}</td>
                <td className="font-mono text-xs">{u.printed_quote_number ?? "—"}</td>
                <td className="text-xs">{u.classification}</td>
                <td className="text-xs">{u.reason}</td>
                <td className="text-xs"><ExtLink url={u.url} label="abrir" /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </V2Panel>
    </div>
  );
}
