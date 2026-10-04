import { fetchOverview } from "../crmApi";
import type { EntityCount, WorkspaceOverview } from "../crmTypes";
import type { CrmSection } from "../crmRoute";
import { STAGE_LABEL } from "../stage";
import { Panel, ProvenanceBadge, ResourceGate, Skeleton, fmtInt } from "../ui";
import { useResource } from "../useResource";

const ENTITY_LABEL: Record<string, string> = {
  opportunities: "Oportunidades",
  quotes: "Cotizaciones",
  quote_revisions: "Revisiones de cotización",
  organizations: "Organizaciones",
  contact_points: "Direcciones de contacto",
  persons: "Personas",
  affiliations: "Afiliaciones persona–institución",
  tasks: "Tareas",
  activities: "Actividades",
  messages: "Mensajes (comms)",
  products: "Productos del catálogo",
  campaigns: "Campañas",
  campaign_replies: "Respuestas a campañas",
  drive_links_in_crm: "Enlaces de Drive guardados en el CRM",
};

/**
 * What the CRM holds, what was imported and what is still missing: the counts that used to open
 * the Resumen. Mounted only when its tab is opened, so the slowest read runs only on request.
 */
export function DataHealth({ navigate }: { navigate: (s: CrmSection) => void }) {
  const [overview, reload] = useResource(fetchOverview);
  return (
    <ResourceGate state={overview} reload={reload} skeleton={<Skeleton rows={6} />}>
      {(o) => <DataHealthBody overview={o} navigate={navigate} />}
    </ResourceGate>
  );
}

function Metric({ label, value, hint, onClick }: { label: string; value: string; hint: string; onClick?: () => void }) {
  const body = (
    <>
      <p className="text-[11px] font-medium uppercase tracking-wide text-ink-faint">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums tracking-tight text-ink">{value}</p>
      <p className="mt-0.5 text-[11px] text-ink-muted">{hint}</p>
    </>
  );
  return onClick ? (
    <button
      type="button"
      onClick={onClick}
      className="rounded-lg border border-line bg-canvas-raised px-3.5 py-3 text-left transition-colors hover:border-line-strong hover:shadow-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
    >
      {body}
    </button>
  ) : (
    <div className="rounded-lg border border-line bg-canvas-raised px-3.5 py-3">{body}</div>
  );
}

function DataHealthBody({ overview, navigate }: { overview: WorkspaceOverview; navigate: (s: CrmSection) => void }) {
  const byKey = Object.fromEntries(overview.entities.map((e) => [e.key, e])) as Record<string, EntityCount>;
  const confirmed = overview.organizations_by_confirmation.confirmed ?? 0;
  const drive = overview.drive_archive;
  const openAssertions = overview.assertions
    .filter((a) => a.resolution === "unresolved" || a.resolution === "ambiguous")
    .reduce((n, a) => n + a.count, 0);
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Metric
          label="Oportunidades"
          value={fmtInt(byKey.opportunities?.count ?? 0)}
          hint={Object.entries(overview.opportunities_by_stage)
            .map(([s, n]) => `${n} ${STAGE_LABEL[s]?.toLowerCase() ?? s}`)
            .join(", ")}
          onClick={() => navigate("oportunidades")}
        />
        <Metric
          label="Cotizaciones"
          value={fmtInt(byKey.quotes?.count ?? 0)}
          hint={`${fmtInt(byKey.quote_revisions?.count ?? 0)} revisiones enviadas`}
          onClick={() => navigate("oportunidades")}
        />
        <Metric
          label="PDF en Drive"
          value={drive.configured ? `${drive.revisions_with_drive_file}/${drive.revisions_total}` : "—"}
          hint={
            drive.configured
              ? `Revisiones con su PDF, según el registro de la carga a Drive (${fmtInt(drive.documents)} documentos). Aún no guardados en el CRM.`
              : "Registros de la carga a Drive no cargados"
          }
          onClick={() => navigate("drive")}
        />
        <Metric label="Evidencias por revisar" value={fmtInt(openAssertions)} hint="Sin resolver o ambiguas" />
      </div>

      <Panel title="Qué hay en el CRM" note="Cero no siempre significa vacío: la etiqueta dice si se importó." bodyClassName="divide-y divide-line">
        {overview.entities.map((e) => (
          <div
            key={e.key}
            className="grid grid-cols-[1fr_auto] items-start gap-x-3 gap-y-0.5 px-3 py-2 sm:grid-cols-[14rem_6rem_9rem_minmax(0,1fr)]"
          >
            <p className="text-[13px] font-medium text-ink">{ENTITY_LABEL[e.key] ?? e.key}</p>
            <p className="text-right text-[13px] font-semibold tabular-nums text-ink sm:text-left">{fmtInt(e.count)}</p>
            <div>
              <ProvenanceBadge provenance={e.provenance} />
            </div>
            <p className="col-span-2 min-w-0 break-words text-[11px] leading-4 text-ink-muted sm:col-span-1">
              {e.note}
              {e.key === "organizations" ? ` ${fmtInt(confirmed)} confirmadas por un operador.` : ""}
              {e.key === "contact_points"
                ? ` Vinculadas: ${overview.contact_points_linked.organization} a institución, ${overview.contact_points_linked.person} a persona.`
                : ""}
            </p>
          </div>
        ))}
      </Panel>
    </div>
  );
}
