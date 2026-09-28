import { Badge, Panel, ResourceGate, Skeleton, fmtInt } from "../ui";
import { useResource } from "../useResource";
import { santiagoTime } from "./calendar";
import { fetchCampaignAudit } from "./marketingApi";
import type { AuditResponse } from "./marketingTypes";

const EVENT_LABEL: Record<string, string> = {
  "campaign.draft_created": "Borrador creado",
  "campaign.draft_content_saved": "Contenido del borrador guardado",
  "campaign.audience_frozen": "Audiencia congelada",
  "campaign.override_granted": "Recontacto aprobado",
  "campaign.planning_set": "Planificación fijada",
  "campaign.planning_cleared": "Planificación eliminada",
  "campaign.transitioned": "Cambio de estado",
  "campaign.approved": "Aprobada",
  "campaign.dry_run_recorded": "Ensayo registrado",
};

function when(value: string | null): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  return `${d.toLocaleDateString("es-CL", { day: "2-digit", month: "short", year: "numeric", timeZone: "America/Santiago" })} · ${santiagoTime(value)}`;
}

/** Auditoría: the campaign's recorded history in PostgreSQL — its row, its origin, its events. */
export function CampaignAudit({ campaignId }: { campaignId: string }) {
  const [state, reload] = useResource(() => fetchCampaignAudit(campaignId), [campaignId]);
  return (
    <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={4} />}>
      {(a) => <Body a={a} />}
    </ResourceGate>
  );
}

function Body({ a }: { a: AuditResponse }) {
  return (
    <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1fr)_22rem]" data-testid="campaign-audit">
      <Panel title="Eventos de la campaña" note="crm.domain_event — solo anexar" bodyClassName="p-0">
        {a.events.length ? (
          <table className="w-full table-fixed text-xs" data-testid="audit-events">
            <thead className="bg-canvas-sunken text-left text-[11px] font-semibold uppercase tracking-wide text-ink-faint">
              <tr>
                <th className="w-12 px-3 py-2">#</th>
                <th className="px-3 py-2">Evento</th>
                <th className="hidden w-40 px-3 py-2 sm:table-cell">Quién</th>
                <th className="w-36 px-3 py-2">Cuándo</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {a.events.map((e) => (
                <tr key={e.seq} className="align-top">
                  <td className="px-3 py-2 tabular-nums text-ink-faint">{e.seq}</td>
                  <td className="px-3 py-2">
                    <p className="font-medium text-ink">{EVENT_LABEL[e.event_type] ?? e.event_type}</p>
                    <p className="break-words font-mono text-[10px] text-ink-faint">
                      {Object.entries(e.payload)
                        .slice(0, 6)
                        .map(([k, v]) => `${k}: ${String(v)}`)
                        .join(" · ")}
                    </p>
                  </td>
                  <td className="hidden px-3 py-2 text-ink-muted sm:table-cell">{e.actor_name ?? e.actor_kind}</td>
                  <td className="px-3 py-2 tabular-nums text-ink-muted">{when(e.recorded_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="px-4 py-6 text-xs text-ink-muted" data-testid="audit-no-events">
            {a.events_note ?? "Esta campaña no tiene eventos registrados."}
          </p>
        )}
        {a.attempt_events.length ? (
          <p className="border-t border-line px-3 py-2 text-[11px] text-ink-muted">
            Eventos de intentos de envío:{" "}
            {a.attempt_events.map((x) => `${x.event_type} ${fmtInt(x.count)}`).join(" · ")}
          </p>
        ) : null}
      </Panel>
      <div className="space-y-3">
        <Panel title="Estado" bodyClassName="space-y-2 px-3 py-2.5 text-xs">
          {a.immutable ? (
            <p className="rounded-md border border-line bg-canvas-sunken px-2 py-1.5 text-ink" data-testid="audit-immutable">
              <Badge tone={a.immutable_enforced_by_database ? "good" : "warn"}>
                {a.immutable_enforced_by_database ? "Inmutable" : "Sin protección en esta base"}
              </Badge>{" "}
              {a.immutable_note}
            </p>
          ) : null}
          {a.immutable ? (
            <p className="text-[11px] text-ink-faint" data-testid="audit-no-actions">
              Sin acciones: una campaña histórica no se edita, reabre, reenvía ni planifica.
            </p>
          ) : null}
          <dl className="grid grid-cols-[8.5rem_minmax(0,1fr)] gap-x-2 gap-y-1.5">
            <dt className="text-ink-faint">Fila creada</dt>
            <dd className="text-ink">{when(a.row.created_at)}</dd>
            <dt className="text-ink-faint">Última modificación</dt>
            <dd className="text-ink">{when(a.row.updated_at)}</dd>
            <dt className="text-ink-faint">Audiencia congelada</dt>
            <dd className="text-ink">{when(a.row.audience_frozen_at)}</dd>
            <dt className="text-ink-faint">Contenido congelado</dt>
            <dd className="text-ink">{when(a.row.content_frozen_at)}</dd>
            <dt className="text-ink-faint">Aprobada</dt>
            <dd className="text-ink">{when(a.row.approved_at)}</dd>
          </dl>
        </Panel>
        <Panel title="Origen" bodyClassName="space-y-1.5 px-3 py-2.5 text-xs">
          {a.origin.kind === "imported_v1" ? (
            <>
              <p className="text-ink">Importada del registro de envíos de V1 (sistema anterior).</p>
              <dl className="grid grid-cols-[6.5rem_minmax(0,1fr)] gap-x-2 gap-y-1">
                <dt className="text-ink-faint">Manifiesto</dt>
                <dd className="break-all font-mono text-[10px] text-ink">{a.origin.manifest_name ?? "—"}</dd>
                <dt className="text-ink-faint">SHA-256</dt>
                <dd className="break-all font-mono text-[10px] text-ink">{a.origin.payload_sha256 ?? "—"}</dd>
                <dt className="text-ink-faint">Cargada</dt>
                <dd className="text-ink">{when(a.origin.acquired_at)}</dd>
              </dl>
            </>
          ) : (
            <p className="text-ink">Creada en este CRM.</p>
          )}
          <p className="text-[11px] text-ink-faint">Fuente: {a.storage.tables.join(", ")} en {a.storage.database}.</p>
        </Panel>
      </div>
    </div>
  );
}
