/**
 * The body of «Hoy» (`OverviewPage`): a single «Para ti» queue of everything the operator needs
 * to act on today, followed by a one-line summary of what the system did automatically, and a
 * housekeeping link to «Operaciones → Por ordenar».
 *
 * **«Para ti»** merges (deduped by `opportunity_id`, most-urgent-first):
 *  - client replies to answer — chip «Responder»
 *  - tasks due / overdue — chip «Tarea»
 *  - follow-ups crossing a rhythm threshold today only (not every follow-up) — chip «Seguimiento»
 *  - forwarded requests still waiting for a decision — chip «Decidir»
 *
 * Each row: one chip, who (institution), what (product / quote), why now, ONE primary button and
 * a «⋯» secondary-actions menu (existing commands, not new API calls).
 *
 * **System summary**: one line «Mientras no estabas: N solicitudes abiertas · M correos archivados»
 * with a «Revisar» button that expands the existing `AutomaticActionsPanel` inline.
 *
 * **Housekeeping**: one link «N cosas por ordenar → Operaciones» that navigates to the
 * «Por decidir» area of `OperacionesPage` (tab "decidir").
 *
 * **Empty state**: when the queue is empty, «Todo al día» with the next 3 follow-ups as a preview.
 *
 * Writes go through the same commands as the drawer (`caseCommands.ts`, `crmAuthoringApi.ts`),
 * gated the same way; a viewer sees the lists without the buttons.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { addNote, fetchPersonAuthoring, refusalOf } from "../authoring/crmAuthoringApi";
import { whatsAppUrl } from "../drawer/PersonPhones";
import { useMayAuthorCrm } from "../authoring/authoring";
import {
  cancelTask,
  caseRefusalText,
  completeTask,
  createRequestFromForward,
  createTask,
  dismissForwardedRequest,
  moveCase,
  newCaseCommandKey,
  pauseCase,
  resumeCase,
  useMayRunCaseCommands,
} from "../caseCommands";
import { contactLine, displayName, quoteProduct } from "../caseDisplay";
import { planNextSteps, queueKindOf, type NextStep, type StepGroup, type StepPlan } from "../nextSteps";
import { CaseInline } from "../drawer/CaseDrawer";
import { HOY_TABS, QueueIcon, QueueListRow, stepAction, stepIcon, type HoyTab } from "./HoyQueueParts";
import { composeInSharedMailbox, gmailMessageId, inSharedMailbox } from "../gmailLinks";
import { isMaskedAddress } from "../redaction";
import { useAuthSession } from "../../context/AuthSessionContext";
import { crmHash, type CrmSection, type DatosTab } from "../crmRoute";
import type { OpportunityCardData } from "../crmTypes";
import { setCotizadorReturn } from "../quotes/cotizadorReturn";
import { quoteToOpen, useOpenOrCreateQuote } from "../quotes/openOrCreateQuote";
import {
  buildParaTiQueue,
  CLOSE_DAYS,
  filterQueue,
  followUpStep,
  followUpsDue,
  HOME_FILTER_KINDS,
  retakeHint,
  tenderClosingLines,
  wroteAfterWinning,
  type CotizarQueueItem,
  type DueTask,
  type FollowUp,
  type OverdueTask,
  useHomeFilter,
  isFollowUpDueToday,
} from "../today";

/** Local filter over the unified «Para hoy» queue by row kind, or null for all. */
export type QueueKindFilter = "reply" | "cotizar" | "followup" | "pausa" | "task" | null;
import { fetchJsonGet, operatorApiUrl } from "../../api/operatorClient";
import { Button, FormField, Modal, Segmented, TextInput, buttonClass, fmtDate, toast } from "../ui";
import { useResource } from "../useResource";
import { useLeave } from "../useLeave";
import { CaseDrawer } from "../drawer/CaseDrawer";
import type { Mode } from "./CaseActions";
import { CaseMoveForm } from "./CaseMove";
import { MailReaderDrawer, useMayReadMail } from "./MailMessage";
import { AutomaticActionsPanel, newsRows, type AutomaticAction } from "./AutomaticActionsPanel";
import type { ForwardedRequest } from "./ForwardedRequestsPanel";

type Navigate = (s: CrmSection, id?: string | null, tab?: DatosTab) => void;

/** Open a case's drawer over Hoy, optionally on one of its forms (a call, «Resolver»). */
type OpenCase = (id: string, mode?: Exclude<Mode, null>) => void;
const OpenCaseContext = createContext<OpenCase | null>(null);

/** Hoy's drawer when the row sits inside `TodayBody`; else the case page, as before. */
function useOpenCase(navigate: Navigate): OpenCase {
  const inPlace = useContext(OpenCaseContext);
  return useCallback<OpenCase>(
    (id, mode) => (inPlace ? inPlace(id, mode) : navigate("oportunidades", id)),
    [inPlace, navigate],
  );
}

/** «Mi cola» (the profile's own view) or «Todo el equipo»; session-only, never stored. */
export type TodayScope = "mine" | "team";

const WEEK_MS = 7 * 86_400_000;

/* ──────────────────────────────────────────────────────────── helpers ── */

function CaseLink({ card, navigate }: { card: OpportunityCardData; navigate: Navigate }) {
  const { name } = displayName(card);
  return (
    <button
      type="button"
      onClick={() => navigate("oportunidades", card.opportunity_id)}
      className="max-w-full truncate rounded text-left text-[13px] font-medium text-ink hover:text-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
    >
      {name}
    </button>
  );
}

/* ──────────────────────────────────────────────────── TodayBody ── */

export function TodayBody({
  items,
  navigate,
  onChanged,
  onCleared,
  onForwardCount,
  refreshing = false,
  now,
  tendersClosing,
  aside,
  scope: scopeProp,
  onScopeChange,
  queueFilter,
  onQueueFilterChange,
}: {
  items: OpportunityCardData[];
  navigate: Navigate;
  onChanged: () => void;
  /** Called each time an item is cleared from the queue — increments «M hechas» in the header. */
  onCleared?: () => void;
  /** Called with the number of forwarded requests waiting, for the header's count. */
  onForwardCount?: (n: number) => void;
  refreshing?: boolean;
  now?: Date;
  /** `tenders_closing` from the pipeline: the tenders that close tomorrow (a tender is in no other list). */
  tendersClosing?: { id: string; title: string }[];
  /** The main column's last panel: the quote number and the rates. */
  aside?: ReactNode;
  /** «Mi cola» / «Todo el equipo» when the page holds it (so its header counts follow); else local. */
  scope?: TodayScope;
  onScopeChange?: (scope: TodayScope) => void;
  /** Active kind filter from the GoalStrip or the queue's own chips. null = show all. */
  queueFilter?: QueueKindFilter;
  /** Called when the user picks a filter chip (or clears it). */
  onQueueFilterChange?: (f: QueueKindFilter) => void;
}) {
  const at = useMemo(() => now ?? new Date(), [now]);
  const { session } = useAuthSession();
  const me = session.kind === "signed_in" ? (session.profile?.displayName ?? session.operator.displayName ?? null) : null;

  // Each profile starts on its own queue (`profile.home_filter`); «Todo el equipo» shows everything.
  const homeFilter = useHomeFilter();
  const [localScope, setLocalScope] = useState<TodayScope>("mine");
  const scope = scopeProp ?? localScope;
  const setScope = (next: TodayScope) => {
    setLocalScope(next);
    onScopeChange?.(next);
  };
  const view = scope === "team" ? "todo" : homeFilter;

  const fullQueue = useMemo(() => buildParaTiQueue(items, at, me), [items, at, me]);
  const queue = useMemo(() => filterQueue(fullQueue, view), [fullQueue, view]);
  const plan = useMemo(() => planNextSteps(items, at, me, 7, (s) => HOME_FILTER_KINDS[view].has(queueKindOf(s))), [items, at, me, view]);
  const showDecidir = HOME_FILTER_KINDS[view].has("decidir");

  // Forwarded requests — fetched separately, appended to the end of the queue.
  const [fwdState, reloadFwd] = useResource(useCallback(() =>
    fetchJsonGet<{ items: ForwardedRequest[] }>(operatorApiUrl("/v2/workspace/forwarded-requests")), []));
  const [fwdGone, setFwdGone] = useState<Set<string>>(new Set());
  const allForwards: ForwardedRequest[] = fwdState.kind === "ready" && Array.isArray(fwdState.data?.items)
    ? fwdState.data.items.filter((f) => !fwdGone.has(f.id))
    : [];
  const forwards = showDecidir ? allForwards : [];
  // The header's «N para ti» counts them too, so it matches the list.
  useEffect(() => { onForwardCount?.(forwards.length); }, [forwards.length, onForwardCount]);

  const mayDecide = useMayRunCaseCommands();
  const mayAuthor = useMayAuthorCrm();
  const [closing, setClosing] = useState<OpportunityCardData | null>(null);

  // A case opens in its drawer over Hoy (no trip to the Pipeline): a row's «Abrir caso», its name,
  // «Resolver», «Registrar llamada». An id Hoy does not hold yet (a case just created from a
  // forwarded request) still goes to the case page.
  // The form request is kept with the id, not rebuilt each render: the drawer's effects watch it, so
  // a fresh object on every Hoy reload would reset the tab and reopen a form the operator closed.
  const [drawer, setDrawer] = useState<{ id: string; initial: { mode: Exclude<Mode, null> } | null } | null>(null);
  const drawerCard = drawer ? (items.find((c) => c.opportunity_id === drawer.id) ?? null) : null;
  const openCase = useCallback<OpenCase>((id, mode) => {
    if (items.some((c) => c.opportunity_id === id)) setDrawer({ id, initial: mode ? { mode } : null });
    else navigate("oportunidades", id);
  }, [items, navigate]);
  const inPlace = useCallback<Navigate>((...args) => {
    const [s, id] = args;
    if (s === "oportunidades" && id) openCase(id);
    else navigate(...args);
  }, [openCase, navigate]);

  // «Gone» IDs — items that left the queue in this render session (leave animation).
  const [goneIds, setGoneIds] = useState<Set<string>>(new Set());
  const clearItem = useCallback((id: string) => {
    setGoneIds((s) => new Set([...s, id]));
    onCleared?.();
  }, [onCleared]);

  // A tender closing tomorrow is quoting work, and the empty state's preview is the next calls:
  // each shows only in a view that holds that work, like the queue.
  const showTenders = HOME_FILTER_KINDS[view].has("licitacion");
  const tenderLines = useMemo(() => (showTenders ? tenderClosingLines(tendersClosing) : []), [tendersClosing, showTenders]);
  const totalQueue = queue.length + forwards.length + tenderLines.length;
  const showPreview = HOME_FILTER_KINDS[view].has("followup");
  // Coming up: quotes and follow-ups younger than day 3 — every older one is already in today's queue.
  const nextFollowUps = useMemo(
    () => (showPreview ? followUpsDue(items, at, { young: true }).filter((f) => !isFollowUpDueToday(f)).slice(0, 3) : []),
    [items, at, showPreview],
  );

  return (
    <OpenCaseContext.Provider value={openCase}>
    <div className="space-y-4" aria-busy={refreshing || undefined}>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_18rem] 2xl:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="min-w-0 space-y-4">

          {/* ─── «Mi cola · Ana» / «Todo el equipo» — only for a profile with its own queue; a page
              that holds the scope (OverviewPage) shows it in its header instead ─── */}
          {homeFilter !== "todo" && scopeProp === undefined ? (
            <div data-testid="today-scope">
              <Segmented<TodayScope>
                label="Qué cola ver"
                value={scope}
                onChange={setScope}
                options={[
                  { value: "mine", label: me ? `Mi cola · ${me}` : "Mi cola" },
                  { value: "team", label: "Todo el equipo" },
                ]}
              />
            </div>
          ) : null}

          {/* ─── «Para hoy» unified queue ─── */}
          <ParaTiSection
            plan={plan}
            forwards={forwards}
            goneIds={goneIds}
            navigate={inPlace}
            mayDecide={mayDecide}
            mayAuthor={mayAuthor}
            onChanged={() => { onChanged(); reloadFwd(); }}
            onClearItem={clearItem}
            onFwdDone={(id) => { setFwdGone((g) => new Set([...g, id])); onChanged(); reloadFwd(); }}
            onClose={setClosing}
            now={at}
            me={me}
            queueFilter={queueFilter ?? null}
            onQueueFilterChange={onQueueFilterChange ?? (() => {})}
          />

          {/* ─── «Licitación cierra mañana» — Hoy's one tender signal ─── */}
          {tenderLines.length > 0 ? <TenderClosingSection lines={tenderLines} navigate={inPlace} /> : null}

          {/* ─── Empty state ─── */}
          {totalQueue === 0 && (
            <EmptyState followUps={nextFollowUps} navigate={inPlace} />
          )}

          {/* ─── «Mientras no estabas» — dismissible one-liner ─── */}
          <SystemSummaryLine navigate={inPlace} onChanged={onChanged} />

        </div>
        {/* Tools at hand: the next quote number and the day's exchange rates. */}
        <aside className="min-w-0 space-y-4 lg:h-[calc(100vh-11rem)] lg:min-h-[32rem] lg:overflow-y-auto lg:pr-1">{aside}</aside>
      </div>

      {closing ? (
        <Modal title="Cerrar sin respuesta" onClose={() => setClosing(null)}>
          <p className="mb-3 truncate text-[13px] font-medium text-ink">{displayName(closing).name}</p>
          <CaseMoveForm
            card={closing}
            target="perdida"
            initialReason="Sin respuesta"
            now={at}
            onCancel={() => setClosing(null)}
            onDone={(o, refetch) => {
              if (refetch) onChanged();
              if (o.tone === "bad") {
                toast(o.lines.join(" "), "bad");
                return;
              }
              setClosing(null);
              toast("Caso cerrado como perdido.", o.tone);
            }}
          />
        </Modal>
      ) : null}
      <CaseDrawer
        card={drawerCard}
        onClose={() => setDrawer(null)}
        onChanged={onChanged}
        refreshing={refreshing}
        initial={drawer?.initial ?? null}
      />
    </div>
    </OpenCaseContext.Provider>
  );
}

/* ─────────────────────────────────────── «Licitación cierra mañana» ── */

function TenderClosingSection({ lines, navigate }: { lines: { id: string; text: string }[]; navigate: Navigate }) {
  return (
    <section aria-label="Licitaciones que cierran mañana" className="overflow-hidden rounded-xl border border-warn/30 bg-warn-bg">
      <ul className="divide-y divide-warn/20">
        {lines.map((l) => (
          <li key={l.id}>
            <a
              href={crmHash("licitaciones", l.id)}
              onClick={(e) => {
                e.preventDefault();
                navigate("licitaciones", l.id);
              }}
              className="flex min-h-[44px] items-center px-4 py-2 text-[13px] font-medium text-warn hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
            >
              {l.text}
            </a>
          </li>
        ))}
      </ul>
    </section>
  );
}

/* ────────────────────────────────────────────────── «Para ti» section ── */

const CHIP: Record<string, { label: string; cls: string; color: string }> = {
  reply:   { label: "Responder",   cls: "bg-warn-bg text-warn",   color: "text-warn" },
  cotizar: { label: "Cotizar",     cls: "bg-brand-50 text-accent", color: "text-accent" },
  task:    { label: "Tarea",       cls: "bg-bad-bg text-bad",     color: "text-bad" },
  followup:{ label: "Seguimiento", cls: "bg-good-bg text-good",   color: "text-good" },
  pausa:   { label: "Vuelve de pausa", cls: "bg-warn-bg text-warn", color: "text-warn" },
  forward: { label: "Decidir",     cls: "bg-info-bg text-info",   color: "text-info" },
};

/**
 * Tiny kind label above the row name — replaces the full-width ChipBadge so the name and
 * secondary text get more horizontal room.
 */
function KindLabel({ kind }: { kind: keyof typeof CHIP }) {
  const { label, color } = CHIP[kind] ?? CHIP.reply;
  return (
    <p className={`text-[10px] font-semibold uppercase tracking-widest ${color} leading-none`}>
      {label}
    </p>
  );
}

/* ──────────────────────────────────────── day-pill helpers ── */

/** «2 h», «1 d», «16 d» — compact wait display for the section rows. */
function formatWaitShort(ms: number): string {
  if (ms <= 0) return "0 h";
  const hours = Math.floor(ms / 3_600_000);
  if (hours < 24) return `${hours} h`;
  return `${Math.floor(ms / 86_400_000)} d`;
}

function waitTone(ms: number): "good" | "warn" | "bad" {
  const days = ms / 86_400_000;
  if (days >= 14) return "bad";
  if (days >= 3) return "warn";
  return "good";
}

const PILL_CLS: Record<"good" | "warn" | "bad", string> = {
  good: "bg-good-bg text-good",
  warn: "bg-warn-bg text-warn",
  bad:  "bg-bad-bg text-bad",
};

/**
 * The days pill: «2 h», «1 d», «16 d» — green < 3 days, amber 3–13, red ≥ 14.
 * Accessible: includes an `aria-label` for screen readers.
 */
function DayPill({ ms, label }: { ms: number; label?: string }) {
  const tone = waitTone(ms);
  const short = formatWaitShort(ms);
  const ariaLabel = label ?? `${formatWaitFull(ms)} esperando`;
  return (
    <span
      className={`inline-flex w-14 shrink-0 items-center justify-center rounded-full py-1 text-[12px] font-bold tabular-nums ${PILL_CLS[tone]}`}
      aria-label={ariaLabel}
      data-testid="day-pill"
    >
      {short}
    </span>
  );
}

function formatWaitFull(ms: number): string {
  if (ms <= 0) return "0 horas";
  const hours = Math.floor(ms / 3_600_000);
  if (hours < 24) return `${hours} ${hours === 1 ? "hora" : "horas"}`;
  const days = Math.floor(ms / 86_400_000);
  return `${days} ${days === 1 ? "día" : "días"}`;
}

/* ──────────────────────────────────────── Cotizar row ── */

function CotizarQueueRow({
  item, index, navigate, mayDecide,
}: {
  item: CotizarQueueItem;
  index: number;
  navigate: Navigate;
  mayDecide: boolean;
  onChanged: () => void;
  onClearItem: () => void;
}) {
  const { card } = item;
  const { name } = displayName(card);
  const contact = contactLine(card);
  // The requested item: from the title after "Solicitud —", or the inbound email subject.
  const solicitudItem = card.title.match(/^[Ss]olicitud\s*[—–-]+\s*(.+)$/)?.[1]?.trim()
    ?? card.last_contact?.inbound?.subject?.trim()
    ?? card.title;
  const what = [solicitudItem, contact].filter(Boolean).join(" · ");
  const originUrl = card.last_contact?.inbound?.url
    ? (inSharedMailbox(card.last_contact.inbound.url) ?? card.last_contact.inbound.url)
    : null;
  // Sales and admin go straight to the Cotizador (the open draft, else a new quote); anyone else
  // opens the case, as before.
  const toCotizador = useCallback((quoteId: string) => { setCotizadorReturn(crmHash("resumen")); navigate("cotizador", quoteId); }, [navigate]);
  const quote = useOpenOrCreateQuote(toCotizador);

  return (
    <li
      className="crm-rise group flex items-center gap-3 px-4 py-2 transition-colors hover:bg-canvas-sunken/30"
      style={{ "--i": Math.min(index, 10) } as CSSProperties}
      data-testid={`today-cotizar-${card.opportunity_id}`}
    >
      <DayPill ms={item.waitMs} />
      <button
        type="button"
        onClick={() => navigate("oportunidades", card.opportunity_id)}
        className="min-w-0 flex-1 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
      >
        <KindLabel kind="cotizar" />
        <p className="truncate text-[14px] font-semibold text-ink group-hover:text-accent">{name}</p>
        <p className="flex flex-wrap items-baseline gap-x-1.5 truncate text-[12px] text-ink-muted">
          <span className="truncate">{what}</span>
        </p>
      </button>
      <div className="flex shrink-0 gap-2">
        <Button
          variant="primary"
          busy={quote.busy}
          busyLabel="Creando…"
          onClick={() => (mayDecide ? void quote.run(card) : navigate("oportunidades", card.opportunity_id))}
        >
          {mayDecide && quoteToOpen(card) ? "Continuar cotización" : "Cotizar"}
        </Button>
        {originUrl ? (
          <a
            href={originUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex h-8 items-center rounded-md border border-line bg-canvas-raised px-3 text-xs font-medium text-ink hover:border-line-strong"
          >
            Ver pedido ↗
          </a>
        ) : null}
        {/* ⋯ placeholder — no secondary actions for cotizar yet */}
        {mayDecide ? (
          <button
            type="button"
            aria-label="Más opciones"
            onClick={() => navigate("oportunidades", card.opportunity_id)}
            className="flex h-8 w-8 items-center justify-center rounded-md text-ink-muted hover:bg-canvas-sunken hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
          >
            <svg viewBox="0 0 16 16" className="h-4 w-4" fill="currentColor" aria-hidden="true">
              <circle cx="3.5" cy="8" r="1.3" /><circle cx="8" cy="8" r="1.3" /><circle cx="12.5" cy="8" r="1.3" />
            </svg>
          </button>
        ) : null}
      </div>
    </li>
  );
}

/**
 * Five sections in the order Cotizar · Responder · Llamar (the follow-ups) · Tareas · Decidir.
 * Each section has a heading, hint and count; rows are oldest-wait-first within every section.
 * A `data-testid="today-para-ti"` wrapper is kept so existing tests can still find the area.
 */
const GROUPS: { key: StepGroup; title: string; hint: string; mark: string }[] = [
  { key: "atrasado", title: "Atrasado", hint: "volvieron de pausa o vencieron y nadie los retomó", mark: "bg-bad" },
  { key: "hoy", title: "Hoy", hint: "primero los clientes que esperan", mark: "bg-ink" },
  { key: "manana", title: "Mañana", hint: "si hoy no responden", mark: "bg-line-strong" },
  { key: "semana", title: "Esta semana", hint: "lo que viene", mark: "bg-line-strong" },
];

const FILTER_OF: Record<NextStep["kind"], Exclude<QueueKindFilter, null>> = {
  responder: "reply", cotizar: "cotizar", seguimiento: "followup", pausa: "pausa", tarea: "task",
};
const FILTER_LABEL: Record<Exclude<QueueKindFilter, null>, string> = {
  reply: "Responder", cotizar: "Cotizar", followup: "Seguimiento", pausa: "Pausas", task: "Tareas",
};
const FILTER_ORDER: Exclude<QueueKindFilter, null>[] = ["reply", "cotizar", "followup", "pausa", "task"];

function dueLabel(step: NextStep): string {
  if (step.dueDays === 1) return "mañana";
  return new Intl.DateTimeFormat("es-CL", { weekday: "short", day: "numeric" }).format(step.due).replace(".", "");
}

/** The agenda: next steps grouped by when they are due; today's rows keep every existing action. */
function ParaTiSection({
  plan, forwards, goneIds, navigate, mayDecide, mayAuthor, onChanged, onClearItem, onFwdDone, onClose, now,
  queueFilter, onQueueFilterChange,
}: {
  plan: StepPlan;
  forwards: ForwardedRequest[];
  goneIds: Set<string>;
  navigate: Navigate;
  mayDecide: boolean;
  mayAuthor: boolean;
  onChanged: () => void;
  onClearItem: (id: string) => void;
  onFwdDone: (id: string) => void;
  onClose: (c: OpportunityCardData) => void;
  now: Date;
  me: string | null;
  queueFilter: QueueKindFilter;
  onQueueFilterChange: (f: QueueKindFilter) => void;
}) {
  const [tab, setTab] = useState<HoyTab>("ahora");
  const [selected, setSelected] = useState<string | null>(null);
  const live = plan.steps.filter((s) => !goneIds.has(s.card.opportunity_id));
  if (live.length === 0 && forwards.length === 0) return null;

  // The title line, the chips and the week agree: they count what can be done today, not the projections.
  const actionable = live.filter((s) => s.actionable);
  const counts: Record<Exclude<QueueKindFilter, null>, number> = { reply: 0, cotizar: 0, followup: 0, pausa: 0, task: 0 };
  for (const s of actionable) counts[FILTER_OF[s.kind]] += 1;
  const all = actionable.length + forwards.length;
  const visible = queueFilter ? live.filter((s) => FILTER_OF[s.kind] === queueFilter) : live;
  const visibleForwards = queueFilter ? [] : forwards;
  const totalVisible = visible.filter((s) => s.actionable).length + visibleForwards.length;

  const chipCls = (active: boolean) =>
    `inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-[12px] font-semibold transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 ${
      active ? "border-ink bg-ink text-canvas" : "border-line-strong bg-canvas-raised text-ink-muted hover:bg-canvas-sunken hover:text-ink"
    }`;
  const chipCount = (n: number, active: boolean) => (
    <span className={`text-[11px] font-semibold tabular-nums ${active ? "opacity-75" : "text-ink-faint"}`}>{n}</span>
  );

  let index = 0;
  const row = (s: NextStep) => {
    const i = index++;
    const item = s.item;
    if (!item && s.state) {
      return <PauseRow key={s.id} step={s} index={i} navigate={navigate} mayDecide={mayDecide} onChanged={onChanged} onClearItem={() => onClearItem(s.card.opportunity_id)} now={now} />;
    }
    if (!item) return <UpcomingRow key={s.id} step={s} index={i} navigate={navigate} />;
    const clear = () => onClearItem(s.card.opportunity_id);
    if (item.kind === "reply") {
      return <ReplyQueueRow key={s.id} reply={item.reply} waitMs={item.waitMs} index={i} navigate={navigate} mayDecide={mayDecide} mayAuthor={mayAuthor} onChanged={onChanged} onClearItem={clear} now={now} />;
    }
    if (item.kind === "cotizar") {
      return <CotizarQueueRow key={s.id} item={item} index={i} navigate={navigate} mayDecide={mayDecide} onChanged={onChanged} onClearItem={clear} />;
    }
    if (item.kind === "followup") {
      return <FollowUpQueueRow key={s.id} followUp={item.followUp} waitMs={item.waitMs} index={i} navigate={navigate} mayDecide={mayDecide} onChanged={onChanged} onClearItem={clear} onClose={onClose} now={now} />;
    }
    return <TaskQueueRow key={s.id} due={item.due} index={i} navigate={navigate} mayDecide={mayDecide} onChanged={onChanged} onClearItem={clear} now={now} pause={s.pause} />;
  };

  const tabDef = HOY_TABS.find((t) => t.key === tab) ?? HOY_TABS[0];
  const inTab = (k: HoyTab) => visible.filter((s) => (HOY_TABS.find((t) => t.key === k)?.groups ?? []).includes(s.group));
  // In the order the list shows them: by group, then the plan's order inside each.
  const listed = GROUPS.filter((g) => tabDef.groups.includes(g.key)).flatMap((g) => inTab(tab).filter((s) => s.group === g.key));
  const listedForwards = tab === "ahora" ? visibleForwards : [];
  const keys = [...listed.map((s) => s.id), ...listedForwards.map((f) => `fwd:${f.id}`)];
  const current = selected && keys.includes(selected) ? selected : (keys[0] ?? null);
  const pos = current ? keys.indexOf(current) : -1;
  const curStep = listed.find((s) => s.id === current) ?? null;
  const curForward = current?.startsWith("fwd:") ? listedForwards.find((f) => `fwd:${f.id}` === current) ?? null : null;
  const goNext = () => setSelected(keys[pos + 1] ?? keys[pos - 1] ?? null);

  return (
    <section
      className="flex flex-col rounded-xl border border-line bg-canvas-raised lg:h-[calc(100vh-11rem)] lg:min-h-[32rem]"
      data-testid="today-para-ti"
    >
      <header className="flex flex-wrap items-center gap-x-2 gap-y-2 border-b border-line px-3 py-2.5">
        <div className="flex rounded-lg bg-canvas-sunken p-0.5" role="tablist" aria-label="Cuándo">
          {HOY_TABS.map((t) => {
            const n = t.key === "ahora" ? inTab(t.key).length + visibleForwards.length : inTab(t.key).length;
            const on = t.key === tabDef.key;
            return (
              <button key={t.key} type="button" role="tab" aria-selected={on} onClick={() => { setTab(t.key); setSelected(null); }}
                className={`h-7 rounded-md px-3 text-[12.5px] font-semibold transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 ${on ? "bg-canvas-raised text-ink shadow-[0_0_0_1px] shadow-line" : "text-ink-muted hover:text-ink"}`}
                data-testid={`hoy-tab-${t.key}`}>
                {t.label} <span className={`ml-1 tabular-nums ${on ? "text-accent" : "text-ink-faint"}`}>{n}</span>
              </button>
            );
          })}
        </div>
        <span className="text-[12px] tabular-nums text-ink-muted">
          {queueFilter && totalVisible !== all ? `${totalVisible} de ${all} para hoy` : `${all} para hoy`}
        </span>
        <div className="ml-auto flex flex-wrap gap-1.5" role="group" aria-label="Filtrar la lista">
          <button type="button" className={chipCls(!queueFilter)} onClick={() => onQueueFilterChange(null)} aria-pressed={!queueFilter}>
            Todo {chipCount(all, !queueFilter)}
          </button>
          {FILTER_ORDER.filter((k) => counts[k] > 0).map((k) => (
            <button key={k} type="button" className={chipCls(queueFilter === k)} onClick={() => onQueueFilterChange(queueFilter === k ? null : k)} aria-pressed={queueFilter === k}>
              {FILTER_LABEL[k]} {chipCount(counts[k], queueFilter === k)}
            </button>
          ))}
        </div>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[minmax(15rem,20rem)_minmax(0,1fr)]">
        <ul className="min-h-0 overflow-y-auto border-line lg:border-r" aria-label="Lista de trabajo" data-testid="hoy-queue-list">
          {GROUPS.filter((g) => tabDef.groups.includes(g.key)).map((g) => {
            const steps = listed.filter((s) => s.group === g.key);
            const fwd = g.key === "hoy" ? listedForwards : [];
            if (steps.length === 0 && fwd.length === 0) return null;
            return (
              <li key={g.key} data-testid={`today-group-${g.key}`}>
                <div className="flex items-center gap-2 border-b border-line bg-canvas-sunken/60 px-3 py-1">
                  <i className={`h-2 w-2 rounded-full ${g.mark}`} aria-hidden="true" />
                  <h3 className="text-[11px] font-bold uppercase tracking-wider text-ink">{g.title}</h3>
                  <span className="text-[11px] font-semibold tabular-nums text-ink-muted">{steps.length + fwd.length}</span>
                </div>
                <ul>
                  {steps.map((s) => (
                    <QueueListRow key={s.id} step={s} selected={s.id === current} onSelect={() => setSelected(s.id)} />
                  ))}
                  {fwd.map((f) => (
                    <li key={f.id}>
                      <button type="button" onClick={() => setSelected(`fwd:${f.id}`)} aria-current={`fwd:${f.id}` === current || undefined}
                        className={`grid w-full grid-cols-[1.75rem_minmax(0,1fr)] items-center gap-2.5 border-b border-line px-3 py-2 text-left ${`fwd:${f.id}` === current ? "bg-accent/10 shadow-[inset_3px_0_0] shadow-accent" : "hover:bg-canvas-sunken/60"}`}>
                        <QueueIcon name="inbox" />
                        <span className="min-w-0">
                          <span className="block truncate text-[13px] font-semibold text-ink">{f.customer.name || f.customer.company || f.customer.email || "Solicitud reenviada"}</span>
                          <span className="block truncate text-[12px] text-ink-muted">Solicitud reenviada · decidir</span>
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </li>
            );
          })}
          {keys.length === 0 ? (
            <li className="px-4 py-8 text-center text-[13px] text-ink-muted">
              {queueFilter ? <>No hay {FILTER_LABEL[queueFilter].toLowerCase()} aquí. <button type="button" onClick={() => onQueueFilterChange(null)} className="font-medium text-accent hover:underline">Ver todo</button></> : "Nada aquí."}
            </li>
          ) : null}
        </ul>

        <div className="min-h-0 overflow-y-auto" data-testid="hoy-queue-detail">
          {curStep || curForward ? (
            <div className="space-y-4 p-4 lg:p-5">
              <div className="flex items-center gap-2">
                {curStep ? <QueueIcon name={stepIcon(curStep)} size="lg" /> : <QueueIcon name="inbox" size="lg" />}
                <span className="text-[12px] font-semibold uppercase tracking-wider text-ink-muted">{curStep ? stepAction(curStep) : "Solicitud reenviada"}</span>
                <span className="ml-auto text-[12px] tabular-nums text-ink-faint">{pos + 1} de {keys.length}</span>
                <button type="button" onClick={goNext} disabled={keys.length < 2}
                  className="h-7 rounded-md border border-line-strong px-2.5 text-[12px] font-semibold text-ink-muted hover:bg-canvas-sunken disabled:opacity-40">
                  Siguiente →
                </button>
              </div>
              {curStep ? <CaseInline card={curStep.card} onChanged={onChanged} /> : null}
              {curStep ? (
                <details className="rounded-lg border border-line" data-testid="hoy-shortcuts">
                  <summary className="cursor-pointer px-3 py-2 text-[12px] font-semibold text-ink-muted hover:text-ink">
                    Atajos de Hoy · no requiere respuesta, retomar, completar
                  </summary>
                  <ul className="border-t border-line">{row(curStep)}</ul>
                </details>
              ) : null}
              {curForward ? (
                <ul className="overflow-hidden rounded-lg border border-line">
                  <ForwardQueueRow item={curForward} index={0} navigate={navigate} mayDecide={mayDecide} onDone={(id) => { onFwdDone(id); goNext(); }} />
                </ul>
              ) : null}
            </div>
          ) : (
            <p className="p-8 text-center text-[13px] text-ink-muted">Elige algo de la lista.</p>
          )}
        </div>
      </div>
    </section>
  );
}

/** A step that is not due yet: what comes and when, with the case a click away. */
function UpcomingRow({ step, index, navigate }: { step: NextStep; index: number; navigate: Navigate }) {
  const kind = step.kind === "seguimiento" ? "followup" : step.kind === "pausa" ? "pausa" : "task";
  return (
    <li
      className="crm-rise flex items-center gap-3 px-4 py-2 text-ink-muted transition-colors hover:bg-canvas-sunken/30"
      style={{ "--i": Math.min(index, 10) } as CSSProperties}
      data-testid={`today-upcoming-${step.id}`}
    >
      <span className="inline-flex w-14 shrink-0 items-center justify-center rounded-full border border-line py-0.5 text-[11.5px] font-semibold tabular-nums text-ink-faint">
        {dueLabel(step)}
      </span>
      <div className="min-w-0 flex-1">
        <KindLabel kind={kind} />
        <p className="flex min-w-0 flex-wrap items-baseline gap-x-2 text-[13px]">
          <CaseLink card={step.card} navigate={navigate} />
          <span className="truncate text-[12.5px] text-ink-faint">{step.label}{step.pause?.hint ? ` · ${step.pause.hint}` : ""}</span>
        </p>
      </div>
      <Button onClick={() => navigate("oportunidades", step.card.opportunity_id)}>Ver caso</Button>
    </li>
  );
}

/* ─────────────────────────────────────────────── Returned pause row ── */

const PAUSE_EXTEND_DAYS = 14;

/**
 * A case whose pause came back (`paused_until` is today or earlier): write and ask what the pause
 * was waiting for, open the case, resume it, or keep it paused two more weeks.
 */
function PauseRow({
  step, index, navigate, mayDecide, onChanged, onClearItem, now,
}: {
  step: NextStep;
  index: number;
  navigate: Navigate;
  mayDecide: boolean;
  onChanged: () => void;
  onClearItem: () => void;
  now: Date;
}) {
  const { card } = step;
  const reason = step.pause?.reason ?? "Sin motivo";
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const [busy, setBusy] = useState<null | "resume" | "extend">(null);
  const keys = useRef({ resume: newCaseCommandKey(), extend: newCaseCommandKey() });
  const to = card.contact?.address && !isMaskedAddress(card.contact.address) ? card.contact.address : null;
  const overdueMs = Math.max(0, -step.dueDays) * 86_400_000;

  useEffect(() => {
    if (!menuOpen) return;
    const away = (e: Event) => { if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setMenuOpen(false); };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); };
  }, [menuOpen]);

  async function resume() {
    setBusy("resume");
    try {
      await resumeCase({ opportunity_id: card.opportunity_id, version: card.version as number }, "Retomado desde «Hoy».", keys.current.resume);
      toast("Pausa retomada");
      onClearItem();
      onChanged();
    } catch (err) {
      keys.current.resume = newCaseCommandKey();
      toast(caseRefusalText(err), "bad");
    } finally { setBusy(null); }
  }

  async function extend() {
    setBusy("extend");
    const day = new Date(now.getFullYear(), now.getMonth(), now.getDate() + PAUSE_EXTEND_DAYS);
    const iso = `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}`;
    try {
      await pauseCase({ opportunity_id: card.opportunity_id, version: card.version as number }, iso, reason, `Sigue en pausa ${PAUSE_EXTEND_DAYS} días más, desde «Hoy».`, keys.current.extend);
      toast(`En pausa hasta ${fmtDate(`${iso}T12:00:00`)}`);
      onClearItem();
      onChanged();
    } catch (err) {
      keys.current.extend = newCaseCommandKey();
      toast(caseRefusalText(err), "bad");
    } finally { setBusy(null); }
  }

  const menuItem = "flex w-full items-center rounded px-2.5 py-1.5 text-left text-[12.5px] text-ink hover:bg-canvas-sunken focus:bg-canvas-sunken focus:outline-none disabled:opacity-50";
  return (
    <li
      className={`crm-rise group relative flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-2 transition-colors hover:bg-canvas-sunken/30 ${menuOpen ? "z-20" : ""}`}
      style={{ "--i": Math.min(index, 10) } as CSSProperties}
      data-testid={`today-pause-${card.opportunity_id}`}
    >
      <DayPill ms={overdueMs} label={`volvió hace ${Math.max(0, -step.dueDays)} días`} />
      <div className="min-w-[12rem] flex-1">
        <KindLabel kind="pausa" />
        <p className="flex flex-wrap items-baseline gap-x-2 text-[13px] text-ink">
          <CaseLink card={card} navigate={navigate} />
          <span className="text-ink-muted">En pausa · {reason}</span>
        </p>
        <p className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-[11px] text-ink-muted">
          <span className="font-medium text-bad">{step.dueDays === 0 ? "Vuelve hoy" : `Volvió hace ${-step.dueDays} ${-step.dueDays === 1 ? "día" : "días"}`}</span>
          {step.pause?.hint ? <><span aria-hidden="true">·</span><span data-testid="today-task-hint">→ {step.pause.hint}</span></> : null}
        </p>
      </div>
      <div className="flex shrink-0 gap-2">
        <a className={buttonClass("secondary")} href={composeInSharedMailbox(to, `Retomamos la cotización ${card.quote_numbers[0] ?? ""}`.trim())}
           target="_blank" rel="noopener noreferrer" aria-label="Escribir en Gmail para retomar">Escribir</a>
        <Button variant="primary" onClick={() => navigate("oportunidades", card.opportunity_id)}>Abrir caso</Button>
        {mayDecide ? (
          <div ref={menuRef} className="relative shrink-0">
            <button type="button" aria-haspopup="menu" aria-expanded={menuOpen} aria-label="Más opciones" onClick={() => setMenuOpen((v) => !v)}
              className="flex h-8 w-8 items-center justify-center rounded-md text-ink-muted hover:bg-canvas-sunken hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600">
              <svg viewBox="0 0 16 16" className="h-4 w-4" fill="currentColor" aria-hidden="true"><circle cx="3.5" cy="8" r="1.3" /><circle cx="8" cy="8" r="1.3" /><circle cx="12.5" cy="8" r="1.3" /></svg>
            </button>
            {menuOpen ? (
              <div role="menu" className="absolute right-0 top-9 z-20 w-56 rounded-lg border border-line bg-canvas-raised p-1 shadow-lg">
                <button type="button" role="menuitem" className={menuItem} disabled={busy !== null} onClick={() => { setMenuOpen(false); void resume(); }}>
                  {busy === "resume" ? "Guardando…" : "Retomar ahora"}
                </button>
                <button type="button" role="menuitem" className={menuItem} disabled={busy !== null} onClick={() => { setMenuOpen(false); void extend(); }}>
                  {busy === "extend" ? "Guardando…" : `Seguir en pausa ${PAUSE_EXTEND_DAYS} días`}
                </button>
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </li>
  );
}

/* ─────────────────────────────────────────────── Reply row ── */

function ReplyQueueRow({
  reply, waitMs, index, navigate, mayDecide, mayAuthor, onChanged, onClearItem, now,
}: {
  reply: import("../today").Reply;
  waitMs: number;
  index: number;
  navigate: Navigate;
  mayDecide: boolean;
  mayAuthor: boolean;
  onChanged: () => void;
  onClearItem: () => void;
  now: Date;
}) {
  const openCase = useOpenCase(navigate);
  const { card } = reply;
  const { leaving, gone, leave, restore } = useLeave(reply);
  const [busy, setBusy] = useState<null | "move" | "skip" | "handled">(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const keys = useRef([newCaseCommandKey()]);
  const skipKeys = useRef<string[]>([]);
  const handledKey = useRef(newCaseCommandKey());
  const menuRef = useRef<HTMLDivElement>(null);
  const blocking = card.attention.find((a) => a.blocking) ?? null;
  const won = wroteAfterWinning(card);
  // «Responder» opens the client's email here; Gmail is one click away inside (owner 2026-10-25).
  const mayRead = useMayReadMail();
  const messageId = gmailMessageId(reply.url);
  const [reading, setReading] = useState(false);
  const canMove = !blocking && mayDecide && card.stage === "quoting" && typeof card.version === "number";
  const product = quoteProduct(card);

  useEffect(() => {
    if (!menuOpen) return;
    const away = (e: Event) => { if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setMenuOpen(false); };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); };
  }, [menuOpen]);

  async function noAnswerNeeded() {
    setBusy("skip");
    const next = new Date(now.getTime() + WEEK_MS);
    leave();
    try {
      for (const [i, c] of [card, ...reply.others].entries()) {
        const quote = c.latest_revision?.quote_number ?? c.quote_numbers[0] ?? "";
        skipKeys.current[i] ??= newCaseCommandKey();
        await createTask(
          { opportunity_id: c.opportunity_id, title: `Seguimiento de ${quote}`.trim(), due_at: next.toISOString(), note: `El cliente escribió el ${fmtDate(reply.at)}; no requiere respuesta.` },
          skipKeys.current[i],
        );
      }
      toast(`Listo. Te lo recuerdo el ${fmtDate(next.toISOString())}.`);
      onClearItem(); onChanged();
    } catch (err) {
      restore();
      skipKeys.current = [];
      toast(caseRefusalText(err), "bad");
    } finally { setBusy(null); }
  }

  async function handled() {
    setBusy("handled");
    leave();
    try {
      await addNote(
        { subject_kind: "opportunity", subject_id: card.opportunity_id, body: `Atendido: el cliente escribió el ${fmtDate(reply.at)} tras ganar el caso; respondido por otro medio o sin respuesta pendiente.` },
        handledKey.current,
      );
      toast("Listo. Queda anotado en el caso.");
      onClearItem(); onChanged();
    } catch (err) {
      restore();
      handledKey.current = newCaseCommandKey();
      toast(refusalOf(err)?.message ?? "No se pudo anotar el caso.", "bad");
    } finally { setBusy(null); }
  }

  async function toConversation() {
    setBusy("move");
    leave();
    try {
      await moveCase(
        { opportunity_id: card.opportunity_id, stage: card.stage, version: card.version as number },
        "negotiating",
        `El cliente respondió el ${fmtDate(reply.at)}.`,
        null, keys.current,
      );
      toast("Pasó a «Conversación».");
      onClearItem(); onChanged();
    } catch (err) {
      restore();
      keys.current = [newCaseCommandKey()];
      toast(caseRefusalText(err), "bad");
    } finally { setBusy(null); }
  }

  if (gone) return null;
  const menuItem = "flex w-full items-center rounded px-2.5 py-1.5 text-left text-[12.5px] text-ink hover:bg-canvas-sunken focus:bg-canvas-sunken focus:outline-none disabled:opacity-50";
  const { name } = displayName(card);
  const what = [
    won ? "Escribió tras ganar" : card.latest_revision ? "Respondió" : "Escribió",
    ...(product ? [product] : []),
    ...(contactLine(card) ? [contactLine(card)!] : []),
  ].join(" · ");
  return (
    <li
      className={`crm-rise group relative flex items-center gap-3 px-4 py-2 transition-colors hover:bg-canvas-sunken/30 ${menuOpen ? "z-20" : ""} ${leaving ? "crm-row-out" : ""}`}
      style={{ "--i": Math.min(index, 10) } as CSSProperties}
      data-testid={`today-reply-${card.opportunity_id}`}
    >
      <DayPill ms={waitMs} />
      <div className="min-w-0 flex-1">
        <KindLabel kind="reply" />
        <button
          type="button"
          onClick={() => navigate("oportunidades", card.opportunity_id)}
          className="block w-full min-w-0 truncate text-left text-[14px] font-semibold text-ink hover:text-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
        >
          {name}
        </button>
        <p className="flex flex-wrap items-baseline gap-x-1.5 truncate text-[12px] text-ink-muted">
          <span className="truncate">{what}</span>
        </p>
        {reply.others.length ? (
          <p className="mt-0.5 truncate text-[11px] text-ink-muted">
            También en:{" "}
            {reply.others.map((o, i) => (
              <span key={o.opportunity_id}>
                {i ? ", " : ""}
                <button type="button" className="text-accent hover:underline" onClick={() => navigate("oportunidades", o.opportunity_id)}>
                  {o.title}
                </button>
              </span>
            ))}
            <span className="text-ink-faint"> · el mismo correo</span>
          </p>
        ) : null}
        {blocking ? <p className="mt-0.5 truncate text-[11px] font-medium text-bad">{blocking.label}</p> : null}
      </div>
      <div className="flex shrink-0 gap-2">
        {/* Primary button */}
        {blocking ? (
          <Button variant="primary" onClick={() => openCase(card.opportunity_id, "resolve")}>Resolver</Button>
        ) : reply.url && mayRead && messageId ? (
          <Button variant="primary" onClick={() => setReading(true)}>Responder</Button>
        ) : reply.url ? (
          <a
            href={inSharedMailbox(reply.url) ?? undefined}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex h-8 items-center rounded-md bg-brand-600 px-3 text-xs font-semibold text-white shadow-sm hover:bg-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 focus-visible:ring-offset-1"
          >
            Responder en Gmail ↗
          </a>
        ) : (
          <Button variant="primary" onClick={() => navigate("oportunidades", card.opportunity_id)}>Abrir caso</Button>
        )}
        {/* Secondary ⋯ menu */}
        {mayDecide && !blocking ? (
          <div ref={menuRef} className="relative shrink-0">
            <button
              type="button"
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              aria-label="Más opciones"
              onClick={() => setMenuOpen((v) => !v)}
              className="flex h-8 w-8 items-center justify-center rounded-md text-ink-muted hover:bg-canvas-sunken hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
            >
              <svg viewBox="0 0 16 16" className="h-4 w-4" fill="currentColor" aria-hidden="true">
                <circle cx="3.5" cy="8" r="1.3" /><circle cx="8" cy="8" r="1.3" /><circle cx="12.5" cy="8" r="1.3" />
              </svg>
            </button>
            {menuOpen ? (
              <div role="menu" className="absolute right-0 top-9 z-20 w-56 rounded-lg border border-line bg-canvas-raised p-1 shadow-lg">
                {!won ? (
                  <button type="button" role="menuitem" className={menuItem} disabled={busy !== null} onClick={() => { setMenuOpen(false); void noAnswerNeeded(); }}>
                    {busy === "skip" ? "Guardando…" : "No requiere respuesta"}
                  </button>
                ) : won && mayAuthor ? (
                  <button type="button" role="menuitem" className={menuItem} disabled={busy !== null} onClick={() => { setMenuOpen(false); void handled(); }}>
                    {busy === "handled" ? "Anotando…" : "Atendido"}
                  </button>
                ) : null}
                {canMove ? (
                  <button type="button" role="menuitem" className={menuItem} disabled={busy !== null} onClick={() => { setMenuOpen(false); void toConversation(); }}>
                    {busy === "move" ? "Moviendo…" : "Pasar a Conversación"}
                  </button>
                ) : null}
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
      {reading && messageId ? (
        <MailReaderDrawer
          open
          onClose={() => setReading(false)}
          title={name}
          subject={card.last_contact?.inbound?.subject ?? null}
          opportunityId={card.opportunity_id}
          messageId={messageId}
          gmailUrl={inSharedMailbox(reply.url)}
          extra={
            <Button variant="secondary" onClick={() => { setReading(false); navigate("oportunidades", card.opportunity_id); }}>
              Abrir caso
            </Button>
          }
        />
      ) : null}
    </li>
  );
}

function replyWaitLabel(at: string, now: Date): string {
  const ms = now.getTime() - Date.parse(at);
  if (ms < 0) return fmtDate(at);
  const mins = Math.floor(ms / 60_000);
  if (mins < 60) return `hace ${mins} ${mins === 1 ? "min" : "min"}`;
  const hours = Math.floor(ms / 3_600_000);
  if (hours < 24) return `hace ${hours} ${hours === 1 ? "hora" : "horas"}`;
  const days = Math.floor(ms / 86_400_000);
  return `hace ${days} ${days === 1 ? "día" : "días"}`;
}

/* ─────────────────────────────────────────────────── Task row ── */

function TaskQueueRow({
  due, index, navigate, mayDecide, onChanged, onClearItem, now, pause = null,
}: {
  due: DueTask | OverdueTask;
  /** A «Retomar: …» task: the case came back from a pause; shown as such, with the reason. */
  pause?: { reason: string; hint: string | null } | null;
  index: number;
  navigate: Navigate;
  mayDecide: boolean;
  onChanged: () => void;
  onClearItem: () => void;
  now: Date;
}) {
  const { card, task, overdueDays } = due;
  const { leaving, gone, leave, restore } = useLeave(due);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const [busy, setBusy] = useState<null | "done" | "snooze">(null);
  const keys = useRef({ done: newCaseCommandKey(), create: newCaseCommandKey(), cancel: newCaseCommandKey() });
  // "escalated" is only on OverdueTask
  const escalated = "escalated" in due ? (due as OverdueTask).escalated : false;
  const pauseTo = card.contact?.address && !isMaskedAddress(card.contact.address) ? card.contact.address : null;

  useEffect(() => {
    if (!menuOpen) return;
    const away = (e: Event) => { if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setMenuOpen(false); };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); };
  }, [menuOpen]);

  async function done() {
    setBusy("done");
    leave();
    try {
      await completeTask({ task_id: task.task_id, task_version: task.version, note: "Hecho desde «Hoy»." }, keys.current.done);
      toast("Tarea hecha.");
      onClearItem(); onChanged();
    } catch (err) {
      restore();
      keys.current.done = newCaseCommandKey();
      toast(caseRefusalText(err), "bad");
    } finally { setBusy(null); }
  }

  async function snooze() {
    setBusy("snooze");
    const next = new Date(Math.max(now.getTime(), Date.parse(task.due_at)) + WEEK_MS);
    leave();
    try {
      await createTask(
        { opportunity_id: card.opportunity_id, title: task.title, due_at: next.toISOString(), note: "Pospuesta una semana desde «Hoy»." },
        keys.current.create,
      );
      await cancelTask({ task_id: task.task_id, task_version: task.version, note: `Pospuesta al ${fmtDate(next.toISOString())}.` }, keys.current.cancel);
      toast(`Pospuesta al ${fmtDate(next.toISOString())}.`);
      onClearItem(); onChanged();
    } catch (err) {
      restore();
      keys.current = { done: keys.current.done, create: newCaseCommandKey(), cancel: newCaseCommandKey() };
      toast(caseRefusalText(err), "bad");
      onChanged();
    } finally { setBusy(null); }
  }

  if (gone) return null;
  const menuItem = "flex w-full items-center rounded px-2.5 py-1.5 text-left text-[12.5px] text-ink hover:bg-canvas-sunken focus:bg-canvas-sunken focus:outline-none disabled:opacity-50";
  return (
    <li
      className={`crm-rise group relative flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-2 transition-colors hover:bg-canvas-sunken/30 ${menuOpen ? "z-20" : ""} ${leaving ? "crm-row-out" : ""}`}
      style={{ "--i": Math.min(index, 10) } as CSSProperties}
      data-testid={`today-task-${task.task_id}`}
    >
      <div className="min-w-[12rem] flex-1">
        <KindLabel kind={pause ? "pausa" : "task"} />
        <p className="flex flex-wrap items-baseline gap-x-2 text-[13px] text-ink">
          <CaseLink card={card} navigate={navigate} />
          <span className="text-ink-muted">{pause ? <>En pausa · {pause.reason}</> : task.title}</span>
        </p>
        <p className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-[11px] text-ink-muted">
          <span className={overdueDays > 0 ? "font-medium text-bad" : "font-medium text-warn"}>{dueText(overdueDays)}</span>
          {escalated && task.owner ? (
            <><span aria-hidden="true">·</span><span data-testid="today-owner-label">de {task.owner}</span></>
          ) : null}
          {retakeHint(task.title) ? (
            <><span aria-hidden="true">·</span><span data-testid="today-task-hint">→ {retakeHint(task.title)}</span></>
          ) : null}
        </p>
      </div>
      <div className="flex shrink-0 gap-2">
        {/* A returned pause: the step is to write and ask; the thread's address, when the viewer may see it. */}
        {pause ? (
          <a
            className={buttonClass("secondary")}
            href={composeInSharedMailbox(pauseTo, `Retomamos la cotización ${card.quote_numbers[0] ?? ""}`.trim())}
            target="_blank" rel="noopener noreferrer" aria-label="Escribir en Gmail para retomar"
          >
            Escribir
          </a>
        ) : null}
        <Button variant="primary" onClick={() => navigate("oportunidades", card.opportunity_id)}>
          Abrir caso
        </Button>
        {/* Secondary ⋯: Hecho + +1 semana */}
        {mayDecide ? (
          <div ref={menuRef} className="relative shrink-0">
            <button
              type="button"
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              aria-label="Más opciones"
              onClick={() => setMenuOpen((v) => !v)}
              className="flex h-8 w-8 items-center justify-center rounded-md text-ink-muted hover:bg-canvas-sunken hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
            >
              <svg viewBox="0 0 16 16" className="h-4 w-4" fill="currentColor" aria-hidden="true">
                <circle cx="3.5" cy="8" r="1.3" /><circle cx="8" cy="8" r="1.3" /><circle cx="12.5" cy="8" r="1.3" />
              </svg>
            </button>
            {menuOpen ? (
              <div role="menu" className="absolute right-0 top-9 z-20 w-48 rounded-lg border border-line bg-canvas-raised p-1 shadow-lg">
                <button type="button" role="menuitem" className={menuItem} disabled={busy !== null} onClick={() => { setMenuOpen(false); void done(); }}>
                  {busy === "done" ? "Guardando…" : "Hecho"}
                </button>
                <button type="button" role="menuitem" className={menuItem} disabled={busy !== null} onClick={() => { setMenuOpen(false); void snooze(); }}>
                  {busy === "snooze" ? "Guardando…" : "+1 semana"}
                </button>
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </li>
  );
}

function dueText(overdueDays: number): string {
  return overdueDays === 0 ? "Vence hoy" : overdueDays === 1 ? "Atrasada 1 día" : `Atrasada ${overdueDays} días`;
}

/* ─────────────────────────────────────────────── Follow-up helpers ── */

const FOLLOW_UP_ACTION: Record<1 | 2 | 3 | 4, string> = {
  1: "Correo",
  2: "Llamar",
  3: "WhatsApp",
  4: "Último correo",
};

/** Chip showing what to do on this step; «Cerrar caso» appears from day `CLOSE_DAYS` onward. */
function FollowUpActionChip({ step, days }: { step: 1 | 2 | 3 | 4; days: number }) {
  const label = FOLLOW_UP_ACTION[step];
  const close = days >= CLOSE_DAYS;
  return (
    <>
      <span
        className="inline-flex shrink-0 items-center rounded-full bg-canvas-sunken px-1.5 py-px text-[10.5px] font-medium text-ink-muted"
        data-testid="today-followup-action"
      >
        → {label}
      </span>
      {close ? (
        <span
          className="inline-flex shrink-0 items-center rounded-full bg-bad-bg px-1.5 py-px text-[10.5px] font-semibold text-bad"
          data-testid="today-followup-close-hint"
          title="Sin respuesta en más de 45 días — considera cerrar el caso"
        >
          Cerrar caso
        </span>
      ) : null}
    </>
  );
}

/**
 * Phone links (tel: and wa.me) for the «Llamar» (step 2) and «WhatsApp» (step 3) steps.
 * Fetches the person's active phone lazily; shows nothing while loading or if no phone is found.
 * Step 2 shows a «Llamar» tel: link; step 3 shows a WhatsApp link.
 */
function FollowUpPhoneLinks({ personId, step }: { personId: string; step: 2 | 3 }) {
  const load = useCallback(() => fetchPersonAuthoring(personId), [personId]);
  const [state] = useResource(load, [personId]);
  if (state.kind !== "ready") return null;
  const phones = state.data.contact_points.filter((c) => c.kind === "phone" && c.status === "active");
  if (phones.length === 0) return null;
  const p = phones[0];
  const dial = p.value_norm || p.value_display.replace(/[^\d+]/g, "");
  const wa = whatsAppUrl(p.value_norm || p.value_display);
  if (step === 3) {
    if (!wa) return null;
    return (
      <p className="mt-0.5 truncate text-[11px]">
        <a
          href={wa}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 font-medium text-good hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
          aria-label={`WhatsApp al ${p.value_display}`}
          data-testid="today-followup-wa-link"
        >
          <svg viewBox="0 0 16 16" className="h-3.5 w-3.5 shrink-0" fill="currentColor" aria-hidden="true"><path d="M8 1a7 7 0 1 0 2.9 13.37L14 14l-.64-3.08A7 7 0 0 0 8 1Zm0 1.4A5.6 5.6 0 1 1 5.08 12l-.16-.27-1.68.44.44-1.68-.27-.16A5.6 5.6 0 0 1 8 2.4Zm-1.5 2.9c-.15 0-.38.05-.58.27-.2.21-.76.74-.76 1.8 0 1.07.78 2.1.89 2.25.11.14 1.53 2.34 3.72 3.19.52.2.93.32 1.24.41.52.16 1 .14 1.37.08.42-.07 1.29-.53 1.47-1.04.18-.51.18-.95.13-1.04-.05-.09-.2-.14-.41-.25-.21-.11-1.28-.63-1.47-.7-.2-.07-.34-.11-.49.11-.14.21-.56.7-.69.84-.13.14-.26.16-.48.05-.21-.11-.9-.33-1.72-1.06-.64-.57-1.07-1.27-1.19-1.49-.13-.21-.01-.33.1-.43.1-.1.21-.26.32-.38.1-.13.14-.21.21-.35.07-.14.03-.27-.01-.38-.05-.11-.49-1.18-.67-1.62-.18-.43-.36-.37-.49-.38l-.41-.01Z"/></svg>
          WhatsApp {p.value_display} ↗
        </a>
      </p>
    );
  }
  // step === 2: tel: link
  return (
    <p className="mt-0.5 truncate text-[11px]">
      <a
        href={`tel:${dial}`}
        className="inline-flex items-center gap-1 font-medium text-ink hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
        aria-label={`Llamar al ${p.value_display}`}
        data-testid="today-followup-tel-link"
      >
        <svg viewBox="0 0 16 16" className="h-3.5 w-3.5 shrink-0" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="M5.5 2.5A11.6 11.6 0 0 0 6.6 5L5 6.5c.7 1.4 1.7 2.7 3 3.5L9.5 8.5a11.5 11.5 0 0 0 2.5 1.1v2a12.3 12.3 0 0 1-8.5-8.5l2-.6Z" strokeLinejoin="round"/></svg>
        {p.value_display}
      </a>
    </p>
  );
}

/* ─────────────────────────────────────────────── Follow-up row ── */

function FollowUpQueueRow({
  followUp, waitMs, index, navigate, mayDecide, onChanged, onClearItem, onClose, now,
}: {
  followUp: FollowUp;
  waitMs: number;
  index: number;
  navigate: Navigate;
  mayDecide: boolean;
  onChanged: () => void;
  onClearItem: () => void;
  onClose: (c: OpportunityCardData) => void;
  now: Date;
}) {
  const openCase = useOpenCase(navigate);
  const f = followUp;
  const { leaving, gone, leave, restore } = useLeave(f);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const [busy, setBusy] = useState<null | "done" | "snooze">(null);
  const keys = useRef({ done: newCaseCommandKey(), create: newCaseCommandKey(), cancel: newCaseCommandKey() });

  const product = quoteProduct(f.card);
  const quote = f.card.latest_revision?.quote_number ?? f.card.quote_numbers[0] ?? "";
  const thread = inSharedMailbox(f.card.last_contact?.outbound?.url ?? f.card.latest_revision?.gmail?.url);
  const to = f.card.contact?.address && !isMaskedAddress(f.card.contact.address) ? f.card.contact.address : null;
  const write = thread ?? composeInSharedMailbox(to, `Seguimiento cotización N° ${quote}`.trim());
  const note = f.card.last_note?.body?.trim() ? f.card.last_note : null;

  useEffect(() => {
    if (!menuOpen) return;
    const away = (e: Event) => { if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setMenuOpen(false); };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); };
  }, [menuOpen]);

  async function markDone() {
    if (!f.task) return;
    setBusy("done");
    setMenuOpen(false);
    leave();
    try {
      await completeTask({ task_id: f.task.task.task_id, task_version: f.task.task.version, note: "Hecho desde «Hoy»." }, keys.current.done);
      toast("Seguimiento hecho.");
      onClearItem(); onChanged();
    } catch (err) {
      restore();
      keys.current.done = newCaseCommandKey();
      toast(caseRefusalText(err), "bad");
    } finally { setBusy(null); }
  }

  async function snooze() {
    setBusy("snooze");
    const base = f.task ? Math.max(now.getTime(), Date.parse(f.task.task.due_at)) : now.getTime();
    const next = new Date(base + WEEK_MS);
    setMenuOpen(false);
    leave();
    try {
      await createTask(
        { opportunity_id: f.card.opportunity_id, title: f.task?.task.title ?? `Seguimiento de ${quote}`.trim(), due_at: next.toISOString(), note: "Pospuesta una semana desde «Hoy»." },
        keys.current.create,
      );
      if (f.task) {
        await cancelTask({ task_id: f.task.task.task_id, task_version: f.task.task.version, note: `Pospuesta al ${fmtDate(next.toISOString())}.` }, keys.current.cancel);
      }
      toast(`Te lo recuerdo el ${fmtDate(next.toISOString())}.`);
      onClearItem(); onChanged();
    } catch (err) {
      restore();
      keys.current = { done: keys.current.done, create: newCaseCommandKey(), cancel: newCaseCommandKey() };
      toast(caseRefusalText(err), "bad");
      onChanged();
    } finally { setBusy(null); }
  }

  if (gone) return null;
  const menuItem = "flex w-full items-center rounded px-2.5 py-1.5 text-left text-[12.5px] text-ink hover:bg-canvas-sunken focus:bg-canvas-sunken focus:outline-none disabled:opacity-50";
  const { name } = displayName(f.card);
  const fStep = followUpStep(f); // cached so TypeScript can narrow the type for FollowUpPhoneLinks
  const what = [
    ...(product ? [product] : []),
    ...(quote ? [quote] : []),
    ...(contactLine(f.card) ? [contactLine(f.card)!] : []),
  ].join(" · ");
  return (
    <li
      className={`crm-rise group relative flex items-center gap-3 px-4 py-2 transition-colors hover:bg-canvas-sunken/30 ${menuOpen ? "z-20" : ""} ${leaving ? "crm-row-out" : ""}`}
      style={{ "--i": Math.min(index, 10) } as CSSProperties}
      data-testid={f.task ? `today-task-${f.task.task.task_id}` : `today-followup-${f.card.opportunity_id}`}
    >
      <DayPill ms={waitMs} />
      <div className="min-w-0 flex-1">
        <KindLabel kind="followup" />
        <button
          type="button"
          onClick={() => navigate("oportunidades", f.card.opportunity_id)}
          className="block w-full min-w-0 truncate text-left text-[14px] font-semibold text-ink hover:text-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
        >
          {name}
        </button>
        <p className="flex flex-wrap items-baseline gap-x-1.5 truncate text-[12px] text-ink-muted">
          <span className="font-semibold text-ink" data-testid="today-followup-step">Seguimiento {fStep} de 4</span>
          <FollowUpActionChip step={fStep} days={f.days} />
          {what ? <span className="truncate">{` · ${what}`}</span> : null}
        </p>
        {(fStep === 2 || fStep === 3) && f.card.contact?.person_id ? (
          <FollowUpPhoneLinks personId={f.card.contact.person_id} step={fStep} />
        ) : null}
        {note || f.task ? (
          <p className="truncate text-[11.5px] text-ink" data-testid="today-followup-note">
            {f.task ? (
              <span className="mr-1.5 inline-flex items-center gap-1 rounded-full bg-warn-bg px-1.5 py-px align-[1px] text-[10.5px] font-semibold text-warn" data-testid="today-reminder-chip">
                <svg viewBox="0 0 16 16" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><circle cx="8" cy="8.5" r="5.5" /><path d="M8 5.5v3l2 1.5" strokeLinecap="round" /></svg>
                Tu recordatorio
              </span>
            ) : null}
            {note ? <><span className="font-medium text-warn">Nota:</span> {note.body}<span className="text-ink-faint"> · {note.author ? `${note.author}, ` : ""}{fmtDate(note.created_at)}</span></> : null}
          </p>
        ) : null}
      </div>
      <div className="flex shrink-0 gap-2">
        {/* Primary action depends on step: Llamar shows «Registrar llamada»; all steps keep «Escribir». */}
        {mayDecide ? (
          <Button variant="primary" onClick={() => openCase(f.card.opportunity_id, "call")}>
            Registrar llamada
          </Button>
        ) : null}
        {/* Secondary: «Escribir» — open the Gmail thread («mejor por correo») */}
        <a
          href={write}
          target="_blank"
          rel="noopener noreferrer"
          title={thread ? "Abre el hilo de la cotización en Gmail" : "Abre un correo nuevo en Gmail"}
          aria-label={thread ? "Escribir en Gmail" : "Nuevo correo en Gmail"}
          className="inline-flex h-8 items-center gap-1.5 rounded-md border border-line bg-canvas-raised px-3 text-xs font-medium text-ink hover:border-line-strong focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 focus-visible:ring-offset-1"
        >
          <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="M2.5 4h11v8h-11zM2.5 4.5 8 8.5l5.5-4" strokeLinejoin="round" /></svg>
          Escribir
        </a>
        {/* Secondary ⋯ */}
        {mayDecide ? (
          <div ref={menuRef} className="relative shrink-0">
            <button
              type="button"
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              aria-label="Más opciones"
              onClick={() => setMenuOpen((v) => !v)}
              className="flex h-8 w-8 items-center justify-center rounded-md text-ink-muted hover:bg-canvas-sunken hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
            >
              <svg viewBox="0 0 16 16" className="h-4 w-4" fill="currentColor" aria-hidden="true">
                <circle cx="3.5" cy="8" r="1.3" /><circle cx="8" cy="8" r="1.3" /><circle cx="12.5" cy="8" r="1.3" />
              </svg>
            </button>
            {menuOpen ? (
              <div role="menu" className="absolute right-0 top-9 z-20 w-56 rounded-lg border border-line bg-canvas-raised p-1 shadow-lg">
                <button type="button" role="menuitem" className={menuItem} disabled={busy !== null} onClick={() => void snooze()}>
                  {busy === "snooze" ? "Guardando…" : "Recordar en 1 semana"}
                </button>
                {f.task ? (
                  <button type="button" role="menuitem" className={menuItem} disabled={busy !== null} onClick={() => void markDone()}>
                    {busy === "done" ? "Guardando…" : "Ya le escribí"}
                  </button>
                ) : null}
                <button type="button" role="menuitem" className={`${menuItem} text-bad`} disabled={busy !== null}
                  onClick={() => { setMenuOpen(false); onClose(f.card); }}>
                  Cerrar sin respuesta
                </button>
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </li>
  );
}

/* ────────────────────────────────────────────────── Forward row ── */

function ForwardQueueRow({
  item, index, navigate, mayDecide, onDone,
}: {
  item: ForwardedRequest;
  index: number;
  navigate: Navigate;
  mayDecide: boolean;
  onDone: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(item.institution ? "" : (item.customer.company ?? ""));
  const [busy, setBusy] = useState<null | "create" | "dismiss">(null);
  const [createKey] = useState(newCaseCommandKey);
  const [dismissKey] = useState(newCaseCommandKey);
  const c = item.customer;
  const who = c.name ?? c.email ?? "Cliente sin nombre";
  const via = item.relay === "marketplace" ? "marketplace" : item.origins.includes("labdelivery") ? "otro correo" : "reenvío";
  const waitLabel = item.received_at ? replyWaitLabel(item.received_at, new Date()) : null;

  async function create() {
    setBusy("create");
    try {
      const receipt = await createRequestFromForward(
        { forwarded_request_id: item.id, note: "Solicitud reenviada a contacto@",
          ...(item.institution
            ? { organization_id: item.institution.id, organization_version: item.institution.version }
            : name.trim() ? { new_organization_name: name.trim() } : {}) },
        createKey,
      );
      toast("Solicitud creada.");
      onDone(item.id);
      navigate("oportunidades", String(receipt.opportunity_id));
    } catch (err) {
      toast(caseRefusalText(err), "bad");
    } finally { setBusy(null); }
  }

  async function dismiss() {
    setBusy("dismiss");
    try {
      await dismissForwardedRequest({ forwarded_request_id: item.id, note: "No es una solicitud" }, dismissKey);
      toast("Descartado.");
      onDone(item.id);
    } catch (err) {
      toast(caseRefusalText(err), "bad");
    } finally { setBusy(null); }
  }

  return (
    <li
      className={`crm-rise flex flex-wrap items-start gap-x-3 gap-y-1.5 px-4 py-2 transition-colors hover:bg-canvas-sunken/30`}
      style={{ "--i": Math.min(index, 10) } as CSSProperties}
      data-testid={`today-forward-${item.id}`}
    >
      <div className="min-w-[12rem] flex-1 space-y-1.5">
        <KindLabel kind="forward" />
        <div>
          <p className="text-[13px] font-medium text-ink">
            {who}{c.company ? <span className="font-normal text-ink-muted"> · {c.company}</span> : null}
          </p>
          <p className="mt-0.5 text-[11px] text-ink-muted">
            {item.requested_item ? <>Pide: <span className="text-ink">{item.requested_item}</span> · </> : null}
            {waitLabel ? `${waitLabel} · ` : ""}vía {via}
            {c.email && c.name ? ` · ${c.email}` : ""}
          </p>
        </div>
        {open && mayDecide ? (
          <div className="space-y-2 rounded-md border border-line bg-canvas-sunken/50 p-3">
            {item.institution ? (
              <p className="text-xs text-ink">
                Institución: <span className="font-medium">{item.institution.name}</span>{" "}
                <span className="text-ink-faint">(ya está en el CRM)</span>
              </p>
            ) : (
              <FormField label="Institución" htmlFor={`fw-${item.id}`} hint="Se crea «por confirmar» si no existe. Déjalo vacío si es una persona sin empresa.">
                <TextInput id={`fw-${item.id}`} value={name} placeholder="Nombre de la institución" onChange={setName} />
              </FormField>
            )}
            <p className="text-[11px] text-ink-muted">
              Se guarda en una nota: {[c.name, c.email, c.phone, c.rut ? `RUT ${c.rut}` : null].filter(Boolean).join(" · ") || "—"}
            </p>
            <Button variant="primary" onClick={() => void create()} busy={busy === "create"} busyLabel="Creando…" disabled={busy !== null}>
              Crear
            </Button>
          </div>
        ) : null}
      </div>
      <div className="flex shrink-0 flex-wrap gap-2">
        {item.gmail_url ? (
          <a href={inSharedMailbox(item.gmail_url) ?? item.gmail_url} target="_blank" rel="noopener noreferrer"
            className="inline-flex h-8 items-center rounded-md border border-line bg-canvas-raised px-3 text-xs font-medium text-ink hover:border-line-strong">
            Abrir correo ↗
          </a>
        ) : null}
        {mayDecide ? (
          <>
            <Button variant="secondary" onClick={() => void dismiss()} busy={busy === "dismiss"} busyLabel="Descartando…" disabled={busy !== null}>
              Descartar
            </Button>
            <Button variant={open ? "quiet" : "primary"} aria-expanded={open} onClick={() => setOpen(!open)} disabled={busy !== null}>
              {open ? "Cerrar" : "Crear solicitud"}
            </Button>
          </>
        ) : null}
      </div>
    </li>
  );
}

/* ──────────────────────────────────────────── System summary line ── */

/**
 * «Mientras no estabas: N solicitudes abiertas · M correos archivados en su caso» — one compact
 * line with a «Revisar» toggle that expands the full `AutomaticActionsPanel` inline.
 * Hidden when there is nothing to report.
 */
function SystemSummaryLine({ navigate, onChanged }: { navigate: Navigate; onChanged: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const [state, reload] = useResource(
    useCallback(() => fetchJsonGet<{ items: AutomaticAction[] }>(operatorApiUrl("/v2/workspace/automatic-actions")), []),
  );

  if (state.kind !== "ready" || !Array.isArray(state.data?.items)) return null;
  const rows = newsRows(state.data.items);
  if (rows.length === 0) return null;

  const opened = rows.filter((r) => r.items[0].kind === "opened").length;
  const filed = rows.reduce((n, r) => n + (r.items[0].kind === "filed" ? r.items.length : 0), 0);
  const summary = [
    opened ? `${opened} ${opened === 1 ? "solicitud abierta" : "solicitudes abiertas"}` : null,
    filed ? `${filed} ${filed === 1 ? "correo archivado en su caso" : "correos archivados en su caso"}` : null,
  ].filter(Boolean).join(" · ");

  return (
    <div className="rounded-xl border border-line bg-canvas-raised" data-testid="today-system-summary">
      <div className="flex flex-wrap items-center gap-2 px-4 py-2.5">
        <p className="flex-1 text-[13px] text-ink-muted">
          <span className="font-medium text-ink">Mientras no estabas:</span>{" "}
          <span className="text-ink">{summary}</span>
          <span className="text-ink-faint"> en los últimos dos días.</span>
        </p>
        <Button
          variant="secondary"
          aria-expanded={expanded}
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? "Ocultar" : "Revisar"}
        </Button>
      </div>
      {expanded ? (
        <div className="border-t border-line px-4 pb-4 pt-2">
          <AutomaticActionsPanel navigate={navigate} onChanged={() => { onChanged(); reload(); }} />
        </div>
      ) : null}
    </div>
  );
}

/* ─────────────────────────────────────────────────────── Empty state ── */

/**
 * «Todo al día» — shown when «Para ti» is empty. Shows the next 3 follow-ups as a quiet preview
 * so the operator knows what is coming next.
 */
function EmptyState({ followUps, navigate }: { followUps: FollowUp[]; navigate: Navigate }) {
  return (
    <section className="rounded-xl border border-line bg-canvas-raised px-4 py-6 text-center" data-testid="today-empty">
      <p className="text-[22px] font-semibold text-good">Todo al día</p>
      <p className="mt-1 text-[13px] text-ink-muted">No hay nada que hacer ahora mismo.</p>
      {followUps.length > 0 ? (
        <div className="mt-4 text-left">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-faint">Próximos seguimientos</p>
          <ul className="mt-1 space-y-0.5">
            {followUps.map((f) => {
              const quote = f.card.latest_revision?.quote_number ?? f.card.quote_numbers[0] ?? "";
              const name = f.card.organization?.name ?? f.card.title;
              return (
                <li key={f.card.opportunity_id} className="flex items-center gap-2 text-[12px]">
                  <button
                    type="button"
                    onClick={() => navigate("oportunidades", f.card.opportunity_id)}
                    className="flex-1 truncate text-left text-ink hover:text-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
                  >
                    {name}
                    {quote ? <span className="ml-1 text-ink-faint">{quote}</span> : null}
                  </button>
                  <span className="shrink-0 tabular-nums text-ink-faint">{f.days} días</span>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
