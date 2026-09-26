import { fetchMarketing } from "../crmApi";
import type { CampaignSummary, MarketingResponse } from "../crmTypes";
import {
  Badge,
  DisabledAction,
  EmptyState,
  NotImportedState,
  PageHeader,
  Panel,
  ResourceGate,
  Skeleton,
  StatLine,
  WRITE_DISABLED_REASON,
  fmtDate,
  fmtInt,
} from "../ui";
import { useResource } from "../useResource";

const RECIPIENT_STATE: Record<string, { label: string; color: string }> = {
  sent: { label: "Enviado", color: "bg-brand-600" },
  bounced: { label: "Rebotado", color: "bg-bad" },
  excluded: { label: "Excluido", color: "bg-line-strong" },
  snapshotted: { label: "En la audiencia, no enviado", color: "bg-warn" },
};

const CONTROL_LABEL: Record<string, string> = {
  "block:address": "Bloqueo por dirección",
  "block:domain": "Bloqueo por dominio",
  "prior_contact:address": "Contacto previo registrado",
};

const STATUS_LABEL: Record<string, string> = { archived: "Archivada", draft: "Borrador", approved: "Aprobada", sending: "Enviando" };

export function MarketingPage() {
  const [state, reload] = useResource(fetchMarketing);
  return (
    <div className="space-y-4">
      <PageHeader
        title="Marketing"
        subtitle="Campañas de correo registradas en el CRM: audiencia, envíos y controles de contacto. Sólo cifras registradas; nada estimado."
        actions={<DisabledAction id="marketing-new-disabled" reason={WRITE_DISABLED_REASON}>Nueva campaña</DisabledAction>}
      />
      <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={3} cards />}>
        {(data) => <Body data={data} />}
      </ResourceGate>
    </div>
  );
}

function Body({ data }: { data: MarketingResponse }) {
  if (data.campaigns.length === 0) {
    return <EmptyState title="Sin campañas">El CRM no tiene campañas registradas.</EmptyState>;
  }
  const totalSent = data.campaigns.reduce((n, c) => n + (c.recipients_by_state.sent ?? 0), 0);
  const totalBounced = data.campaigns.reduce((n, c) => n + (c.recipients_by_state.bounced ?? 0), 0);
  const replies = data.campaigns.reduce((n, c) => n + c.replies_recorded, 0);
  return (
    <>
      <StatLine
        items={[
          { label: "Campañas", value: data.campaigns.length },
          { label: "Destinatarios enviados", value: fmtInt(totalSent) },
          { label: "Rebotes registrados", value: fmtInt(totalBounced), tone: totalBounced ? "warn" : undefined },
          { label: "Respuestas", value: replies === 0 ? "no importadas" : fmtInt(replies) },
        ]}
      />
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
        {data.campaigns.map((c) => (
          <CampaignCard key={c.campaign_id} c={c} />
        ))}
      </div>
      {replies === 0 ? <NotImportedState title="Respuestas a campañas">{data.replies_note}</NotImportedState> : null}
      <p className="text-[11px] text-ink-faint">
        Aperturas y clics no se registran en el CRM y no se muestran. El estado de entrega de cada envío figura como «pendiente»
        porque el CRM no ha recibido confirmaciones de entrega.
      </p>
      <Panel title="Controles de contacto" note="outbound.contact_control — se aplican antes de cualquier envío" bodyClassName="divide-y divide-line">
        {data.contact_controls.map((cc) => (
          <div key={`${cc.kind}:${cc.scope}`} className="flex items-center gap-3 px-3 py-2">
            <span className="min-w-0 flex-1 text-[13px] text-ink">{CONTROL_LABEL[`${cc.kind}:${cc.scope}`] ?? `${cc.kind} · ${cc.scope}`}</span>
            <span className="text-[13px] font-semibold tabular-nums text-ink">{fmtInt(cc.count)}</span>
          </div>
        ))}
      </Panel>
    </>
  );
}

function CampaignCard({ c }: { c: CampaignSummary }) {
  const states = Object.entries(c.recipients_by_state).sort((a, b) => b[1] - a[1]);
  const total = states.reduce((n, [, v]) => n + v, 0);
  const attempts = c.send_attempts.reduce((n, a) => n + a.count, 0);
  const rejected = c.send_attempts.filter((a) => a.submission_state === "rejected").reduce((n, a) => n + a.count, 0);
  return (
    <article className="flex flex-col rounded-lg border border-line bg-canvas-raised p-3.5">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-[13px] font-semibold text-ink" title={c.name}>
            {c.name}
          </h3>
          <p className="truncate text-xs text-ink-muted" title={c.subject ?? undefined}>
            {c.subject ?? "Sin asunto registrado"}
          </p>
        </div>
        <Badge glyph={false}>{STATUS_LABEL[c.status] ?? c.status}</Badge>
      </div>
      <p className="mt-3 text-2xl font-semibold tabular-nums tracking-tight text-ink">{fmtInt(total)}</p>
      <p className="text-[11px] text-ink-faint">destinatarios en la audiencia</p>
      <div className="mt-2 flex h-2 overflow-hidden rounded-full bg-canvas-sunken" role="img" aria-label={states.map(([s, n]) => `${RECIPIENT_STATE[s]?.label ?? s}: ${n}`).join(", ")}>
        {states.map(([s, n]) => (
          <span key={s} className={RECIPIENT_STATE[s]?.color ?? "bg-ink-faint"} style={{ width: `${(n / Math.max(total, 1)) * 100}%` }} />
        ))}
      </div>
      <dl className="mt-2 space-y-1 text-xs">
        {states.map(([s, n]) => (
          <div key={s} className="flex items-center gap-2">
            <span aria-hidden="true" className={`h-2 w-2 rounded-full ${RECIPIENT_STATE[s]?.color ?? "bg-ink-faint"}`} />
            <dt className="flex-1 text-ink-muted">{RECIPIENT_STATE[s]?.label ?? s}</dt>
            <dd className="font-semibold tabular-nums text-ink">{fmtInt(n)}</dd>
          </div>
        ))}
      </dl>
      <div className="mt-auto border-t border-line/70 pt-2 text-[11px] text-ink-faint [margin-top:0.75rem]">
        {fmtInt(attempts)} intentos de envío{rejected ? ` · ${rejected} rechazados` : ""} · {fmtDate(c.first_sent_at)}
        {c.last_sent_at && c.last_sent_at.slice(0, 10) !== c.first_sent_at?.slice(0, 10) ? ` – ${fmtDate(c.last_sent_at)}` : ""}
      </div>
    </article>
  );
}
