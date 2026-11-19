/**
 * «Ventas › Seguimiento» (owner 2026-11-18, views and actions 2026-11-19, QA fixes 2026-11-19):
 * every open case with a sent quote. Who follows it, how to reach them, how long it has waited,
 * «Llamé» to record a call, and the case itself one click away (the same drawer as Pipeline and
 * Hoy) with «Pausar…» and «Ya no sigue» in each row's «⋯» (each undoable for a few seconds).
 *
 * Three views (Tabla · Agenda · Por encargado), filter chips that combine, and four orders, all
 * kept in the hash query (`?vista=&encargado=&canal=…&orden=`) so a link (Hoy's `?caso=<id>`, which
 * opens that case) keeps working. Rows keep their place across saves and refetches (`keepOrder`);
 * the open «Llamé», its typed text and any open menu live in `segStore`. The Tabla selects several
 * cases to assign at once; «Modo llamadas» walks the cases due today, one «Llamé» after the other.
 * Everything is derived from the one pipeline read.
 */
import { startTransition, useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import { fetchProfiles, type ProfileCard } from "../../api/authClient";
import { OperatorApiError } from "../../api/operatorClient";
import { useMayAuthorCrm } from "../authoring/authoring";
import { useAuthSession } from "../../context/AuthSessionContext";
import { bulkResultText, runBulkFollowUp } from "../bulkAssign";
import {
  applyCaseReceipt,
  caseRefusalText,
  onCaseReceipt,
  setCaseFollowUp,
  useMayRunCaseCommands,
} from "../caseCommands";
import { toastWithUndo } from "../caseUndo";
import { displayName } from "../caseDisplay";
import { fetchPipeline } from "../crmApi";
import type { OpportunityCardData, PipelineResponse } from "../crmTypes";
import { CaseDrawer } from "../drawer/CaseDrawer";
import { useMediaQuery } from "../motion";
import { WHATSAPP_PROFILE_KEYS, seguimientoKpis, seguimientoRows, type FollowUpChannel, type SegRow } from "../seguimiento";
import {
  EMPTY_FILTERS,
  applyFilters,
  boardMoveBody,
  bulkBodies,
  dueForCall,
  facetCounts,
  filterGroups,
  keepOrder,
  knownFilters,
  readSegQuery,
  sortRows,
  writeSegQuery,
  type BulkAction,
  type FilterContext,
  type GroupKey,
  type SegFilters,
  type SegOrder,
  type SegView,
} from "../seguimientoView";
import { useSegUi } from "../segStore";
import { pausedUntil } from "../stage";
import { Button, EmptyState, Modal, PageHeader, ResourceGate, SelectInput, Skeleton, toast } from "../ui";
import { useResource } from "../useResource";
import { CallScript } from "./CallScript";
import { CaseMoveForm, type MoveOutcome } from "./CaseMove";
import { assignedText } from "./FollowUpPicker";
import type { CardHandlers } from "./SegCard";
import type { RowAction } from "./SegParts";
import { FilterBar, ViewSwitch } from "./SeguimientoFilters";
import { KpiStrip } from "./SeguimientoKpis";
import { SeguimientoSheet } from "./SeguimientoSheet";
import { SegCardList, SeguimientoAgenda, SeguimientoBoard } from "./SeguimientoViews";

const HASH_PATH = "#/ventas/seguimiento";
const VIEW_KEY = "seguimiento.vista";
const FLASH_MS = 2600;
/** The sheet needs this much screen; under it the Tabla is cards (no sideways scroll). */
const SHEET_QUERY = "(min-width: 1280px)";

function hashParams(): URLSearchParams {
  const hash = window.location.hash;
  const at = hash.indexOf("?");
  return new URLSearchParams(at < 0 ? "" : hash.slice(at + 1));
}

function replaceHash(params: URLSearchParams) {
  const query = params.toString();
  history.replaceState(null, "", `${HASH_PATH}${query ? `?${query}` : ""}`);
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
const idOf = (r: SegRow) => r.card.opportunity_id;

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

const TOOL_BUTTON =
  "inline-flex h-11 items-center gap-1.5 whitespace-nowrap rounded-md border px-3 text-[13px] font-semibold transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 disabled:opacity-50 sm:h-9";
const toolTone = (on: boolean) => (on ? "border-brand-100 bg-brand-50 text-accent" : "border-line-strong bg-canvas-raised text-ink hover:bg-canvas-sunken");

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
  const mayCommand = useMayRunCaseCommands();
  const mayCall = useMayAuthorCrm() && mayCommand;
  const params = useHashParams();
  const caso = params.get("caso")?.toLowerCase() ?? initialId;
  const phone = useMediaQuery("(max-width: 639px)");
  const sheet = useMediaQuery(SHEET_QUERY);
  const [ui, act] = useSegUi();

  // «Now» moves with each read, so day counts and «hoy» are as of the data on screen.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const now = useMemo(() => fixedNow ?? new Date(), [fixedNow, data]);
  // A case paused until a later day is out of the follow-up until that day (as in Hoy).
  const rows = useMemo(
    () => seguimientoRows(data.items, profiles, now).filter((r) => pausedUntil(r.card, now) === null),
    [data.items, profiles, now],
  );
  const rowsRef = useRef(rows);
  rowsRef.current = rows;
  const kpis = useMemo(() => seguimientoKpis(rows, data, now), [rows, data, now]);

  const [query, setQuery] = useState(() => readSegQuery(params, storedView()));
  const q = ui.q;
  const deferredQ = useDeferredValue(q);
  const [selectedId, setSelectedId] = useState<string | null>(caso);
  const [openId, setOpenId] = useState<string | null>(null);
  const [acting, setActing] = useState<{ id: string; action: RowAction } | null>(null);
  const [guionOpen, setGuionOpen] = useState(false);
  const [flashId, setFlashId] = useState<string | null>(null);
  const flashTimer = useRef<number | null>(null);
  const [selected, setSelected] = useState<Set<string> | null>(null);
  const [bulk, setBulk] = useState<{ done: number; total: number } | null>(null);
  const [callMode, setCallMode] = useState<{ ids: string[]; current: string | null } | null>(null);

  const flash = useCallback((id: string) => {
    setFlashId(id);
    if (flashTimer.current) window.clearTimeout(flashTimer.current);
    flashTimer.current = window.setTimeout(() => setFlashId(null), FLASH_MS);
  }, []);
  useEffect(() => () => void (flashTimer.current && window.clearTimeout(flashTimer.current)), []);

  // A link to one case (`?caso=`, from Hoy or shared) selects it, opens its panel and lights the row.
  const linked = useRef<string | null>(null);
  useEffect(() => {
    if (!caso || linked.current === caso || !rows.some((r) => idOf(r) === caso)) return;
    linked.current = caso;
    setSelectedId(caso);
    setOpenId(caso);
    flash(caso);
  }, [caso, rows, flash]);

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
        replaceHash(writeSegQuery(hashParams(), next));
        return next;
      }),
    );
  }, []);
  const setView = useCallback(
    (view: SegView) => {
      rememberView(view);
      if (view !== "tabla") setSelected(null);
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
    act.setQ("");
    update((p) => ({ ...p, filters: EMPTY_FILTERS }));
  }, [update, act]);

  const ctx: FilterContext = useMemo(() => ({ me, profiles, now }), [me, profiles, now]);
  const groups = useMemo(() => filterGroups(rows, ctx), [rows, ctx]);
  // An unknown value (an old link, a profile that left) is simply not applied.
  const filters = useMemo(() => knownFilters(query.filters, groups), [query.filters, groups]);
  const counts = useMemo(() => facetCounts(rows, groups, filters, ctx, deferredQ), [rows, groups, filters, ctx, deferredQ]);
  const sorted = useMemo(() => sortRows(applyFilters(rows, filters, ctx, deferredQ), query.order), [rows, filters, ctx, deferredQ, query.order]);
  // Rows keep their place until the person changes the order, a filter or the search.
  const orderKey = JSON.stringify([filters, query.order, deferredQ, query.view]);
  const lastShown = useRef<{ key: string; ids: string[] }>({ key: "", ids: [] });
  const visible = useMemo(
    () => keepOrder(sorted, lastShown.current.key === orderKey ? lastShown.current.ids : []),
    [sorted, orderKey],
  );
  useEffect(() => {
    lastShown.current = { key: orderKey, ids: visible.map(idOf) };
  }, [visible, orderKey]);
  const visibleRef = useRef(visible);
  visibleRef.current = visible;

  const guionRow = rows.find((r) => idOf(r) === selectedId) ?? visible[0] ?? null;
  const openCard = openId ? data.items.find((c) => c.opportunity_id === openId) ?? null : null;
  const actingCard = acting ? data.items.find((c) => c.opportunity_id === acting.id) ?? null : null;
  const suggested = visible.filter((r) => r.isSuggested);

  /** Open a case's panel, and put it in the address so the link can be shared. */
  const openCase = useCallback((id: string) => {
    setSelectedId(id);
    setOpenId(id);
    const p = hashParams();
    p.set("caso", id);
    linked.current = id;
    replaceHash(p);
  }, []);
  const closeCase = useCallback(() => {
    setOpenId(null);
    const p = hashParams();
    p.delete("caso");
    replaceHash(p);
  }, []);

  /** Bring a case into view with its «Llamé» open. */
  const goTo = useCallback(
    (id: string) => {
      setSelectedId(id);
      act.openCall(id);
      flash(id);
    },
    [act, flash],
  );

  /** The next case to call after `id`, in the order on screen: due today or late, not called today. */
  const nextDueAfter = useCallback(
    (id: string): string | null => {
      const list = visibleRef.current;
      const at = list.findIndex((r) => idOf(r) === id);
      return [...list.slice(at + 1), ...list.slice(0, Math.max(0, at))].find((r) => idOf(r) !== id && dueForCall(r, now))?.card.opportunity_id ?? null;
    },
    [now],
  );

  const onCallSaved = useCallback(
    (id: string, said: string) => {
      setSelectedId(id);
      reload();
      if (callMode) {
        const at = callMode.ids.indexOf(id);
        const next = callMode.ids.slice(at + 1).find((x) => rowsRef.current.some((r) => idOf(r) === x)) ?? null;
        toast(`Llamada guardada · próximo: ${said}`);
        if (next) {
          setCallMode({ ...callMode, current: next });
          goTo(next);
        } else {
          setCallMode(null);
          toast("Listo: no quedan casos por llamar hoy.");
        }
        return;
      }
      const next = nextDueAfter(id);
      toast(`Llamada guardada · próximo: ${said}`, "good", next ? { action: { label: "Siguiente →", onClick: () => goTo(next) } } : {});
    },
    [reload, callMode, goTo, nextDueAfter],
  );

  function startCallMode() {
    const ids = visible.filter((r) => dueForCall(r, now)).map(idOf);
    if (ids.length === 0) {
      toast("No hay casos por llamar ahora: los visibles tienen fecha más adelante o ya se llamaron hoy.", "warn");
      return;
    }
    setSelected(null);
    setCallMode({ ids, current: ids[0] });
    goTo(ids[0]);
  }
  function stopCallMode() {
    setCallMode(null);
    act.openCall(null);
  }
  function skipCall() {
    if (!callMode?.current) return;
    const at = callMode.ids.indexOf(callMode.current);
    const next = callMode.ids.slice(at + 1).find((x) => rowsRef.current.some((r) => idOf(r) === x));
    if (next) {
      setCallMode({ ...callMode, current: next });
      goTo(next);
    } else stopCallMode();
  }

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
        toast(assignedText(ownerId, profiles));
      } catch (err) {
        setOwner(before);
        toast(caseRefusalText(err), "bad");
        if (err instanceof OperatorApiError && err.status === 409) reload();
      }
    },
    [patchCards, profiles, reload],
  );

  /** One action on several cases, one command each, with progress and one toast at the end. */
  async function runBulk(action: BulkAction, ids: string[]) {
    if (bulk) return;
    const chosen = rowsRef.current.filter((r) => ids.includes(idOf(r)));
    const { bodies, skipped } = bulkBodies(chosen, action, profiles);
    if (bodies.length === 0) {
      toast(action.kind === "channel" ? "Ese canal no aplica a los casos elegidos." : "Nada que cambiar en los casos elegidos.", "warn");
      return;
    }
    setBulk({ done: 0, total: bodies.length });
    const result = await runBulkFollowUp(bodies, (done, total) => setBulk({ done, total }));
    setBulk(null);
    setSelected(null);
    toast(bulkResultText(result, skipped), result.failed ? "warn" : "good");
    reload();
  }

  function actionDone(o: MoveOutcome, refetch: boolean, action: RowAction, card: OpportunityCardData) {
    setActing(null);
    if (refetch) reload();
    if (o.tone === "bad") {
      toast(o.lines.join(" "), o.tone);
      return;
    }
    // A close recorded in part still closed the case: its «Deshacer» stays on offer.
    const text = o.tone === "warn" ? o.lines.join(" ") : action === "pausa" ? "Caso en pausa: vuelve ese día." : "Caso cerrado: sale del seguimiento.";
    toastWithUndo(text, action, card, o.receipts, reload, o.tone);
  }

  const handlers: CardHandlers = useMemo(
    () => ({
      onOpen: openCase,
      onAction: (id, action) => setActing({ id, action }),
      onCallSaved,
      onReceipt: () => undefined, // every receipt is applied by the page's `onCaseReceipt`
      onConflict: reload,
      onGuion: (id) => {
        setSelectedId(id);
        setGuionOpen(true);
      },
    }),
    [onCallSaved, reload, openCase],
  );

  const selection = selected
    ? {
        selected,
        onToggle: (id: string) =>
          setSelected((s) => {
            const next = new Set(s ?? []);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
          }),
        onToggleAll: () => setSelected((s) => (s && visible.every((r) => s.has(idOf(r))) ? new Set() : new Set(visible.map(idOf)))),
      }
    : null;
  const viewProps = { rows: visible, profiles, now, clock, callsPaused: refreshing, on: handlers, flashId };
  const noneShown = visible.length === 0;
  const meKey = profiles.find((p) => p.id === me)?.key.toLowerCase() ?? "";
  const callerIsTechnical = WHATSAPP_PROFILE_KEYS.includes(meKey);
  const technicalName = profiles.find((p) => WHATSAPP_PROFILE_KEYS.includes(p.key.toLowerCase()))?.displayName ?? null;
  const modeAt = callMode?.current ? callMode.ids.indexOf(callMode.current) + 1 : 0;

  const script =
    guionOpen && guionRow ? (
      <CallScript row={guionRow} me={myName} now={now} technicalName={technicalName} callerIsTechnical={callerIsTechnical} onClose={() => setGuionOpen(false)} />
    ) : null;

  return (
    <>
      <PageHeader
        title="Seguimiento"
        subtitle="Cotizaciones enviadas que esperan respuesta. Llama, escribe y marca: los correos que mandamos se marcan solos."
        actions={
          <>
            <ViewSwitch view={query.view} onChange={setView} />
            <button type="button" aria-pressed={guionOpen} disabled={!guionRow} onClick={() => setGuionOpen((v) => !v)} className={`${TOOL_BUTTON} ${toolTone(guionOpen)}`}>
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
            onSearch={act.setQ}
            onClear={clear}
          />
          <div className="flex flex-wrap items-center gap-2" data-testid="seg-tools">
            {mayCall ? (
              <button
                type="button"
                aria-pressed={!!callMode}
                onClick={() => (callMode ? stopCallMode() : startCallMode())}
                className={`${TOOL_BUTTON} ${toolTone(!!callMode)}`}
              >
                Modo llamadas
              </button>
            ) : null}
            {mayCommand && query.view === "tabla" ? (
              <button
                type="button"
                aria-pressed={!!selected}
                onClick={() => {
                  setSelected((s) => (s ? null : new Set()));
                  setCallMode(null);
                }}
                className={`${TOOL_BUTTON} ${toolTone(!!selected)}`}
              >
                {selected ? "Terminar selección" : "Seleccionar"}
              </button>
            ) : null}
            {mayCommand && suggested.length > 0 ? (
              <Button
                variant="quiet"
                className="h-11 text-[13px] font-semibold sm:h-9"
                disabled={!!bulk}
                onClick={() => void runBulk({ kind: "confirm" }, suggested.map(idOf))}
              >
                Confirmar todos los sugeridos ({suggested.length})
              </Button>
            ) : null}
            {bulk ? (
              <span role="status" className="text-[12.5px] font-semibold text-ink-muted" data-testid="seg-bulk-progress">
                Asignando {bulk.done} de {bulk.total}…
              </span>
            ) : null}
          </div>
          {callMode ? (
            <div role="status" className="flex flex-wrap items-center gap-3 rounded-xl border border-brand-100 bg-brand-50 px-4 py-2.5 text-[13px] text-ink" data-testid="seg-call-mode">
              <b className="font-bold text-accent">Modo llamadas</b>
              <span className="tabular-nums" data-testid="seg-call-mode-at">
                {modeAt} de {callMode.ids.length}
              </span>
              <span className="text-ink-muted">Guarda la llamada y pasa al siguiente.</span>
              <span className="ml-auto flex gap-2">
                <Button onClick={skipCall} className="max-sm:h-11">Saltar →</Button>
                <Button onClick={stopCallMode} className="max-sm:h-11">Salir</Button>
              </span>
            </div>
          ) : null}
          <div className="grid min-w-0 items-start gap-4 2xl:grid-cols-[minmax(0,1fr)_auto]">
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
              ) : !sheet ? (
                <SegCardList {...viewProps} selection={selection} />
              ) : (
                <SeguimientoSheet
                  rows={visible}
                  profiles={profiles}
                  now={now}
                  selectedId={selectedId}
                  flashId={flashId}
                  order={query.order}
                  onOrder={setOrder}
                  onSelect={setSelectedId}
                  onOpen={handlers.onOpen}
                  onAction={handlers.onAction}
                  onGuion={handlers.onGuion!}
                  onCallSaved={onCallSaved}
                  onReceipt={handlers.onReceipt}
                  onConflict={reload}
                  callsPaused={refreshing}
                  clock={clock}
                  selection={selection}
                />
              )}
            </div>
            {/* Beside the sheet on a very wide screen; above it otherwise, so it is where the eyes are. */}
            {script && !phone ? <div className="order-first min-w-0 2xl:order-none">{script}</div> : null}
          </div>
          {selected ? (
            <div
              role="region"
              aria-label="Acciones sobre los seleccionados"
              className="sticky bottom-4 z-20 flex flex-wrap items-center gap-2 rounded-xl border border-line-strong bg-canvas-raised px-4 py-3 shadow-lg"
              data-testid="seg-bulk-bar"
            >
              <b className="text-[13px] tabular-nums text-ink">
                {selected.size} {selected.size === 1 ? "seleccionado" : "seleccionados"}
              </b>
              <Button variant="primary" className="max-sm:h-11" disabled={selected.size === 0 || !!bulk} onClick={() => void runBulk({ kind: "confirm" }, [...selected])}>
                Confirmar sugerido
              </Button>
              <span className="w-40">
                <SelectInput
                  ariaLabel="Asignar a…"
                  value=""
                  placeholder="Asignar a…"
                  disabled={selected.size === 0 || !!bulk}
                  className="max-sm:min-h-11"
                  options={[...profiles.map((p) => ({ value: p.id, label: p.displayName })), { value: "sin", label: "Sin confirmar" }]}
                  onChange={(v) => v && void runBulk({ kind: "owner", ownerId: v === "sin" ? null : v }, [...selected])}
                />
              </span>
              <span className="w-36">
                <SelectInput
                  ariaLabel="Canal…"
                  value=""
                  placeholder="Canal…"
                  disabled={selected.size === 0 || !!bulk}
                  className="max-sm:min-h-11"
                  options={[
                    { value: "llamada", label: "Llamada" },
                    { value: "correo", label: "Correo" },
                    { value: "whatsapp", label: "WhatsApp" },
                  ]}
                  onChange={(v) => v && void runBulk({ kind: "channel", channel: v as FollowUpChannel }, [...selected])}
                />
              </span>
              <Button className="ml-auto max-sm:h-11" onClick={() => setSelected(null)} disabled={!!bulk}>
                Cancelar
              </Button>
            </div>
          ) : null}
        </>
      )}
      {script && phone ? (
        <Modal title="Guion de llamada" onClose={() => setGuionOpen(false)}>
          {script}
        </Modal>
      ) : null}
      <CaseDrawer card={openCard} onClose={closeCase} onChanged={reload} refreshing={refreshing} />
      {acting && actingCard ? (
        <Modal title={acting.action === "pausa" ? "Pausar el seguimiento" : "Ya no sigue"} onClose={() => setActing(null)}>
          <p className="mb-3 truncate text-[13px] font-medium text-ink">{displayName(actingCard).name}</p>
          <CaseMoveForm
            card={actingCard}
            target={acting.action}
            showFrom={false}
            lostWording={{
              submit: "Ya no sigue",
              reason: "¿Por qué ya no sigue?",
              hint: "Elige una. «Sin respuesta» y «No es una solicitud» lo cierran como abandonado; las demás, como perdido. Se conserva todo y puedes deshacerlo.",
            }}
            onCancel={() => setActing(null)}
            onDone={(o, refetch) => actionDone(o, refetch, acting.action, actingCard)}
          />
        </Modal>
      ) : null}
    </>
  );
}
