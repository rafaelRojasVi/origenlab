import { useCallback, useState } from "react";
import { fetchMarketing } from "../crmApi";
import type { CampaignSummary, MarketingResponse } from "../crmTypes";
import { AudienceBuilder } from "../marketing/AudienceBuilder";
import { AudienceFreeze } from "../marketing/AudienceFreeze";
import { CampaignCalendar } from "../marketing/CampaignCalendar";
import { CampaignDetail } from "../marketing/CampaignDetail";
import { CampaignEditor, type EditorSeed } from "../marketing/CampaignEditor";
import { HoldsBanner } from "../marketing/CampaignHolds";
import { MarketingOverview } from "../marketing/MarketingOverview";
import { SuppressionStatus } from "../marketing/SuppressionStatus";
import { PLANNING_LABEL, fmtShortDay, relativeDay, santiagoTime, todayInSantiago } from "../marketing/calendar";
import { EmailFrame } from "../marketing/EmailFrame";
import { fetchCampaign, fetchTaxonomy } from "../marketing/marketingApi";
import type { CampaignContent, EquipmentTaxonomy } from "../marketing/marketingTypes";
import {
  Badge,
  EmptyState,
  NotImportedState,
  PageHeader,
  Panel,
  ResourceGate,
  Segmented,
  Skeleton,
  StatLine,
  fmtDate,
  fmtInt,
} from "../ui";
import { useResource, type ResourceState } from "../useResource";

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

const STATUS_LABEL: Record<string, string> = {
  archived: "Archivada",
  draft: "Borrador",
  audience_frozen: "Audiencia congelada",
  approved: "Aprobada",
  sending: "Enviando",
  active: "Enviando",
  completed: "Completada",
  cancelled: "Cancelada",
};

type Tab = "campanas" | "calendario" | "audiencias" | "bajas";
type View =
  | { kind: "list" }
  | { kind: "editor"; seed: EditorSeed; key: string }
  | { kind: "freeze"; campaign: CampaignContent; key: string }
  | { kind: "detail"; campaignId: string };

export function MarketingPage() {
  const [state, reload] = useResource(fetchMarketing);
  const [taxonomyState] = useResource(fetchTaxonomy);
  const [tab, setTab] = useState<Tab>("campanas");
  const [view, setView] = useState<View>({ kind: "list" });
  const [openError, setOpenError] = useState<string | null>(null);
  const taxonomy = taxonomyState.kind === "ready" ? taxonomyState.data : null;
  const draftsEnabled = state.kind === "ready" && Boolean(state.data.authoring?.drafts_enabled);
  const freezeEnabled = state.kind === "ready" && Boolean(state.data.authoring?.freeze_enabled);
  const planningEnabled = state.kind === "ready" && Boolean(state.data.authoring?.planning_enabled);
  const openDetail = (id: string) => setView({ kind: "detail", campaignId: id });
  const detailSummary =
    view.kind === "detail" && state.kind === "ready" ? state.data.campaigns.find((c) => c.campaign_id === view.campaignId) ?? null : null;

  const openNew = () => setView({ kind: "editor", seed: { stored: null }, key: `new-${Date.now()}` });
  const openStored = useCallback(async (id: string) => {
    setOpenError(null);
    try {
      const content = await fetchCampaign(id);
      setView({ kind: "editor", seed: { stored: content }, key: `${id}-${content.version}` });
    } catch (err) {
      setOpenError(err instanceof Error ? err.message : String(err));
    }
  }, []);
  const openFreeze = useCallback(async (id: string) => {
    setOpenError(null);
    try {
      const content = await fetchCampaign(id);
      setView({ kind: "freeze", campaign: content, key: `freeze-${id}-${content.version}` });
    } catch (err) {
      setOpenError(err instanceof Error ? err.message : String(err));
    }
  }, []);
  const duplicate = (content: CampaignContent) =>
    setView({ kind: "editor", seed: { stored: null, duplicateOf: content }, key: `dup-${content.campaign_id}-${Date.now()}` });

  return (
    <div className="space-y-4">
      <PageHeader
        title="Marketing"
        subtitle="Campañas de correo en el CRM y audiencias por interés en equipos. Sólo cifras registradas; nada estimado. Nada se envía desde aquí: el envío está bloqueado mientras las respuestas BAJA no se sincronicen automáticamente desde Gmail."
        actions={
          tab !== "audiencias" && tab !== "bajas" && view.kind === "list" ? (
            <button type="button" onClick={openNew} className="h-7 rounded-md bg-ink px-3 text-xs font-medium text-white hover:bg-black">
              Nueva campaña
            </button>
          ) : null
        }
      />
      {state.kind === "ready" && view.kind === "list" && tab !== "audiencias" && tab !== "bajas" ? (
        <MarketingOverview campaigns={state.data.campaigns} onOpen={openDetail} />
      ) : null}
      {view.kind === "list" ? <HoldsBanner onChanged={() => reload()} /> : null}
      <Segmented
        label="Sección de marketing"
        value={tab}
        onChange={(t) => {
          setTab(t);
          setView({ kind: "list" });
        }}
        options={[
          { value: "campanas", label: "Campañas" },
          { value: "calendario", label: "Calendario" },
          { value: "audiencias", label: "Audiencias por equipo" },
          { value: "bajas", label: "Bajas" },
        ]}
      />
      {openError ? (
        <p role="alert" className="rounded-md border border-bad/30 bg-bad-bg px-3 py-2 text-xs text-bad">
          No se pudo abrir la campaña: {openError}
        </p>
      ) : null}
      {tab === "bajas" ? (
        <SuppressionStatus />
      ) : tab === "audiencias" ? (
        taxonomy ? <AudienceBuilder taxonomy={taxonomy} /> : <TaxonomyGate state={taxonomyState} />
      ) : view.kind === "detail" ? (
        detailSummary ? (
          <CampaignDetail
            key={view.campaignId}
            summary={detailSummary}
            planningEnabled={planningEnabled}
            onBack={() => {
              setView({ kind: "list" });
              reload();
            }}
            onEdit={(id) => void openStored(id)}
            onPlanned={() => reload()}
          />
        ) : (
          <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={3} />}>
            {() => <EmptyState title="Campaña no encontrada">La campaña ya no figura en el CRM.</EmptyState>}
          </ResourceGate>
        )
      ) : tab === "calendario" && view.kind === "list" ? (
        <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={6} />}>
          {(data) => <CampaignCalendar campaigns={data.campaigns} taxonomy={taxonomy} onOpen={openDetail} />}
        </ResourceGate>
      ) : view.kind === "freeze" ? (
        <div className="space-y-3">
          <button
            type="button"
            onClick={() => {
              setView({ kind: "list" });
              reload();
            }}
            className="text-xs font-medium text-brand-700 hover:underline"
          >
            ← Volver a campañas
          </button>
          {taxonomy ? (
            <AudienceFreeze
              key={view.key}
              campaign={view.campaign}
              taxonomy={taxonomy}
              freezeEnabled={freezeEnabled}
              onFrozen={() => reload()}
              onNewVersion={duplicate}
            />
          ) : (
            <TaxonomyGate state={taxonomyState} />
          )}
        </div>
      ) : view.kind === "editor" ? (
        <div className="space-y-3">
          <button
            type="button"
            onClick={() => {
              setView({ kind: "list" });
              reload();
            }}
            className="text-xs font-medium text-brand-700 hover:underline"
          >
            ← Volver a campañas
          </button>
          <CampaignEditor key={view.key} seed={view.seed} taxonomy={taxonomy} draftsEnabled={draftsEnabled} onSaved={() => undefined} onDuplicate={duplicate} onFreeze={(id) => void openFreeze(id)} />
        </div>
      ) : (
        <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={3} cards />}>
          {(data) => <Body data={data} onOpen={(id) => void openStored(id)} onHistory={openDetail} />}
        </ResourceGate>
      )}
    </div>
  );
}

function TaxonomyGate({ state }: { state: ResourceState<EquipmentTaxonomy> }) {
  return (
    <ResourceGate state={state} reload={() => window.location.reload()}>
      {() => null}
    </ResourceGate>
  );
}

function Body({ data, onOpen, onHistory }: { data: MarketingResponse; onOpen: (id: string) => void; onHistory: (id: string) => void }) {
  const drafts = data.authoring?.drafts_enabled;
  const storage = data.storage;
  const note = (
    <p className="text-[11px] text-ink-faint" data-testid="campaign-storage-note">
      Las campañas y borradores se guardan en <code>{storage?.table ?? "outbound.campaign"}</code>
      {storage ? (
        <>
          {" "}
          de la base <code>{storage.database}</code>
        </>
      ) : null}
      .{" "}
      {drafts
        ? "El guardado de borradores está habilitado en este entorno."
        : "El guardado de borradores no está habilitado en este entorno: un borrador nuevo existe sólo en la pestaña."}
    </p>
  );
  if (data.campaigns.length === 0) {
    return (
      <>
        {note}
        <EmptyState title="Sin campañas">El CRM no tiene campañas registradas.</EmptyState>
      </>
    );
  }
  const totalSent = data.campaigns.reduce((n, c) => n + (c.recipients_by_state.sent ?? 0), 0);
  const totalBounced = data.campaigns.reduce((n, c) => n + (c.recipients_by_state.bounced ?? 0), 0);
  const replies = data.campaigns.reduce((n, c) => n + c.replies_recorded, 0);
  return (
    <>
      <StatLine
        items={[
          { label: "Campañas", value: data.campaigns.length },
          { label: "Borradores", value: data.campaigns.filter((c) => c.status === "draft").length },
          { label: "Destinatarios enviados", value: fmtInt(totalSent) },
          { label: "Rebotes registrados", value: fmtInt(totalBounced), tone: totalBounced ? "warn" : undefined },
          { label: "Respuestas", value: replies === 0 ? "no importadas" : fmtInt(replies) },
        ]}
      />
      {note}
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 2xl:grid-cols-3">
        {data.campaigns.map((c) => (
          <CampaignCard key={c.campaign_id} c={c} onOpen={() => onOpen(c.campaign_id)} onHistory={() => onHistory(c.campaign_id)} />
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

/** A thumbnail of the stored HTML when there is some; otherwise it says it was never imported. */
function Thumbnail({ c }: { c: CampaignSummary }) {
  const [state] = useResource(
    () => (c.has_html ? fetchCampaign(c.campaign_id) : Promise.resolve(null)),
    [c.campaign_id, c.has_html, c.version],
  );
  if (!c.has_html) {
    return (
      <div
        className="flex h-[150px] items-center justify-center rounded-md border border-dashed border-line-strong bg-canvas-sunken text-xs text-ink-muted"
        data-testid="thumb-not-imported"
      >
        Contenido no importado
      </div>
    );
  }
  if (state.kind !== "ready" || !state.data?.body_html) {
    return <div className="crm-skeleton h-[150px] rounded-md" aria-label="Cargando miniatura" />;
  }
  return (
    <div className="flex h-[150px] justify-center overflow-hidden rounded-md border border-line bg-canvas-sunken" data-testid="thumb">
      <EmailFrame html={state.data.body_html} width={600} height={600} scale={0.25} title={`Miniatura de ${c.name}`} />
    </div>
  );
}

function CampaignCard({ c, onOpen, onHistory }: { c: CampaignSummary; onOpen: () => void; onHistory: () => void }) {
  const states = Object.entries(c.recipients_by_state).sort((a, b) => b[1] - a[1]);
  const total = states.reduce((n, [, v]) => n + v, 0);
  const attempts = c.send_attempts.reduce((n, a) => n + a.count, 0);
  const rejected = c.send_attempts.filter((a) => a.submission_state === "rejected").reduce((n, a) => n + a.count, 0);
  return (
    <article className="flex flex-col rounded-lg border border-line bg-canvas-raised p-3.5" data-testid="campaign-card">
      <Thumbnail c={c} />
      <div className="mt-3 flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-[13px] font-semibold text-ink" title={c.name}>
            <button type="button" onClick={onOpen} className="max-w-full truncate text-left hover:underline">
              {c.name}
            </button>
          </h3>
          <p className="truncate text-xs text-ink-muted" title={c.subject ?? undefined}>
            {c.subject ?? "Sin asunto registrado"}
          </p>
          {c.preheader ? <p className="truncate text-[11px] text-ink-faint">{c.preheader}</p> : null}
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <Badge glyph={false} tone={c.status === "draft" ? "info" : "neutral"}>
            {STATUS_LABEL[c.status] ?? c.status}
          </Badge>
          {c.origin === "imported_v1" ? (
            <Badge tone="warn" title="Cargada desde el registro de envíos de V1">
              Histórica V1
            </Badge>
          ) : null}
          {c.hold?.held ? (
            <Badge tone="bad" title={c.hold.refusals.map((r) => r.label).join(" · ")}>
              Bloqueada
            </Badge>
          ) : null}
        </div>
      </div>
      {c.planned_for_date && (c.status === "draft" || c.status === "audience_frozen") ? (
        <p className="mt-2 rounded-md border border-dashed border-warn/60 px-2 py-1 text-[11px] text-warn" title={PLANNING_LABEL} data-testid="card-planned">
          Planificada: {fmtShortDay(c.planned_for_date)}
          {c.planned_for_at ? ` · ${santiagoTime(c.planned_for_at)}` : ""} · {relativeDay(c.planned_for_date, todayInSantiago())}
        </p>
      ) : null}
      {total > 0 ? (
        <>
          <p className="mt-3 text-2xl font-semibold tabular-nums tracking-tight text-ink">{fmtInt(total)}</p>
          <p className="text-[11px] text-ink-faint">destinatarios en la audiencia</p>
          <div
            className="mt-2 flex h-2 overflow-hidden rounded-full bg-canvas-sunken"
            role="img"
            aria-label={states.map(([s, n]) => `${RECIPIENT_STATE[s]?.label ?? s}: ${n}`).join(", ")}
          >
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
        </>
      ) : (
        <p className="mt-3 text-[11px] text-ink-faint">Sin audiencia registrada.</p>
      )}
      <div className="mt-auto flex items-center gap-2 border-t border-line/70 pt-2 text-[11px] text-ink-faint [margin-top:0.75rem]">
        <span className="min-w-0 flex-1 truncate">
          {c.status === "draft"
            ? `Borrador v${c.version ?? 1} · actualizado ${fmtDate(c.updated_at ?? c.created_at)}`
            : `${fmtInt(attempts)} intentos de envío${rejected ? ` · ${rejected} rechazados` : ""} · ${fmtDate(c.first_sent_at)}${
                c.last_sent_at && c.last_sent_at.slice(0, 10) !== c.first_sent_at?.slice(0, 10) ? ` – ${fmtDate(c.last_sent_at)}` : ""
              }`}
        </span>
        <button type="button" onClick={onHistory} className="shrink-0 font-medium text-brand-700 hover:underline" data-testid="open-history">
          Historial
        </button>
        <button type="button" onClick={onOpen} className="shrink-0 font-medium text-brand-700 hover:underline">
          {c.status === "draft" ? "Editar" : "Abrir"}
        </button>
      </div>
    </article>
  );
}
