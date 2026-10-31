import { useCallback, useState } from "react";
import { fetchMarketing } from "../crmApi";
import type { CampaignSummary, MarketingResponse, TotalKey } from "../crmTypes";
import { AudienceBuilder } from "../marketing/AudienceBuilder";
import { AudienceFreeze } from "../marketing/AudienceFreeze";
import { useMayAuthorCampaigns } from "../marketing/authoring";
import { CampaignCalendar } from "../marketing/CampaignCalendar";
import { CampaignDetail, type DetailTab } from "../marketing/CampaignDetail";
import { CampaignEditor, type EditorSeed } from "../marketing/CampaignEditor";
import { HoldsBanner } from "../marketing/CampaignHolds";
import { BlocksOverview } from "../marketing/BlocksOverview";
import { CampaignRows } from "../marketing/CampaignRows";
import { AnalyticsAlert, AnalyticsKpis, CampaignResultsChart, ContactBase, CoverageCard } from "../marketing/MarketingAnalytics";
import { MarketingOverview } from "../marketing/MarketingOverview";
import { PeopleList } from "../marketing/PeopleList";
import { fetchMarketingAnalytics, type MarketingAnalytics, type PeopleQuery } from "../marketing/analytics";
import { SuppressionStatus } from "../marketing/SuppressionStatus";
import { PLANNING_LABEL, fmtShortDay, isV1LaneFinished, relativeDay, santiagoTime, todayInSantiago } from "../marketing/calendar";
import { EmailFrame } from "../marketing/EmailFrame";
import { V1LaneCampaignCard } from "../marketing/V1LaneCampaignCard";
import { NEVER_SENT_LABEL, TOTAL_HINT, TOTAL_LABEL, cardTotals, neverSent, isNeverSentHistorical } from "../marketing/campaignTotals";
import { fetchCampaign, fetchTaxonomy } from "../marketing/marketingApi";
import type { CampaignContent, EquipmentTaxonomy } from "../marketing/marketingTypes";
import {
  Badge,
  EmptyState,
  PageHeader,
  Panel,
  ResourceGate,
  Segmented,
  Skeleton,
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
  | { kind: "detail"; campaignId: string; tab?: DetailTab; total?: TotalKey };

export function MarketingPage() {
  // The editor and the freeze screen are write surfaces: a reader never reaches them, and a
  // campaign it opens always lands on the read-only detail (Resumen, HTML, Destinatarios, ...).
  const mayAuthor = useMayAuthorCampaigns();
  const [state, reload] = useResource(fetchMarketing);
  const [taxonomyState] = useResource(fetchTaxonomy);
  const [analyticsState] = useResource(fetchMarketingAnalytics);
  const analytics = analyticsState.kind === "ready" ? analyticsState.data : null;
  const [people, setPeople] = useState<{ query: PeopleQuery; title: string } | null>(null);
  const openPeople = (query: PeopleQuery, title: string) => setPeople({ query, title });
  const [tab, setTab] = useState<Tab>("campanas");
  const [view, setView] = useState<View>({ kind: "list" });
  const [openError, setOpenError] = useState<string | null>(null);
  const taxonomy = taxonomyState.kind === "ready" ? taxonomyState.data : null;
  const draftsEnabled = state.kind === "ready" && Boolean(state.data.authoring?.drafts_enabled);
  const freezeEnabled = state.kind === "ready" && Boolean(state.data.authoring?.freeze_enabled);
  const planningEnabled = state.kind === "ready" && Boolean(state.data.authoring?.planning_enabled);
  const openDetail = (id: string, tab?: DetailTab, total?: TotalKey) => setView({ kind: "detail", campaignId: id, tab, total });
  const detailSummary =
    view.kind === "detail" && state.kind === "ready" ? state.data.campaigns.find((c) => c.campaign_id === view.campaignId) ?? null : null;

  const openNew = () => {
    if (mayAuthor) setView({ kind: "editor", seed: { stored: null }, key: `new-${Date.now()}` });
  };
  const openStored = useCallback(async (id: string) => {
    if (!mayAuthor) {
      setView({ kind: "detail", campaignId: id });
      return;
    }
    setOpenError(null);
    try {
      const content = await fetchCampaign(id);
      setView({ kind: "editor", seed: { stored: content }, key: `${id}-${content.version}` });
    } catch (err) {
      setOpenError(err instanceof Error ? err.message : String(err));
    }
  }, [mayAuthor]);
  const openFreeze = useCallback(async (id: string) => {
    if (!mayAuthor) return;
    setOpenError(null);
    try {
      const content = await fetchCampaign(id);
      setView({ kind: "freeze", campaign: content, key: `freeze-${id}-${content.version}` });
    } catch (err) {
      setOpenError(err instanceof Error ? err.message : String(err));
    }
  }, [mayAuthor]);
  const duplicate = (content: CampaignContent) => {
    if (mayAuthor) setView({ kind: "editor", seed: { stored: null, duplicateOf: content }, key: `dup-${content.campaign_id}-${Date.now()}` });
  };

  return (
    <div className="space-y-4">
      <PageHeader
        title="Marketing"
        subtitle="Campañas enviadas, a quién llegaron y a quién nos queda escribir. Sólo cifras registradas; nada estimado. Este panel no envía campañas."
        actions={
          mayAuthor && tab !== "audiencias" && tab !== "bajas" && view.kind === "list" ? (
            <button type="button" onClick={openNew} className="h-7 rounded-md bg-ink px-3 text-xs font-medium text-on-solid hover:bg-ink-strong">
              Nueva campaña
            </button>
          ) : null
        }
      />
      {view.kind === "list" && tab === "campanas" && analytics ? (
        <div className="space-y-3" data-testid="marketing-analytics">
          <AnalyticsAlert data={analytics} />
          <AnalyticsKpis data={analytics} onOpen={openPeople} />
          <div className="grid grid-cols-1 items-start gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
            <ContactBase data={analytics} />
            {state.kind === "ready" ? (
              <CampaignResultsChart data={analytics} campaigns={state.data.campaigns} onOpen={openPeople} />
            ) : null}
          </div>
        </div>
      ) : null}
      {people && view.kind === "list" && tab === "campanas" ? (
        <PeopleList key={JSON.stringify(people.query)} title={people.title} query={people.query} onClose={() => setPeople(null)} />
      ) : null}
      {state.kind === "ready" && view.kind === "list" && tab !== "audiencias" && tab !== "bajas" ? (
        <MarketingOverview
          campaigns={state.data.campaigns}
          onOpen={(id) => openDetail(id)}
          v1LaneCampaigns={state.data.v1_lane_campaigns}
        />
      ) : null}
      {view.kind === "list" && tab !== "bajas" ? <HoldsBanner onlyGeneral onChanged={() => reload()} /> : null}
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
          { value: "bajas", label: "Bajas y rebotes" },
          { value: "audiencias", label: "Audiencias por equipo" },
        ]}
      />
      {openError ? (
        <p role="alert" className="rounded-md border border-bad/30 bg-bad-bg px-3 py-2 text-xs text-bad">
          No se pudo abrir la campaña: {openError}
        </p>
      ) : null}
      {tab === "bajas" ? (
        <div className="space-y-4">
          {analytics ? <BlocksOverview data={analytics} /> : null}
          <SuppressionStatus />
          <details className="group rounded-xl border border-line bg-canvas-raised" data-testid="safety-details">
            <summary className="cursor-pointer list-none px-4 py-3 text-[13px] font-semibold text-ink">
              <span className="mr-1.5 inline-block transition-transform group-open:rotate-90">›</span>Controles y bloqueos de seguridad
            </summary>
            <div className="space-y-3 px-4 pb-4">
              <HoldsBanner onChanged={() => reload()} />
              {state.kind === "ready" ? <ContactControls controls={state.data.contact_controls} /> : null}
            </div>
          </details>
        </div>
      ) : tab === "audiencias" ? (
        taxonomy ? <AudienceBuilder taxonomy={taxonomy} /> : <TaxonomyGate state={taxonomyState} />
      ) : view.kind === "detail" ? (
        detailSummary ? (
          <CampaignDetail
            key={`${view.campaignId}:${view.tab ?? ""}:${view.total ?? ""}`}
            summary={detailSummary}
            planningEnabled={planningEnabled}
            testSend={state.kind === "ready" ? state.data.test_send : undefined}
            taxonomy={taxonomy}
            initialTab={view.tab}
            initialTotal={view.total}
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
          {(data) => (
            <CampaignCalendar
              campaigns={data.campaigns}
              taxonomy={taxonomy}
              onOpen={(id) => openDetail(id)}
              v1LaneCampaigns={data.v1_lane_campaigns}
            />
          )}
        </ResourceGate>
      ) : view.kind === "freeze" && mayAuthor ? (
        <div className="space-y-3">
          <button
            type="button"
            onClick={() => {
              setView({ kind: "list" });
              reload();
            }}
            className="text-xs font-medium text-accent hover:underline"
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
      ) : view.kind === "editor" && mayAuthor ? (
        <div className="space-y-3">
          <button
            type="button"
            onClick={() => {
              setView({ kind: "list" });
              reload();
            }}
            className="text-xs font-medium text-accent hover:underline"
          >
            ← Volver a campañas
          </button>
          <CampaignEditor key={view.key} seed={view.seed} taxonomy={taxonomy} draftsEnabled={draftsEnabled} onSaved={() => undefined} onDuplicate={duplicate} onFreeze={(id) => void openFreeze(id)} />
        </div>
      ) : (
        <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={3} cards />}>
          {(data) => (
            <Body
              data={data}
              analytics={analytics}
              mayAuthor={mayAuthor}
              onOpen={(id) => void openStored(id)}
              onHistory={openDetail}
              onPeople={openPeople}
            />
          )}
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

function ContactControls({ controls }: { controls: MarketingResponse["contact_controls"] }) {
  return (
    <Panel title="Controles de contacto" note="se aplican antes de cualquier envío" bodyClassName="divide-y divide-line">
      {controls.map((cc) => (
        <div key={`${cc.kind}:${cc.scope}`} className="flex items-center gap-3 px-3 py-2">
          <span className="min-w-0 flex-1 text-[13px] text-ink">{CONTROL_LABEL[`${cc.kind}:${cc.scope}`] ?? `${cc.kind} · ${cc.scope}`}</span>
          <span className="text-[13px] font-semibold tabular-nums text-ink">{fmtInt(cc.count)}</span>
        </div>
      ))}
    </Panel>
  );
}

/** A campaign that went out: it is a row with its measured results, not a card. */
const wasSent = (c: CampaignSummary) => c.status !== "draft" && c.status !== "audience_frozen" && Boolean(c.first_sent_at);

function Body({
  data,
  analytics,
  mayAuthor,
  onOpen,
  onHistory,
  onPeople,
}: {
  data: MarketingResponse;
  analytics: MarketingAnalytics | null;
  mayAuthor: boolean;
  onOpen: (id: string) => void;
  onHistory: (id: string, tab?: DetailTab, total?: TotalKey) => void;
  onPeople: (query: PeopleQuery, title: string) => void;
}) {
  const [showNeverSent, setShowNeverSent] = useState(false);
  const storage = data.storage;
  const fixture = storage ? /^origenlab_test_/.test(storage.database) : false;
  const today = todayInSantiago();
  // V1-lane plans the CRM has not imported yet (the API drops a plan once its sends are in).
  const v1All = (data.v1_lane_campaigns ?? []).filter((c) => !c.imported);
  const v1Lane = v1All
    .filter((c) => !isV1LaneFinished(c, today))
    .map((c) => <V1LaneCampaignCard key={c.key} campaign={c} testSend={data.test_send} />);
  const v1Finished = v1All.filter((c) => isV1LaneFinished(c, today));
  // Historical V1 campaigns that never went out are hidden (the record stays in the CRM).
  const hidden = data.campaigns.filter(isNeverSentHistorical);
  const campaigns = showNeverSent ? data.campaigns : data.campaigns.filter((c) => !isNeverSentHistorical(c));
  const sent = campaigns.filter(wasSent);
  const unsent = campaigns.filter((c) => !wasSent(c));
  const revealNeverSent =
    hidden.length > 0 ? (
      <button type="button" onClick={() => setShowNeverSent((v) => !v)} className="text-xs font-medium text-accent hover:underline">
        {showNeverSent
          ? `Ocultar ${hidden.length === 1 ? "la campaña nunca enviada" : "las campañas nunca enviadas"}`
          : `Mostrar ${hidden.length} ${hidden.length === 1 ? "campaña nunca enviada" : "campañas nunca enviadas"}`}
      </button>
    ) : null;
  return (
    <>
      {fixture ? (
        <p role="alert" className="rounded-md border border-bad/40 bg-bad-bg px-3 py-2 text-xs font-medium text-bad" data-testid="fixture-warning">
          Base de pruebas <code>{storage?.database}</code>: estas campañas son datos inventados de desarrollo, no historia real.
        </p>
      ) : null}
      {v1Lane}
      {sent.length ? (
        <CampaignRows campaigns={sent} analytics={analytics} onDetail={(id) => onHistory(id)} onPeople={onPeople} />
      ) : null}
      {v1Finished.map((c) => <V1LaneCampaignCard key={c.key} campaign={c} testSend={data.test_send} />)}
      {unsent.length ? (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 2xl:grid-cols-3">
          {unsent.map((c) => (
            <CampaignCard
              key={c.campaign_id}
              c={c}
              mayAuthor={mayAuthor}
              onOpen={() => (mayAuthor && (c.status === "draft" || c.status === "audience_frozen") ? onOpen(c.campaign_id) : onHistory(c.campaign_id))}
              onHistory={(tab, total) => onHistory(c.campaign_id, tab, total)}
            />
          ))}
        </div>
      ) : null}
      {!sent.length && !unsent.length && !v1All.length ? (
        <EmptyState title="Sin campañas">El CRM no tiene campañas registradas.</EmptyState>
      ) : null}
      {revealNeverSent}
      {analytics ? <CoverageCard data={analytics} onOpen={onPeople} /> : null}
      <p className="text-[11px] text-ink-faint" data-testid="analytics-footnote">
        — = aún no medido. «Respondieron» cuenta a quien escribió hasta {analytics?.reply_window_days ?? 14} días después de recibir la
        campaña (sin rebotes, respuestas automáticas ni bajas). Aperturas y clics no se registran.
      </p>
    </>
  );
}

/** Label shown in the thumbnail when HTML is not available, using the per-campaign html_state. */
function thumbnailUnavailableLabel(c: CampaignSummary): string {
  if (c.html_state === "not_recovered") return "HTML no recuperado";
  if (c.html_state === "ambiguous_attribution") return "Atribución ambigua";
  return "HTML enviado no archivado";
}

/** A thumbnail of the stored HTML when there is some; otherwise it says it was never imported. */
function Thumbnail({ c }: { c: CampaignSummary }) {
  const hasHtml = c.has_html || c.html_state === "sent_html_archived" || c.html_state === "historical_draft";
  const [state] = useResource(
    () => (hasHtml ? fetchCampaign(c.campaign_id) : Promise.resolve(null)),
    [c.campaign_id, hasHtml, c.version],
  );
  if (!hasHtml) {
    return (
      <div
        className="flex h-[150px] items-center justify-center rounded-md border border-dashed border-line-strong bg-canvas-sunken text-xs text-ink-muted"
        data-testid="thumb-not-imported"
      >
        {thumbnailUnavailableLabel(c)}
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

function CampaignCard({
  c,
  mayAuthor,
  onOpen,
  onHistory,
}: {
  c: CampaignSummary;
  mayAuthor: boolean;
  onOpen: () => void;
  onHistory: (tab?: DetailTab, total?: TotalKey) => void;
}) {
  const clickable = cardTotals(c);
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
          {neverSent(c) ? (
            <span data-testid="never-sent">
              <Badge tone="neutral" title="Archivada sin ningún intento de envío registrado.">{NEVER_SENT_LABEL}</Badge>
            </span>
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
          {clickable.length ? (
            <dl className="mt-2 grid grid-cols-3 gap-1.5 text-xs" data-testid="card-totals">
              {clickable.map(({ key, value }) => (
                <div key={key} className="min-w-0">
                  <button
                    type="button"
                    title={TOTAL_HINT[key]}
                    onClick={() => onHistory(key === "responses" && value === null ? "respuestas" : "destinatarios", key)}
                    className="block w-full rounded-md border border-line/70 px-2 py-1 text-left hover:border-line-strong hover:bg-canvas-sunken/60"
                    data-testid={`card-total-${key}`}
                  >
                    <dt className="truncate text-[10px] text-ink-faint">{TOTAL_LABEL[key]}</dt>
                    <dd className="truncate font-semibold tabular-nums text-ink">
                      {value === null ? <span className="text-[11px] font-medium text-ink-faint">No sincronizadas</span> : fmtInt(value)}
                    </dd>
                  </button>
                </div>
              ))}
            </dl>
          ) : (
            <dl className="mt-2 space-y-1 text-xs">
              {states.map(([s, n]) => (
                <div key={s} className="flex items-center gap-2">
                  <span aria-hidden="true" className={`h-2 w-2 rounded-full ${RECIPIENT_STATE[s]?.color ?? "bg-ink-faint"}`} />
                  <dt className="flex-1 text-ink-muted">{RECIPIENT_STATE[s]?.label ?? s}</dt>
                  <dd className="font-semibold tabular-nums text-ink">{fmtInt(n)}</dd>
                </div>
              ))}
            </dl>
          )}
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
        <button type="button" onClick={() => onHistory()} className="shrink-0 font-medium text-accent hover:underline" data-testid="open-history">
          Historial
        </button>
        {mayAuthor && (c.status === "draft" || c.status === "audience_frozen") ? (
          <button type="button" onClick={onOpen} className="shrink-0 font-medium text-accent hover:underline">
            {c.status === "draft" ? "Editar" : "Abrir"}
          </button>
        ) : null}
      </div>
    </article>
  );
}
