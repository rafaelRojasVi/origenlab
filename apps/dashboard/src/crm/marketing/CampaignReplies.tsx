import { Badge, ExternalLink, Panel, ResourceGate, Skeleton, fmtDate, fmtInt, type Tone } from "../ui";
import { useResource } from "../useResource";
import { fetchCampaignReplies } from "./marketingApi";
import { PeopleList } from "./PeopleList";
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
 * Respuestas: who wrote back within the reply window, measured from the captured mail (the same
 * figure as the campaign list), then what the CRM stores with lineage and the BAJA bookkeeping.
 * Nothing is fetched from Gmail here.
 */
export function CampaignReplies({ campaignId }: { campaignId: string }) {
  const [state, reload] = useResource(() => fetchCampaignReplies(campaignId), [campaignId]);
  return (
    <div className="space-y-3">
      <PeopleList title="Respondieron (hasta 14 días después)" query={{ kind: "campaign", metric: "responded", campaignId }} onClose={() => undefined} closable={false} />
      <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={4} />}>
        {(d) => <Body d={d} />}
      </ResourceGate>
    </div>
  );
}

function Body({ d }: { d: RepliesResponse }) {
  return (
    <div className="space-y-3" data-testid="campaign-replies">
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
      <dl className="grid grid-cols-1 gap-2 sm:grid-cols-2" data-testid="reply-counts">
        <Figure label="BAJA / REMOVER con vínculo a un envío" value={d.counts.baja} />
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
      <dd className="text-lg font-semibold tabular-nums text-ink">{value === null ? <span className="text-ink-faint">—</span> : fmtInt(value)}</dd>
    </div>
  );
}
