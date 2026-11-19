/**
 * «Ventas › Seguimiento» (owner 2026-11-18, views and actions 2026-11-19): every open case with a
 * sent quote. Who follows it, how to reach them, how long it has waited, «Llamé» to record a call,
 * and the case itself one click away (the same drawer as Pipeline and Hoy) with «Pausar…» and «Ya no
 * sigue» in each row's «⋯». Three views (Tabla · Agenda · Por encargado), filter chips that
 * combine, and four orders, all kept in the hash query (`?vista=&encargado=&canal=…&orden=`) so a
 * link (Hoy's `?caso=<id>`) keeps working. Everything is derived from the one pipeline read.
 */
import { startTransition, useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import { fetchProfiles, type ProfileCard } from "../../api/authClient";
import { OperatorApiError } from "../../api/operatorClient";
import { useAuthSession } from "../../context/AuthSessionContext";
import { applyCaseReceipt, caseRefusalText, onCaseReceipt, setCaseFollowUp } from "../caseCommands";
import { displayName } from "../caseDisplay";
import { fetchPipeline } from "../crmApi";
import type { OpportunityCardData, PipelineResponse } from "../crmTypes";
import { CaseDrawer } from "../drawer/CaseDrawer";
import { useMediaQuery } from "../motion";
import { WHATSAPP_PROFILE_KEYS, seguimientoKpis, seguimientoRows, type SegRow } from "../seguimiento";
import {
  EMPTY_FILTERS,
  applyFilters,
  boardMoveBody,
  facetCounts,
  filterGroups,
  knownFilters,
  readSegQuery,
  sortRows,
  writeSegQuery,
  type FilterContext,
  type GroupKey,
  type SegFilters,
  type SegOrder,
  type SegView,
} from "../seguimientoView";
import { pausedUntil } from "../stage";
import { EmptyState, Modal, PageHeader, ResourceGate, Skeleton, toast } from "../ui";
import { useResource } from "../useResource";
import { CallScript } from "./CallScript";
import { CaseMoveForm } from "./CaseMove";
import type { CardHandlers } from "./SegCard";
import type { RowAction } from "./SegParts";
import { FilterBar, ViewSwitch } from "./SeguimientoFilters";
import { KpiStrip } from "./SeguimientoKpis";
import { SeguimientoSheet } from "./SeguimientoSheet";
import { SegCardList, SeguimientoAgenda, SeguimientoBoard } from "./SeguimientoViews";

const HASH_PATH = "#/ventas/seguimiento";
const VIEW_KEY = "seguimiento.vista";

function hashParams(): URLSearchParams {
  const hash = window.location.hash;
  const at = hash.indexOf("?");
  return new URLSearchParams(at < 0 ? "" : hash.slice(at + 1));
}

/** The page's own query (`?caso=`, `?vista=`…), read on every hash change. */
function useHashParams(): URLSearchParams {
  const [params, setParams] = useState(hashParams);
  useEffect(() => {
    const sync = () => setParams(hashParams());
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);
  return params;
}

/** The view this viewer last used (a per-browser convenience; the hash wins). */
function storedView(): SegView {
  try {
    const v = window.localStorage.getItem(VIEW_KEY);
    return v === "agenda" || v === "encargado" ? v : "tabla";
  } catch {
    return "tabla";
  }
}

function rememberView(view: SegView) {
  try {
    window.localStorage.setItem(VIEW_KEY, view);
  } catch {
    /* the choice just won't be remembered */
  }
}

/** Module-level, so `useResource` keeps it between visits. */
async function loadProfiles(): Promise<ProfileCard[]> {
  const r = await fetchProfiles();
  return r.kind === "ok" ? r.profiles : [];
}

const realClock = () => new Date();

export function SeguimientoPage({
  initialOpportunityId = null,
  now: fixedNow,
  clock = realClock,
}: {
  initialOpportunityId?: string | null;
  /** What the page shows «as of»; by default the time of each read. */
  now?: Date;
  /** The real time when a call is saved (tests pass a fixed one). */
  clock?: () => Date;
}) {
  const [state, reload, refreshing, patch] = useResource(fetchPipeline);
  const [profileState] = useResource<ProfileCard[]>(loadProfiles);
  // Every recorded command (encargado, canal, a close, a pause) shows at once, with the version
  // the next command needs; Hoy and Pipeline read the same cards and do the same.
  useEffect(
    () => onCaseReceipt((receipt) => patch((data) => ({ ...data, items: applyCaseReceipt(data.items, receipt) }))),
    [patch],
  );
  const profiles = profileState.kind === "ready" ? profileState.data : [];
  const patchCards = useCallback(
    (change: (items: OpportunityCardData[]) => OpportunityCardData[]) => patch((d) => ({ ...d, items: change(d.items) })),
    [patch],
  );
  return (
    <div className="space-y-4">
      <ResourceGate state={state} reload={reload} skeleton={<PageSkeleton />}>
        {(data) => (
          <Seguimiento
            data={data}
            profiles={profiles}
            fixedNow={fixedNow}
            clock={clock}
            initialId={initialOpportunityId}
            reload={reload}
            refreshing={refreshing}
            patchCards={patchCards}
          />
        )}
      </ResourceGate>
    </div>
  );
}

function PageSkeleton() {
  return (
    <>
      <PageHeader title="Seguimiento" subtitle="Cotizaciones enviadas que esperan respuesta." />
      <div className="grid grid-cols-2 gap-3 min-[900px]:grid-cols-5" aria-hidden="true">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="crm-skeleton h-24 rounded-xl" />
        ))}
      </div>
      <Skeleton rows={8} />
    </>
  );
}

function Seguimiento({
  data,
  profiles,
  fixedNow,
  clock,
  initialId,
  reload,
  refreshing,
  patchCards,
}: {
  data: PipelineResponse;
  profiles: ProfileCard[];
  fixedNow?: Date;
  clock: () => Date;
  initialId: string | null;
  reload: () => void;
  refreshing: boolean;
  patchCards: (change: (items: OpportunityCardData[]) => OpportunityCardData[]) => void;
}) {
  const { session } = useAuthSession();
  const signedIn = session.kind === "signed_in" ? session : null;
  const me = signedIn ? signedIn.profile?.id ?? signedIn.operator.operatorId : null;
  const myName = signedIn ? signedIn.profile?.displayName || signedIn.operator.displayName : "OrigenLab";
  const params = useHashParams();
  const caso = params.get("caso")?.toLowerCase() ?? initialId;
  const phone = useMediaQuery("(max-width: 639px)");

  // «Now» moves with each read, so day counts and «hoy» are as of the data on screen.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const now = useMemo(() => fixedNow ?? new Date(), [fixedNow, data]);
  // A case paused until a later day is out of the follow-up until that day (as in Hoy).
  const rows = useMemo(
    () => seguimientoRows(data.items, profiles, now).filter((r) => pausedUntil(r.card, now) === null),
    [data.items, profiles, now],
  );
  const kpis = useMemo(() => seguimientoKpis(rows, data, now), [rows, data, now]);

  const [query, setQuery] = useState(() => readSegQuery(params, storedView()));
  const [q, setQ] = useState("");
  const deferredQ = useDeferredValue(q);
  const [selectedId, setSelectedId] = useState<string | null>(caso);
  const [openId, setOpenId] = useState<string | null>(null);
  const [acting, setActing] = useState<{ id: string; action: RowAction } | null>(null);
  const [guionOpen, setGuionOpen] = useState(false);
  useEffect(() => {
    if (caso) setSelectedId(caso);
  }, [caso]);
  // A link followed while the page is open (another `?vista=` or filter) wins over what is on
  // screen; our own changes use `replaceState`, which fires no `hashchange`.
  const firstParams = useRef(params);
  useEffect(() => {
    if (params === firstParams.current) return;
    setQuery((prev) => readSegQuery(params, prev.view));
  }, [params]);

  /** Change the view, filters or order, and write them into the address (no new history entry). */
  const update = useCallback((change: (prev: typeof query) => typeof query) => {
    startTransition(() =>
      setQuery((prev) => {
        const next = change(prev);
        const query = writeSegQuery(hashParams(), next).toString();
        history.replaceState(null, "", `${HASH_PATH}${query ? `?${query}` : ""}`);
        return next;
      }),
    );
  }, []);
  const setView = useCallback(
    (view: SegView) => {
      rememberView(view);
      update((p) => ({ ...p, view }));
    },
    [update],
  );
  const setOrder = useCallback((order: SegOrder) => update((p) => ({ ...p, order })), [update]);
  const toggle = useCallback(
    (group: GroupKey, value: string) =>
      update((p) => {
        const chosen = p.filters[group];
        const next = chosen.includes(value) ? chosen.filter((v) => v !== value) : [...chosen, value];
        return { ...p, filters: { ...p.filters, [group]: next } as SegFilters };
      }),
    [update],
  );
  const clear = useCallback(() => {
    setQ("");
    update((p) => ({ ...p, filters: EMPTY_FILTERS }));
  }, [update]);

  const ctx: FilterContext = useMemo(() => ({ me, profiles, now }), [me, profiles, now]);
  const groups = useMemo(() => filterGroups(rows, ctx), [rows, ctx]);
  // An unknown value (an old link, a profile that left) is simply not applied.
  const filters = useMemo(() => knownFilters(query.filters, groups), [query.filters, groups]);
  const counts = useMemo(() => facetCounts(rows, groups, filters, ctx, deferredQ), [rows, groups, filters, ctx, deferredQ]);
  const visible = useMemo(
    () => sortRows(applyFilters(rows, filters, ctx, deferredQ), query.order),
    [rows, filters, ctx, deferredQ, query.order],
  );
  const guionRow = rows.find((r) => r.card.opportunity_id === selectedId) ?? visible[0] ?? null;
  const openCard = openId ? data.items.find((c) => c.opportunity_id === openId) ?? null : null;
  const actingCard = acting ? data.items.find((c) => c.opportunity_id === acting.id) ?? null : null;

  const onCallSaved = useCallback(
    (id: string, said: string) => {
      setSelectedId(id);
      toast(`Llamada guardada · próximo: ${said}`);
      reload();
    },
    [reload],
  );

  /** A card dropped on another person's column: shown there at once, put back if refused. */
  const onMove = useCallback(
    async (row: SegRow, ownerId: string | null) => {
      const before = row.card.follow_up ?? null;
      const body = boardMoveBody(row.card, ownerId, profiles);
      const setOwner = (fu: OpportunityCardData["follow_up"] | null) =>
        patchCards((items) => items.map((c) => (c.opportunity_id === row.card.opportunity_id ? { ...c, follow_up: fu ?? undefined } : c)));
      setOwner({ operator_id: ownerId, operator_name: null, channel: body.channel });
      try {
        await setCaseFollowUp(body);
        const name = profiles.find((p) => p.id === ownerId)?.displayName ?? "Sin confirmar";
        toast(`${displayName(row.card).name}: ahora con ${name}.`);
      } catch (err) {
        setOwner(before);
        toast(caseRefusalText(err), "bad");
        if (err instanceof OperatorApiError && err.status === 409) reload();
      }
    },
    [patchCards, profiles, reload],
  );

  const handlers: CardHandlers = useMemo(
    () => ({
      onOpen: (id) => {
        setSelectedId(id);
        setOpenId(id);
      },
      onAction: (id, action) => setActing({ id, action }),
      onCallSaved,
      onReceipt: () => undefined, // every receipt is applied by the page's `onCaseReceipt`
      onConflict: reload,
    }),
    [onCallSaved, reload],
  );

  const viewProps = { rows: visible, profiles, now, clock, callsPaused: refreshing, on: handlers };
  const noneShown = visible.length === 0;

  return (
    <>
      <PageHeader
        title="Seguimiento"
        subtitle="Cotizaciones enviadas que esperan respuesta. Llama, escribe y marca: los correos que mandamos se marcan solos."
        actions={
          <>
            <ViewSwitch view={query.view} onChange={setView} />
            <button
              type="button"
              aria-pressed={guionOpen}
              disabled={!guionRow}
              onClick={() => setGuionOpen((v) => !v)}
              className={`inline-flex h-9 items-center gap-1.5 rounded-md border px-3 text-[13px] font-semibold transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 disabled:opacity-50 ${guionOpen ? "border-brand-100 bg-brand-50 text-accent" : "border-line-strong bg-canvas-raised text-ink hover:bg-canvas-sunken"}`}
            >
              Guion de llamada
            </button>
          </>
        }
      />
      <KpiStrip kpis={kpis} profiles={profiles} rowsCount={rows.length} />
      {rows.length === 0 ? (
        <EmptyState title="No hay cotizaciones enviadas esperando respuesta." />
      ) : (
        <>
          <FilterBar
            groups={groups}
            filters={filters}
            counts={counts}
            order={query.order}
            q={q}
            shown={visible.length}
            total={rows.length}
            onToggle={toggle}
            onOrder={setOrder}
            onSearch={setQ}
            onClear={clear}
          />
          <div className="grid min-w-0 items-start gap-4 xl:grid-cols-[minmax(0,1fr)_auto]">
            <div className="min-w-0">
              {noneShown ? (
                <EmptyState title="Ningún caso con estos filtros">
                  <button type="button" onClick={clear} className="font-semibold text-accent hover:underline">
                    Limpiar
                  </button>
                </EmptyState>
              ) : query.view === "agenda" ? (
                <SeguimientoAgenda {...viewProps} />
              ) : query.view === "encargado" ? (
                <SeguimientoBoard {...viewProps} onMove={(row, ownerId) => void onMove(row, ownerId)} />
              ) : phone ? (
                <SegCardList {...viewProps} />
              ) : (
                <SeguimientoSheet
                  rows={visible}
                  profiles={profiles}
                  now={now}
                  selectedId={selectedId}
                  scrollToId={caso}
                  order={query.order}
                  onOrder={setOrder}
                  onSelect={setSelectedId}
                  onOpen={handlers.onOpen}
                  onAction={handlers.onAction}
                  onGuion={(id) => {
                    setSelectedId(id);
                    setGuionOpen(true);
                  }}
                  onCallSaved={onCallSaved}
                  onReceipt={handlers.onReceipt}
                  onConflict={reload}
                  callsPaused={refreshing}
                  clock={clock}
                />
              )}
            </div>
            {guionOpen && guionRow ? (
              <CallScript
                row={guionRow}
                me={myName}
                now={now}
                technicalName={profiles.find((p) => WHATSAPP_PROFILE_KEYS.includes(p.key.toLowerCase()))?.displayName ?? null}
                onClose={() => setGuionOpen(false)}
              />
            ) : null}
          </div>
        </>
      )}
      <CaseDrawer card={openCard} onClose={() => setOpenId(null)} onChanged={reload} refreshing={refreshing} />
      {acting && actingCard ? (
        <Modal title={acting.action === "pausa" ? "Pausar el seguimiento" : "Ya no sigue"} onClose={() => setActing(null)}>
          <p className="mb-3 truncate text-[13px] font-medium text-ink">{displayName(actingCard).name}</p>
          <CaseMoveForm
            card={actingCard}
            target={acting.action}
            showFrom={false}
            onCancel={() => setActing(null)}
            onDone={(o, refetch) => {
              const action = acting.action;
              setActing(null);
              if (refetch) reload();
              toast(o.tone === "good" ? (action === "pausa" ? "Caso en pausa: vuelve ese día." : "Caso cerrado: sale del seguimiento.") : o.lines.join(" "), o.tone);
            }}
          />
        </Modal>
      ) : null}
    </>
  );
}
