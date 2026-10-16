import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useAuthSession } from "../../context/AuthSessionContext";
import { crmHash, type CrmSection } from "../crmRoute";
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
 * grey voided) — then «+ Nueva» (the new-quote dialog; Task 5). The address names the tab shown:
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

const tabDomId = (id: string | null) => `cotizador-tab-${id ?? "todas"}`;
const panelDomId = (id: string | null) => `cotizador-panel-${id ?? "todas"}`;

/** The address of the tab shown, replaced (not pushed) so a closed tab is not one «back» away twice. */
function showInAddress(active: string | null): void {
  const target = crmHash("cotizador", active);
  if (window.location.hash !== target) window.location.replace(target);
}

type Asking = { kind: "discard" | "evict"; tab: QuoteTab; resolve: (yes: boolean) => void };

function CotizadorModule({ operatorId, routeId, navigate }: { operatorId: string; routeId: string | null; navigate: Navigate }) {
  const [asking, setAsking] = useState<Asking | null>(null);
  const ask = useMemo<TabsAsk>(
    () => ({
      discard: (tab) => new Promise<boolean>((resolve) => setAsking({ kind: "discard", tab, resolve })),
      evict: (tab) => new Promise<boolean>((resolve) => setAsking({ kind: "evict", tab, resolve })),
    }),
    [],
  );
  const tabs = useQuoteTabs(operatorId, ask, routeId);
  const { state, open, close, activate, setMeta, peek } = tabs;

  // A question left open when the module goes away is a «no».
  const askingRef = useRef(asking);
  askingRef.current = asking;
  useEffect(() => () => askingRef.current?.resolve(false), []);
  const answer = (yes: boolean) => {
    asking?.resolve(yes);
    setAsking(null);
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
      void open(routeId).then((opened) => {
        if (!opened) showInAddress(peek().active);
      });
    } else if (first) {
      if (peek().active) showInAddress(peek().active);
    } else {
      activate(null);
    }
  }, [routeId, open, activate, peek]);

  // Lazy panes: a pane mounts the first time its tab is shown, then stays.
  const [mounted, setMounted] = useState<ReadonlySet<string>>(() => new Set(state.active ? [state.active] : []));
  if (state.active && !mounted.has(state.active)) setMounted(new Set(mounted).add(state.active));

  const closeOne = useCallback(
    async (id: string) => {
      const wasShown = peek().active === id;
      if ((await close(id)) && wasShown) showInAddress(peek().active);
    },
    [close, peek],
  );

  const show = (id: string | null) => navigate("cotizador", id);

  // A row of «Todas»: open (or show) its tab, with the label, client and state the row knows.
  const openFromList = useCallback(
    async (id: string, meta: TabMeta) => {
      if (await open(id, meta)) showInAddress(peek().active);
    },
    [open, peek],
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
        <div role="tablist" aria-label="Cotizaciones abiertas" onKeyDown={onKeyDown} className="flex min-w-0 items-end gap-1 overflow-x-auto pt-1">
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
            <TabChip key={t.id} tab={t} selected={state.active === t.id} hasPanel={mounted.has(t.id)} onShow={() => show(t.id)} onClose={() => void closeOne(t.id)} />
          ))}
        </div>
        {/* Task 5 builds the new-quote dialog: until then the button is there, off, and says so. */}
        <span title="Llega en la siguiente entrega" className="inline-flex shrink-0">
          <Button variant="quiet" disabled className="mb-0.5 min-h-[44px] px-3 text-[13.5px] font-semibold" aria-describedby="cotizador-nueva-later">
            + Nueva
          </Button>
          <span id="cotizador-nueva-later" className="sr-only">
            Llega en la siguiente entrega
          </span>
        </span>
      </div>

      <section role="tabpanel" id={panelDomId(null)} aria-labelledby={tabDomId(null)} hidden={state.active !== null}>
        <QuoteListPane active={state.active === null} onOpen={(id, meta) => void openFromList(id, meta)} />
      </section>
      {state.tabs.map((t) =>
        mounted.has(t.id) ? (
          <section key={t.id} role="tabpanel" id={panelDomId(t.id)} aria-labelledby={tabDomId(t.id)} hidden={state.active !== t.id}>
            <CotizadorPage quoteId={t.id} navigate={navigate} onMeta={(m) => setMeta(t.id, m)} />
          </section>
        ) : null,
      )}

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

function TabChip({
  tab,
  selected,
  hasPanel,
  onShow,
  onClose,
}: {
  tab: QuoteTab;
  selected: boolean;
  hasPanel: boolean;
  onShow: () => void;
  onClose: () => void;
}) {
  const label = tab.meta?.label ?? "Cotización";
  const status = tab.meta?.status ?? null;
  return (
    <div className={`${tabClass(selected)} pl-3.5 pr-1`} role="presentation">
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
        className="inline-flex h-full items-center gap-2 rounded-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
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
      {/* Not a tab stop of its own: on the keyboard, Supr closes the focused tab. */}
      <button
        type="button"
        tabIndex={-1}
        aria-label={`Cerrar ${label}`}
        title="Cerrar"
        onClick={onClose}
        className="ml-1 inline-flex h-8 w-8 items-center justify-center rounded-md text-ink-muted hover:bg-canvas-sunken hover:text-ink"
      >
        <span aria-hidden="true">×</span>
      </button>
    </div>
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
