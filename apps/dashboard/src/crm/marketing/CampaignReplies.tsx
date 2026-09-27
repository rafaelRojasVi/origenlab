import { Badge, ExternalLink, Panel, ResourceGate, Skeleton, fmtDate, fmtInt, type Tone } from "../ui";
import { useResource } from "../useResource";
import { fetchCampaignReplies } from "./marketingApi";
import type { ReplyItem, RepliesResponse } from "./marketingTypes";

const KIND: Record<ReplyItem["kind"], { label: string; tone: Tone }> = {
  reply: { label: "Respuesta", tone: "info" },
  baja: { label: "BAJA / REMOVER", tone: "bad" },
  baja_pending_review: { label: "BAJA en revisión", tone: "warn" },
};

const ASSOCIATION: Record<ReplyItem["association"], string> = {
  recipient: "Guardada como respuesta de este destinatario",
  send_lineage: "Responde a un correo enviado por esta campaña (In-Reply-To comprobado)",
  address_match: "Asociada sólo por dirección: el remitente figura en la audiencia; no se sabe a qué correo respondió",
};

/**
 * Respuestas: only what the CRM stores with lineage. Nothing is fetched from Gmail. With nothing
 * stored the tab says the replies were never synchronized — it never shows a zero.
 */
export function CampaignReplies({ campaignId }: { campaignId: string }) {
  const [state, reload] = useResource(() => fetchCampaignReplies(campaignId), [campaignId]);
  return (
    <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={4} />}>
      {(d) => <Body d={d} />}
    </ResourceGate>
  );
}

function Body({ d }: { d: RepliesResponse }) {
  return (
    <div className="space-y-3" data-testid="campaign-replies">
      {d.sync.state === "not_synced" ? (
        <div role="status" className="rounded-lg border border-line bg-canvas-sunken/70 px-4 py-4" data-testid="replies-not-synced">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="neutral">Sin sincronizar</Badge>
            <p className="text-[13px] font-semibold text-ink">{d.sync.label}</p>
          </div>
          <p className="mt-1.5 max-w-2xl text-xs leading-5 text-ink-muted">
            El CRM no tiene ninguna respuesta guardada de esta campaña y nada lee Gmail automáticamente. Esto no significa que
            nadie haya respondido: las respuestas simplemente no se han traído al CRM.
          </p>
        </div>
      ) : (
        <p className="text-xs text-ink-muted">{d.sync.label}</p>
      )}
      {d.items.length ? (
        <Panel title="Respuestas guardadas" note={`${fmtInt(d.items.length)} registradas`} bodyClassName="divide-y divide-line">
          {d.items.map((i) => (
            <article key={`${i.kind}-${i.id}`} className="space-y-1 px-3 py-2.5 text-xs" data-testid="reply-item" data-kind={i.kind}>
              <div className="flex flex-wrap items-center gap-1.5">
                <Badge tone={KIND[i.kind].tone}>{KIND[i.kind].label}</Badge>
                {i.class_label !== KIND[i.kind].label ? <Badge glyph={false}>{i.class_label}</Badge> : null}
                <span className="ml-auto tabular-nums text-ink-muted">{i.received_at ? fmtDate(i.received_at) : "sin fecha"}</span>
              </div>
              <p className="text-ink">
                {i.person_name ? <b>{i.person_name}</b> : null}
                {i.person_name ? " · " : null}
                <span className="font-mono">{i.address ?? "—"}</span>
                {i.organization_name ? <span className="text-ink-muted"> · {i.organization_name}</span> : null}
              </p>
              <p className="text-[11px] text-ink-faint">{ASSOCIATION[i.association]}</p>
              {i.excerpt ? <p className="rounded bg-canvas-sunken px-2 py-1 text-ink-muted">{i.excerpt}</p> : null}
              {i.gmail_url ? <ExternalLink href={i.gmail_url}>Abrir en Gmail</ExternalLink> : null}
            </article>
          ))}
        </Panel>
      ) : null}
      <dl className="grid grid-cols-1 gap-2 sm:grid-cols-3" data-testid="reply-counts">
        <Figure label="Respuestas guardadas" value={d.sync.state === "not_synced" ? null : d.counts.reply} />
        <Figure label="BAJA / REMOVER con vínculo a un envío" value={d.sync.state === "not_synced" ? null : d.counts.baja} />
        <Figure label="BAJA en revisión (por dirección)" value={d.counts.baja_pending_review} />
      </dl>
      <ul className="space-y-1 text-[11px] leading-4 text-ink-muted">
        <li data-testid="baja-by-address">
          {d.baja_by_address.label} <b className="text-ink">{fmtInt(d.baja_by_address.recipients)}</b>
        </li>
        <li data-testid="baja-unassociated">
          {d.unassociated.label}: <b className="text-ink">{fmtInt(d.unassociated.baja_without_campaign_lineage)}</b>
        </li>
        <li>{d.excerpts} No se consulta Gmail.</li>
      </ul>
    </div>
  );
}

function Figure({ label, value }: { label: string; value: number | null }) {
  return (
    <div className="rounded-lg border border-line bg-canvas-raised px-3 py-2">
      <dt className="text-[11px] text-ink-faint">{label}</dt>
      <dd className="text-lg font-semibold tabular-nums text-ink">{value === null ? <span className="text-sm font-medium text-ink-faint">No sincronizadas</span> : fmtInt(value)}</dd>
    </div>
  );
}
