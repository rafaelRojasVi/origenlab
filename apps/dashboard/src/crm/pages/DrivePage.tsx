import { useMemo, useState } from "react";
import { fetchDriveArchive } from "../crmApi";
import type { DriveArchiveResponse, DriveFolder } from "../crmTypes";
import type { CrmSection } from "../crmRoute";
import {
  Badge,
  EmptyState,
  ExternalLink,
  NotImportedState,
  PageHeader,
  ResourceGate,
  SearchInput,
  Segmented,
  Skeleton,
  StatLine,
} from "../ui";
import { useResource } from "../useResource";

type Filter = "all" | "in_crm" | "not_in_crm";

const LEDGER_STATUS_LABEL: Record<string, string> = {
  held: "Retenido por el owner",
  pending_organization_confirmation: "Falta confirmar institución",
  ready_to_import: "Listo para importar",
};

export function DrivePage({ navigate }: { navigate: (s: CrmSection, id?: string) => void }) {
  const [state, reload] = useResource(fetchDriveArchive);
  return (
    <div className="space-y-3">
      <PageHeader
        title="Archivo Drive"
        subtitle="Carpetas de caso en Drive › Cotizaciones › Casos, y si cada PDF ya está en el CRM. Sólo lectura: el panel no llama a Drive."
      />
      <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={8} />}>
        {(data) =>
          !data.configured ? (
            <NotImportedState title="Los registros del archivo de Drive no están cargados">
              Este API arrancó sin <code>ORIGENLAB_V2_DRIVE_ARCHIVE_LEDGERS</code>. Los enlaces de Drive no están en el CRM
              (<code>crm.external_identifier</code> está vacío); viven sólo en los registros de las corridas del archivo de casos.
            </NotImportedState>
          ) : (
            <Archive data={data} navigate={navigate} />
          )
        }
      </ResourceGate>
    </div>
  );
}

function Archive({ data, navigate }: { data: DriveArchiveResponse; navigate: (s: CrmSection, id?: string) => void }) {
  const [filter, setFilter] = useState<Filter>("all");
  const [q, setQ] = useState("");
  const folders = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return data.folders.filter((f) => {
      if (filter === "in_crm" && f.in_crm === 0) return false;
      if (filter === "not_in_crm" && f.in_crm === f.documents.length) return false;
      if (!needle) return true;
      return [f.organization_name ?? "", ...f.quote_numbers, ...f.documents.map((d) => d.original_filename ?? "")]
        .join(" ")
        .toLowerCase()
        .includes(needle);
    });
  }, [data.folders, filter, q]);
  const partial = data.folders.filter((f) => f.in_crm > 0 && f.in_crm < f.documents.length).length;
  return (
    <>
      <StatLine
        items={[
          { label: "Carpetas de caso", value: data.totals.folders },
          { label: "PDF archivados", value: data.totals.documents },
          { label: "En el CRM", value: data.totals.in_crm, tone: "good" },
          { label: "No importados", value: data.totals.not_in_crm, tone: data.totals.not_in_crm ? "warn" : undefined },
          {
            label: "Revisiones CRM sin PDF en Drive",
            value: data.crm_revisions_without_drive_file.length,
            tone: data.crm_revisions_without_drive_file.length ? "bad" : "good",
          },
        ]}
      />
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <Segmented
          label="Estado en el CRM"
          value={filter}
          onChange={setFilter}
          options={[
            { value: "all", label: "Todas" },
            { value: "in_crm", label: "Con PDF en el CRM" },
            { value: "not_in_crm", label: "Con PDF no importado" },
          ]}
        />
        <SearchInput value={q} onChange={setQ} label="Buscar en el archivo" placeholder="Número, institución, archivo…" />
        <span className="text-xs tabular-nums text-ink-faint">{folders.length} carpetas{partial ? ` · ${partial} mixtas` : ""}</span>
      </div>
      {folders.length === 0 ? (
        <EmptyState title="Ninguna carpeta coincide" />
      ) : (
        <div className="overflow-hidden rounded-lg border border-line bg-canvas-raised">
          <div className="flex items-center gap-2 border-b border-line px-3 py-2">
            <h2 className="text-[13px] font-semibold text-ink">Cotizaciones › Casos</h2>
            <Badge glyph={false}>Drive · sólo lectura</Badge>
            <span className="ml-auto hidden text-[11px] text-ink-faint sm:inline">{data.ledgers.length} registros de archivo</span>
          </div>
          <ul className="divide-y divide-line">
            {folders.map((f) => (
              <FolderRow key={f.folder_id ?? f.case_key} folder={f} navigate={navigate} />
            ))}
          </ul>
        </div>
      )}
    </>
  );
}

function FolderRow({ folder, navigate }: { folder: DriveFolder; navigate: (s: CrmSection, id?: string) => void }) {
  const [open, setOpen] = useState(false);
  const first = folder.documents[0];
  const title = folder.organization_name ?? first?.original_filename?.replace(/\.pdf$/i, "") ?? "Caso";
  const all = folder.in_crm === folder.documents.length;
  return (
    <li>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 hover:bg-canvas-sunken/40">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="flex min-w-0 flex-1 items-center gap-2 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
        >
          <span aria-hidden="true" className={`text-[10px] text-ink-faint transition-transform ${open ? "rotate-90" : ""}`}>
            ▶
          </span>
          <span className="w-24 shrink-0 truncate text-xs font-semibold tabular-nums text-ink">{folder.quote_numbers[0] ?? "—"}</span>
          <span className="min-w-0 flex-1 truncate text-[13px] text-ink">{title}</span>
        </button>
        <span className="text-[11px] tabular-nums text-ink-muted">
          {folder.documents.length} PDF
        </span>
        {all ? (
          <Badge tone="good">En CRM</Badge>
        ) : folder.in_crm === 0 ? (
          <Badge tone="warn">No importado</Badge>
        ) : (
          <Badge tone="warn">
            {folder.in_crm}/{folder.documents.length} en CRM
          </Badge>
        )}
        {folder.folder_url ? (
          <span className="text-xs">
            <ExternalLink href={folder.folder_url} label={`Abrir carpeta ${folder.quote_numbers[0] ?? ""} en Drive`}>
              Abrir en Drive
            </ExternalLink>
          </span>
        ) : null}
      </div>
      {open ? (
        <ul className="border-t border-line/70 bg-canvas-sunken/40 px-3 py-2">
          {folder.documents.map((d) => (
            <li key={d.document_sha256} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-1.5 text-xs">
              <span className="w-24 shrink-0 font-medium tabular-nums text-ink">
                {d.quote_number ?? "—"}
                {d.revision ? <span className="text-ink-faint"> r{d.revision}</span> : null}
              </span>
              <span className="min-w-0 flex-1 truncate text-ink-muted" title={d.original_filename ?? undefined}>
                {d.original_filename ?? d.document_sha256.slice(0, 16)}
              </span>
              {d.in_crm && d.crm ? (
                <button
                  type="button"
                  onClick={() => navigate("oportunidades", d.crm!.opportunity_id)}
                  className="text-[11px] font-medium text-good hover:underline"
                >
                  ✓ En CRM
                </button>
              ) : (
                <Badge tone="warn">{LEDGER_STATUS_LABEL[d.ledger_crm_status ?? ""] ?? "No importado"}</Badge>
              )}
              <ExternalLink href={d.file_url}>PDF</ExternalLink>
              {d.gmail_url ? <ExternalLink href={d.gmail_url}>Gmail</ExternalLink> : null}
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}
