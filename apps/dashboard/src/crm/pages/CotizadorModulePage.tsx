import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type RefObject } from "react";
import { useAuthSession } from "../../context/AuthSessionContext";
import { useMayRunCaseCommands } from "../caseCommands";
import { crmHash, parseCrmHash, type CrmSection } from "../crmRoute";
import { NewQuoteDialog } from "../quotes/NewQuoteDialog";
import { REVISION_STATUS } from "../quotes/IssuedView";
import { useMayOpenCotizador } from "../quotes/openOrCreateQuote";
import { QuoteListPane } from "../quotes/QuoteListPane";
import { MAX_TABS, useQuoteTabs, type QuoteTab, type TabMeta, type TabStatus, type TabsAsk } from "../quotes/tabStore";
import { Button, Modal, Skeleton } from "../ui";
import { CotizadorPage, NoQuoteAccess } from "./CotizadorPage";

/**
 * «Cotizador»: many quotes open at once, as tabs (R4), at `#/crm/cotizador[/<quoteId>]`.
 *
 * «Todas» comes first (the list of quotes), then up to eight open quotes — each tab says
 * the quote's CN number, its client and its state (a dot: amber draft, green approved, blue sent,
 * grey voided) — then «+ Nueva» (`NewQuoteDialog`: a client, and the API finds or opens its case;
 * the quote made opens as a tab). The address names the tab shown:
 * a click on a tab, an entry point elsewhere («Cotizar» in Hoy, a request, a case) or the old
 * `#/crm/cotizar/<id>` all open the quote here, and a quote already open is only shown again.
 *
 * Each tab's pane is the one-quote page (`CotizadorPage`). A pane stays mounted, hidden, once it
 * has been shown, so its autosave timers and unsaved edits survive a switch; a tab restored from
 * this computer mounts (and reads its quote) only the first time it is shown, so a reload reads one
 * quote, not eight. Closing a tab sends its pending save first and asks when that fails; a ninth
 * tab closes the oldest one with nothing pending, and asks when every tab has something pending.
 *
 * Sales and administration only: anyone else (the menu hides it) is told so, and nothing is read.
 */
type Navigate = (section: CrmSection, id?: string | null) => void;

export function CotizadorModulePage({ quoteId, navigate }: { quoteId: string | null; navigate: Navigate }) {
  const { session } = useAuthSession();
  const mayQuote = useMayOpenCotizador();
  if (session.kind !== "signed_in") return <Skeleton rows={3} />;
  if (!mayQuote) return <NoQuoteAccess />;
  // Each operator has their own tabs on this computer.
  const operatorId = session.operator.operatorId;
  return <CotizadorModule key={operatorId} operatorId={operatorId} routeId={quoteId} navigate={navigate} />;
}

const DOT: Record<TabStatus, string> = {
  draft: "bg-amber-600",
  approved: "bg-good",
  sent: "bg-info",
  void: "bg-ink-faint",
};

const NO_COMMANDS = "Crear cotizaciones está desactivado en este servidor.";

const tabDomId = (id: string | null) => `cotizador-tab-${id ?? "todas"}`;
const panelDomId = (id: string | null) => `cotizador-panel-${id ?? "todas"}`;

type Asking = { kind: "discard" | "evict"; tab: QuoteTab; resolve: (yes: boolean) => void };

function CotizadorModule({ operatorId, routeId, navigate }: { operatorId: string; routeId: string | null; navigate: Navigate }) {
  // Work that finishes after the module is gone (a flush, a question) must not move the address.
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  /** The address of the tab shown, replaced (not pushed) so a closed tab is not one «back» away twice;
   *  only while the module is on screen and the address is still the module's. */
  const showInAddress = useCallback((active: string | null) => {
    if (!alive.current || parseCrmHash(window.location.hash).section !== "cotizador") return;
    const target = crmHash("cotizador", active);
    if (window.location.hash !== target) window.location.replace(target);
  }, []);

  // Questions wait in line: a second one (another tab closing) never drops the first.
  const queue = useRef<Asking[]>([]);
  const [asking, setAsking] = useState<Asking | null>(null);
  const ask = useMemo<TabsAsk>(() => {
    const enqueue = (kind: Asking["kind"]) => (tab: QuoteTab) =>
      new Promise<boolean>((resolve) => {
        queue.current.push({ kind, tab, resolve });
        if (queue.current.length === 1) setAsking(queue.current[0]);
      });
    return { discard: enqueue("discard"), evict: enqueue("evict") };
  }, []);
  const tabs = useQuoteTabs(operatorId, ask, routeId);
  const { state, open, close, activate, setMeta, peek } = tabs;

  // Questions left open when the module goes away are a «no».
  useEffect(
    () => () => {
      const left = queue.current;
      queue.current = [];
      left.forEach((q) => q.resolve(false));
    },
    [],
  );
  const answer = (yes: boolean) => {
    const current = queue.current.shift();
    setAsking(queue.current[0] ?? null);
    current?.resolve(yes);
  };

  // The address drives the tab shown. Without a quote in it, the first time, the tab shown last
  // time is put back in the address; after that it means «Todas». (Keyed on the address seen
  // last, so running twice for one address — React's development double run — does nothing more.)
  const lastRoute = useRef<string | null | undefined>(undefined);
  useEffect(() => {
    if (lastRoute.current === routeId) return;
    const first = lastRoute.current === undefined;
    lastRoute.current = routeId;
    if (routeId) {
      // The address may move on while the operator is asked to make room: then this quote is not wanted.
      const stillWanted = () => alive.current && lastRoute.current === routeId;
      void open(routeId, null, { stillWanted }).then((opened) => {
        if (!opened && stillWanted()) showInAddress(peek().active);
      });
    } else if (first) {
      if (peek().active) showInAddress(peek().active);
    } else {
      activate(null);
    }
  }, [routeId, open, activate, peek, showInAddress]);

  // Lazy panes: a pane mounts the first time its tab is shown, then stays until its tab closes.
  const [mounted, setMounted] = useState<ReadonlySet<string>>(() => new Set(state.active ? [state.active] : []));
  {
    const openIds = new Set(state.tabs.map((t) => t.id));
    let next: Set<string> | null = [...mounted].some((id) => !openIds.has(id)) ? new Set([...mounted].filter((id) => openIds.has(id))) : null;
    if (state.active && !(next ?? mounted).has(state.active)) next = new Set(next ?? mounted).add(state.active);
    if (next) setMounted(next);
  }

  const closeOne = useCallback(
    async (id: string) => {
      const wasShown = peek().active === id;
      if (!(await close(id)) || !alive.current) return;
      // The focus goes to the tab now selected («Todas» when none is left), not to the page's start.
      document.getElementById(tabDomId(peek().active))?.focus();
      if (wasShown) showInAddress(peek().active);
    },
    [close, peek, showInAddress],
  );

  const show = (id: string | null) => navigate("cotizador", id);
  // «+ Nueva» and the list's «Nueva cotización»: only with the commands on (sales and admin).
  const mayCreate = useMayRunCaseCommands();
  const [creating, setCreating] = useState(false);
  const startNew = mayCreate ? () => setCreating(true) : undefined;
  const scrollerRef = useRef<HTMLDivElement>(null);

  // A row of «Todas»: open (or show) its tab, with the label, client and state the row knows.
  const openFromList = useCallback(
    async (id: string, meta: TabMeta) => {
      if (await open(id, meta, { stillWanted: () => alive.current })) showInAddress(peek().active);
    },
    [open, peek, showInAddress],
  );

  // Arrows move between tabs (Home / End to the ends); Enter or Space shows one; Supr closes it.
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const list = Array.from(e.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]'));
    const at = list.indexOf(document.activeElement as HTMLButtonElement);
    if (at < 0) return;
    let to: number | null = null;
    if (e.key === "ArrowRight") to = (at + 1) % list.length;
    else if (e.key === "ArrowLeft") to = (at - 1 + list.length) % list.length;
    else if (e.key === "Home") to = 0;
    else if (e.key === "End") to = list.length - 1;
    else if (e.key === "Delete") {
      const id = list[at].dataset.quoteId;
      if (id) {
        e.preventDefault();
        void closeOne(id);
      }
      return;
    }
    if (to === null) return;
    e.preventDefault();
    list[to].focus();
  };

  return (
    <div className="space-y-[18px]">
      <div className="flex items-end gap-1 border-b border-line">
        {/* The tablist owns only tabs; each tab's × sits over its chip, in a layer of its own (`CloseButtons`). */}
        <div ref={scrollerRef} className="relative min-w-0 overflow-x-auto">
        <div role="tablist" aria-label="Cotizaciones abiertas" onKeyDown={onKeyDown} className="flex w-max items-end gap-1 pt-1">
          <button
            type="button"
            role="tab"
            id={tabDomId(null)}
            aria-selected={state.active === null}
            aria-controls={panelDomId(null)}
            tabIndex={state.active === null ? 0 : -1}
            onClick={() => show(null)}
            className={`${tabClass(state.active === null)} px-4 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-600`}
          >
            Todas
          </button>
          {state.tabs.map((t) => (
            <QuoteTabButton key={t.id} tab={t} selected={state.active === t.id} hasPanel={mounted.has(t.id)} onShow={() => show(t.id)} />
          ))}
        </div>
        <CloseButtons tabs={state.tabs} scrollerRef={scrollerRef} onClose={(id) => void closeOne(id)} />
        </div>
        <span className="inline-flex shrink-0">
          <Button
            variant="quiet"
            onClick={startNew}
            disabled={!startNew}
            title={startNew ? undefined : NO_COMMANDS}
            aria-describedby={startNew ? undefined : "cotizador-nueva-off"}
            className="mb-0.5 min-h-[44px] px-3 text-[13.5px] font-semibold"
          >
            + Nueva
          </Button>
          {startNew ? null : (
            <span id="cotizador-nueva-off" className="sr-only">
              {NO_COMMANDS}
            </span>
          )}
        </span>
      </div>

      <section role="tabpanel" id={panelDomId(null)} aria-labelledby={tabDomId(null)} hidden={state.active !== null}>
        <QuoteListPane active={state.active === null} onOpen={(id, meta) => void openFromList(id, meta)} onNew={startNew} />
      </section>
      {state.tabs.map((t) =>
        mounted.has(t.id) ? (
          <section key={t.id} role="tabpanel" id={panelDomId(t.id)} aria-labelledby={tabDomId(t.id)} hidden={state.active !== t.id}>
            <CotizadorPage quoteId={t.id} navigate={navigate} onMeta={(m) => setMeta(t.id, m)} />
          </section>
        ) : null,
      )}

      {creating ? (
        <NewQuoteDialog
          onClose={() => setCreating(false)}
          onCreated={(id, meta) => {
            setCreating(false);
            void openFromList(id, meta);
          }}
        />
      ) : null}
      {asking ? <AskDialog asking={asking} onAnswer={answer} /> : null}
    </div>
  );
}

/** The look of a tab: the one shown joins the page under it. */
function tabClass(selected: boolean): string {
  return `inline-flex h-11 shrink-0 items-center whitespace-nowrap rounded-t-[10px] border border-line text-[13.5px] text-ink ${
    selected ? "-mb-px border-b-canvas bg-canvas font-semibold" : "border-b-0 bg-canvas-sunken hover:bg-canvas-raised"
  }`;
}

function QuoteTabButton({
  tab,
  selected,
  hasPanel,
  onShow,
}: {
  tab: QuoteTab;
  selected: boolean;
  hasPanel: boolean;
  onShow: () => void;
}) {
  const label = tab.meta?.label ?? "Cotización";
  const status = tab.meta?.status ?? null;
  return (
    <button
      type="button"
      role="tab"
      id={tabDomId(tab.id)}
      data-quote-id={tab.id}
      aria-selected={selected}
      aria-controls={hasPanel ? panelDomId(tab.id) : undefined}
      aria-keyshortcuts="Delete"
      tabIndex={selected ? 0 : -1}
      onClick={onShow}
      // The right padding leaves room for the × laid over the chip.
      className={`${tabClass(selected)} gap-2 pl-3.5 pr-10 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-600`}
    >
      <span className={`font-mono tabular-nums ${tab.meta ? "font-medium" : "text-ink-muted"}`}>{label}</span>
      {tab.meta?.client ? <span className="max-w-[140px] truncate font-normal text-ink-muted">{tab.meta.client}</span> : null}
      {status ? (
        <>
          <span aria-hidden="true" className={`h-2 w-2 shrink-0 rounded-full ${DOT[status]}`} data-testid="cotizador-tab-dot" data-status={status} />
          <span className="sr-only">{REVISION_STATUS[status]?.label ?? ""}</span>
        </>
      ) : null}
    </button>
  );
}

type Spot = { left: number; top: number };
const CLOSE_SIZE = 32;

/**
 * Each tab's ×, outside the tablist (it owns only tabs) and laid over the right end of its chip:
 * placed from the tab's own box, again whenever the tabs change size. They scroll with the tabs
 * (same scroll box). Not tab stops of their own: on the keyboard, Supr closes the focused tab.
 */
function CloseButtons({
  tabs,
  scrollerRef,
  onClose,
}: {
  tabs: QuoteTab[];
  scrollerRef: RefObject<HTMLDivElement | null>;
  onClose: (id: string) => void;
}) {
  const [spots, setSpots] = useState<Record<string, Spot>>({});
  useLayoutEffect(() => {
    const place = () => {
      const next: Record<string, Spot> = {};
      for (const t of tabs) {
        const el = document.getElementById(tabDomId(t.id));
        if (!el) continue;
        next[t.id] = { left: el.offsetLeft + el.offsetWidth - CLOSE_SIZE - 4, top: el.offsetTop + (el.offsetHeight - CLOSE_SIZE) / 2 };
      }
      setSpots((prev) => (JSON.stringify(prev) === JSON.stringify(next) ? prev : next));
    };
    place();
    const box = scrollerRef.current;
    if (!box || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(place);
    ro.observe(box);
    box.querySelectorAll('[role="tab"]').forEach((el) => ro.observe(el));
    return () => ro.disconnect();
  }, [tabs, scrollerRef]);
  return (
    <>
      {tabs.map((t) => {
        const spot = spots[t.id];
        if (!spot) return null;
        return (
          <button
            key={t.id}
            type="button"
            tabIndex={-1}
            aria-label={`Cerrar ${t.meta?.label ?? "Cotización"}`}
            title="Cerrar"
            onClick={() => onClose(t.id)}
            style={{ left: spot.left, top: spot.top }}
            className="absolute inline-flex h-8 w-8 items-center justify-center rounded-md text-ink-muted hover:bg-canvas-sunken hover:text-ink"
          >
            <span aria-hidden="true">×</span>
          </button>
        );
      })}
    </>
  );
}

function tabName(tab: QuoteTab): string {
  const label = tab.meta?.label ?? "esta cotización";
  return tab.meta?.client ? `${label} · ${tab.meta.client}` : label;
}

function AskDialog({ asking, onAnswer }: { asking: Asking; onAnswer: (yes: boolean) => void }) {
  const discard = asking.kind === "discard";
  return (
    <Modal title={discard ? "No se pudo guardar. ¿Cerrar igual?" : `Ya tienes ${MAX_TABS} cotizaciones abiertas`} onClose={() => onAnswer(false)}>
      <p className="text-[13px] text-ink">
        {discard
          ? `Si cierras ${tabName(asking.tab)}, se pierden los cambios que no se guardaron.`
          : `Todas tienen cambios sin guardar. Para abrir otra se cierra la más antigua, ${tabName(asking.tab)}, después de guardarla.`}
      </p>
      <div className="mt-4 flex flex-wrap justify-end gap-2">
        <Button className="min-h-[44px] px-4 text-[14px]" onClick={() => onAnswer(false)}>
          {discard ? "Seguir editando" : "Cancelar"}
        </Button>
        <Button variant={discard ? "danger" : "primary"} className="min-h-[44px] px-4 text-[14px]" onClick={() => onAnswer(true)}>
          {discard ? "Cerrar igual" : `Cerrar ${asking.tab.meta?.label ?? "la más antigua"}`}
        </Button>
      </div>
    </Modal>
  );
}
