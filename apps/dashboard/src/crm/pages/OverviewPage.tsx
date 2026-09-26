import { fetchOverview, fetchPipeline } from "../crmApi";
import type { EntityCount, OpportunityCardData, WorkspaceOverview } from "../crmTypes";
import type { CrmSection } from "../crmRoute";
import { STAGE_LABEL, byLatestSent } from "../stage";
import { Badge, PageHeader, Panel, ProvenanceBadge, ResourceGate, Skeleton, fmtDate, fmtInt } from "../ui";
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
  drive_links_in_crm: "Enlaces de Drive en el CRM",
};

export function OverviewPage({ navigate }: { navigate: (s: CrmSection, id?: string) => void }) {
  const [overview, reloadOverview] = useResource(fetchOverview);
  const [pipeline, reloadPipeline] = useResource(fetchPipeline);
  return (
    <div className="space-y-4">
      <PageHeader
        title="Resumen"
        subtitle="Qué contiene hoy el CRM, qué falta importar y dónde hace falta una decisión."
      />
      <ResourceGate state={overview} reload={reloadOverview} skeleton={<Skeleton rows={4} />}>
        {(o) => <OverviewBody overview={o} navigate={navigate} />}
      </ResourceGate>
      <ResourceGate state={pipeline} reload={reloadPipeline} skeleton={<Skeleton rows={4} />}>
        {(p) => <RecentAndBlocked items={p.items} navigate={navigate} />}
      </ResourceGate>
    </div>
  );
}

function Metric({
  label,
  value,
  hint,
  onClick,
  tone,
}: {
  label: string;
  value: string;
  hint: string;
  onClick?: () => void;
  tone?: "bad";
}) {
  const body = (
    <>
      <p className="text-[11px] font-medium uppercase tracking-wide text-ink-faint">{label}</p>
      <p className={`mt-1 text-2xl font-semibold tabular-nums tracking-tight ${tone === "bad" ? "text-bad" : "text-ink"}`}>{value}</p>
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

function OverviewBody({ overview, navigate }: { overview: WorkspaceOverview; navigate: (s: CrmSection) => void }) {
  const byKey = Object.fromEntries(overview.entities.map((e) => [e.key, e])) as Record<string, EntityCount>;
  const confirmed = overview.organizations_by_confirmation.confirmed ?? 0;
  const drive = overview.drive_archive;
  const openAssertions = overview.assertions
    .filter((a) => a.resolution === "unresolved" || a.resolution === "ambiguous")
    .reduce((n, a) => n + a.count, 0);
  return (
    <>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Metric
          label="Oportunidades"
          value={fmtInt(byKey.opportunities?.count ?? 0)}
          hint={Object.entries(overview.opportunities_by_stage)
            .map(([s, n]) => `${n} ${STAGE_LABEL[s]?.toLowerCase() ?? s}`)
            .join(" · ")}
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
          hint={drive.configured ? `${fmtInt(drive.documents)} documentos en el archivo de casos` : "Registros del archivo no cargados"}
          onClick={() => navigate("drive")}
        />
        <Metric
          label="Por revisar"
          value={fmtInt(openAssertions)}
          hint="Evidencias sin resolver o ambiguas"
          onClick={() => navigate("revision")}
        />
      </div>

      <Panel
        title="Qué hay en el CRM"
        note="Cero no siempre significa vacío: la etiqueta dice si se importó."
        bodyClassName="divide-y divide-line"
      >
        {overview.entities.map((e) => (
          <div key={e.key} className="grid grid-cols-[1fr_auto] items-start gap-x-3 gap-y-0.5 px-3 py-2 sm:grid-cols-[14rem_6rem_9rem_1fr]">
            <p className="text-[13px] font-medium text-ink">{ENTITY_LABEL[e.key] ?? e.key}</p>
            <p className="text-right text-[13px] font-semibold tabular-nums text-ink sm:text-left">{fmtInt(e.count)}</p>
            <div>
              <ProvenanceBadge provenance={e.provenance} />
            </div>
            <p className="col-span-2 text-[11px] leading-4 text-ink-muted sm:col-span-1">
              {e.note}
              {e.key === "organizations" ? ` ${fmtInt(confirmed)} confirmadas por un operador.` : ""}
              {e.key === "contact_points"
                ? ` Vinculadas: ${overview.contact_points_linked.organization} a institución, ${overview.contact_points_linked.person} a persona.`
                : ""}
            </p>
          </div>
        ))}
      </Panel>
    </>
  );
}

function RecentAndBlocked({
  items,
  navigate,
}: {
  items: OpportunityCardData[];
  navigate: (s: CrmSection, id?: string) => void;
}) {
  const recent = [...items].sort(byLatestSent).slice(0, 6);
  const blocked = items.filter((i) => i.status === "blocked");
  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
      <Panel title="Últimas cotizaciones enviadas" bodyClassName="divide-y divide-line">
        {recent.map((c) => (
          <button
            key={c.opportunity_id}
            type="button"
            onClick={() => navigate("oportunidades", c.opportunity_id)}
            className="flex w-full items-center gap-3 px-3 py-2 text-left hover:bg-canvas-sunken/50 focus:outline-none focus-visible:bg-canvas-sunken"
          >
            <span className="w-24 shrink-0 truncate text-xs font-semibold tabular-nums text-ink">{c.latest_revision?.quote_number ?? c.quote_numbers[0] ?? "—"}</span>
            <span className="min-w-0 flex-1 truncate text-[13px] text-ink">{c.organization?.name ?? "Sin institución"}</span>
            <span className="hidden w-24 shrink-0 text-right text-[11px] text-ink-faint sm:block">{fmtDate(c.latest_revision?.sent_at)}</span>
          </button>
        ))}
      </Panel>
      <Panel title="Requieren decisión" aside={<Badge tone={blocked.length ? "bad" : "good"}>{blocked.length}</Badge>} bodyClassName="divide-y divide-line">
        {blocked.length === 0 ? (
          <p className="px-3 py-4 text-xs text-ink-muted">Ninguna oportunidad bloqueada.</p>
        ) : (
          blocked.map((c) => (
            <button
              key={c.opportunity_id}
              type="button"
              onClick={() => navigate("oportunidades", c.opportunity_id)}
              className="block w-full px-3 py-2 text-left hover:bg-canvas-sunken/50 focus:outline-none focus-visible:bg-canvas-sunken"
            >
              <span className="block truncate text-[13px] font-medium text-ink">{c.organization?.name ?? c.title}</span>
              <span className="block truncate text-[11px] text-bad">{c.attention.find((a) => a.blocking)?.label}</span>
            </button>
          ))
        )}
      </Panel>
    </div>
  );
}
