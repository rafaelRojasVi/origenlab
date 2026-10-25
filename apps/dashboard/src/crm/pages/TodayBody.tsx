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
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { addNote, fetchPersonSuggestions, refusalOf } from "../authoring/crmAuthoringApi";
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
  useMayRunCaseCommands,
} from "../caseCommands";
import { contactLine, displayName, quoteProduct } from "../caseDisplay";
import { composeInSharedMailbox, inSharedMailbox } from "../gmailLinks";
import { isMaskedAddress } from "../redaction";
import { useAuthSession } from "../../context/AuthSessionContext";
import { crmHash, type CrmSection, type DatosTab } from "../crmRoute";
import type { OpportunityCardData } from "../crmTypes";
import { quoteToOpen, useOpenOrCreateQuote } from "../quotes/openOrCreateQuote";
import {
  buildParaTiQueue,
  followUpsDue,
  historicalCount,
  organizationsToConfirm,
  tenderClosingLines,
  wroteAfterWinning,
  type CotizarQueueItem,
  type DueTask,
  type FollowUp,
  type FollowUpQueueItem,
  type OverdueTask,
  type QueueItem,
  type ReplyQueueItem,
  type TaskQueueItem,
} from "../today";
import { fetchJsonGet, operatorApiUrl } from "../../api/operatorClient";
import { Badge, Button, FormField, Modal, TextInput, fmtDate, toast } from "../ui";
import type { ResourceState } from "../useResource";
import { useResource } from "../useResource";
import { useLeave } from "../useLeave";
import { CaseMoveForm } from "./CaseMove";
import { AutomaticActionsPanel, newsRows, type AutomaticAction } from "./AutomaticActionsPanel";
import type { ForwardedRequest } from "./ForwardedRequestsPanel";

type Navigate = (s: CrmSection, id?: string | null, tab?: DatosTab) => void;

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
  people,
  tendersClosing,
  aside,
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
  /** Person suggestions, read by the page beside the pipeline so neither waits on the other. */
  people?: ResourceState<Awaited<ReturnType<typeof fetchPersonSuggestions>>>;
  /** `tenders_closing` from the pipeline: the tenders that close tomorrow (a tender is in no other list). */
  tendersClosing?: { id: string; title: string }[];
  /** The main column's last panel: the quote number and the rates. */
  aside?: ReactNode;
}) {
  const at = useMemo(() => now ?? new Date(), [now]);
  const { session } = useAuthSession();
  const me = session.kind === "signed_in" ? (session.profile?.displayName ?? session.operator.displayName ?? null) : null;

  const queue = useMemo(() => buildParaTiQueue(items, at, me), [items, at, me]);

  // Forwarded requests — fetched separately, appended to the end of the queue.
  const [fwdState, reloadFwd] = useResource(useCallback(() =>
    fetchJsonGet<{ items: ForwardedRequest[] }>(operatorApiUrl("/v2/workspace/forwarded-requests")), []));
  const [fwdGone, setFwdGone] = useState<Set<string>>(new Set());
  const forwards: ForwardedRequest[] = fwdState.kind === "ready" && Array.isArray(fwdState.data?.items)
    ? fwdState.data.items.filter((f) => !fwdGone.has(f.id))
    : [];
  // The header's «N para ti» counts them too, so it matches the list.
  useEffect(() => { onForwardCount?.(forwards.length); }, [forwards.length, onForwardCount]);

  // Housekeeping counts (local — no extra fetch needed).
  const orgs = useMemo(() => organizationsToConfirm(items), [items]);
  const historical = historicalCount(items, at);
  const blocked = useMemo(() => items.filter((i) => i.status === "blocked" && !queue.some((q) => q.opportunity_id === i.opportunity_id)), [items, queue]);
  const peopleCount = people?.kind === "ready" ? people.data.total : 0;
  const housekeepingCount = orgs.length + historical + blocked.length + peopleCount;

  const mayDecide = useMayRunCaseCommands();
  const mayAuthor = useMayAuthorCrm();
  const [closing, setClosing] = useState<OpportunityCardData | null>(null);

  // «Gone» IDs — items that left the queue in this render session (leave animation).
  const [goneIds, setGoneIds] = useState<Set<string>>(new Set());
  const clearItem = useCallback((id: string) => {
    setGoneIds((s) => new Set([...s, id]));
    onCleared?.();
  }, [onCleared]);

  const tenderLines = useMemo(() => tenderClosingLines(tendersClosing), [tendersClosing]);
  const totalQueue = queue.length + forwards.length + tenderLines.length;
  const nextFollowUps = useMemo(() => followUpsDue(items, at).slice(0, 3), [items, at]);

  return (
    <div className="space-y-4" aria-busy={refreshing || undefined}>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_21rem]">
        <div className="min-w-0 space-y-4">

          {/* ─── «Para ti» queue ─── */}
          <ParaTiSection
            queue={queue}
            forwards={forwards}
            goneIds={goneIds}
            navigate={navigate}
            mayDecide={mayDecide}
            mayAuthor={mayAuthor}
            onChanged={() => { onChanged(); reloadFwd(); }}
            onClearItem={clearItem}
            onFwdDone={(id) => { setFwdGone((g) => new Set([...g, id])); onChanged(); reloadFwd(); }}
            onClose={setClosing}
            now={at}
            me={me}
          />

          {/* ─── «Licitación cierra mañana» — Hoy's one tender signal ─── */}
          {tenderLines.length > 0 ? <TenderClosingSection lines={tenderLines} navigate={navigate} /> : null}

          {/* ─── Empty state ─── */}
          {totalQueue === 0 && (
            <EmptyState followUps={nextFollowUps} navigate={navigate} />
          )}

          {/* ─── «Hecho automáticamente» — one collapsible line ─── */}
          <SystemSummaryLine navigate={navigate} onChanged={onChanged} />

          {/* ─── Housekeeping link → Operaciones ─── */}
          {housekeepingCount > 0 ? (
            <HousekeepingLink count={housekeepingCount} navigate={navigate} />
          ) : null}

        </div>
        {/* Tools at hand: the next quote number and the day's exchange rates. */}
        <aside className="min-w-0 space-y-4 lg:sticky lg:top-4 lg:self-start">{aside}</aside>
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
    </div>
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

const CHIP: Record<string, { label: string; cls: string }> = {
  reply:   { label: "Responder",  cls: "bg-warn-bg text-warn" },
  task:    { label: "Tarea",      cls: "bg-bad-bg text-bad" },
  followup:{ label: "Seguimiento",cls: "bg-good-bg text-good" },
  forward: { label: "Decidir",    cls: "bg-info-bg text-info" },
};

function ChipBadge({ kind }: { kind: keyof typeof CHIP }) {
  const { label, cls } = CHIP[kind] ?? CHIP.reply;
  return (
    <span className={`inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-[11px] font-semibold ${cls}`}>
      {label}
    </span>
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

/* ──────────────────────────────────────── section container ── */

function QueueSection({ title, hint, count, children }: {
  title: string;
  hint?: string;
  count: number;
  children: ReactNode;
}) {
  return (
    <section className="overflow-hidden rounded-xl border border-line bg-canvas-raised">
      <header className="flex flex-wrap items-baseline gap-x-2 gap-y-1 px-4 pb-1 pt-3">
        <h2 className="text-[14px] font-semibold text-ink">{title}</h2>
        <span className="text-[12px] tabular-nums text-ink-muted">{count}</span>
        {hint ? <span className="ml-1 text-[11.5px] text-ink-faint">{hint}</span> : null}
      </header>
      <ul className="divide-y divide-line">{children}</ul>
    </section>
  );
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
  const toCotizador = useCallback((quoteId: string) => navigate("cotizador", quoteId), [navigate]);
  const quote = useOpenOrCreateQuote(toCotizador);

  return (
    <li
      className="crm-rise group flex items-center gap-3 px-4 py-2.5 transition-colors hover:bg-canvas-sunken/30"
      style={{ "--i": Math.min(index, 10) } as CSSProperties}
      data-testid={`today-cotizar-${card.opportunity_id}`}
    >
      <DayPill ms={item.waitMs} />
      <button
        type="button"
        onClick={() => navigate("oportunidades", card.opportunity_id)}
        className="min-w-0 flex-1 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
      >
        <p className="truncate text-[14px] font-semibold text-ink group-hover:text-accent">{name}</p>
        <p className="truncate text-[12px] text-ink-muted">{what}</p>
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
 * Three sections (Cotizar / Responder / Seguimientos de hoy), then tasks and forwarded requests.
 * Each section has a heading, hint and count; rows are oldest-wait-first within every section.
 * A `data-testid="today-para-ti"` wrapper is kept so existing tests can still find the area.
 */
function ParaTiSection({
  queue, forwards, goneIds, navigate, mayDecide, mayAuthor, onChanged, onClearItem, onFwdDone, onClose, now,
}: {
  queue: QueueItem[];
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
}) {
  if (queue.length === 0 && forwards.length === 0) return null;

  const cotizarItems = queue.filter((q): q is CotizarQueueItem => q.kind === "cotizar" && !goneIds.has(q.opportunity_id));
  const replyItems   = queue.filter((q): q is ReplyQueueItem   => q.kind === "reply"   && !goneIds.has(q.opportunity_id));
  const followItems  = queue.filter((q): q is FollowUpQueueItem => q.kind === "followup" && !goneIds.has(q.opportunity_id));
  const taskItems    = queue.filter((q): q is TaskQueueItem    => q.kind === "task"    && !goneIds.has(q.opportunity_id));
  const liveForwards = forwards.filter(() => true); // already filtered by goneIds outside

  return (
    <div data-testid="today-para-ti" className="space-y-4">
      {/* ─── Cotizar ─── */}
      {cotizarItems.length > 0 && (
        <QueueSection
          title="Cotizar"
          hint="pidieron precio y aún no tienen cotización · la más antigua arriba"
          count={cotizarItems.length}
        >
          {cotizarItems.map((item, i) => (
            <CotizarQueueRow
              key={item.opportunity_id}
              item={item}
              index={i}
              navigate={navigate}
              mayDecide={mayDecide}
              onChanged={onChanged}
              onClearItem={() => onClearItem(item.opportunity_id)}
            />
          ))}
        </QueueSection>
      )}

      {/* ─── Responder ─── */}
      {replyItems.length > 0 && (
        <QueueSection
          title="Responder"
          hint="el cliente escribió y espera"
          count={replyItems.length}
        >
          {replyItems.map((item, i) => (
            <ReplyQueueRow
              key={item.opportunity_id}
              reply={item.reply}
              waitMs={item.waitMs}
              index={i}
              navigate={navigate}
              mayDecide={mayDecide}
              mayAuthor={mayAuthor}
              onChanged={onChanged}
              onClearItem={() => onClearItem(item.opportunity_id)}
              now={now}
            />
          ))}
        </QueueSection>
      )}

      {/* ─── Seguimientos de hoy ─── */}
      {followItems.length > 0 && (
        <QueueSection
          title="Seguimientos de hoy"
          hint="cotización enviada, sin respuesta · ritmo 3 · 14 · 30 días"
          count={followItems.length}
        >
          {followItems.map((item, i) => (
            <FollowUpQueueRow
              key={item.opportunity_id}
              followUp={item.followUp}
              waitMs={item.waitMs}
              index={i}
              navigate={navigate}
              mayDecide={mayDecide}
              onChanged={onChanged}
              onClearItem={() => onClearItem(item.opportunity_id)}
              onClose={onClose}
              now={now}
            />
          ))}
        </QueueSection>
      )}

      {/* ─── Tareas ─── */}
      {taskItems.length > 0 && (
        <QueueSection title="Tareas" count={taskItems.length}>
          {taskItems.map((item, i) => (
            <TaskQueueRow
              key={item.opportunity_id}
              due={item.due}
              index={i}
              navigate={navigate}
              mayDecide={mayDecide}
              onChanged={onChanged}
              onClearItem={() => onClearItem(item.opportunity_id)}
              now={now}
            />
          ))}
        </QueueSection>
      )}

      {/* ─── Decidir (forwarded requests) ─── */}
      {liveForwards.length > 0 && (
        <QueueSection title="Decidir" count={liveForwards.length}>
          {liveForwards.map((f, i) => (
            <ForwardQueueRow
              key={f.id}
              item={f}
              index={i}
              navigate={navigate}
              mayDecide={mayDecide}
              onDone={onFwdDone}
            />
          ))}
        </QueueSection>
      )}
    </div>
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
  const { card } = reply;
  const { leaving, gone, leave } = useLeave(reply);
  const [busy, setBusy] = useState<null | "move" | "skip" | "handled">(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const keys = useRef([newCaseCommandKey()]);
  const skipKeys = useRef<string[]>([]);
  const handledKey = useRef(newCaseCommandKey());
  const menuRef = useRef<HTMLDivElement>(null);
  const blocking = card.attention.find((a) => a.blocking) ?? null;
  const won = wroteAfterWinning(card);
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
      leave(); onClearItem(); onChanged();
    } catch (err) {
      skipKeys.current = [];
      toast(caseRefusalText(err), "bad");
    } finally { setBusy(null); }
  }

  async function handled() {
    setBusy("handled");
    try {
      await addNote(
        { subject_kind: "opportunity", subject_id: card.opportunity_id, body: `Atendido: el cliente escribió el ${fmtDate(reply.at)} tras ganar el caso; respondido por otro medio o sin respuesta pendiente.` },
        handledKey.current,
      );
      toast("Listo. Queda anotado en el caso.");
      leave(); onClearItem(); onChanged();
    } catch (err) {
      handledKey.current = newCaseCommandKey();
      toast(refusalOf(err)?.message ?? "No se pudo anotar el caso.", "bad");
    } finally { setBusy(null); }
  }

  async function toConversation() {
    setBusy("move");
    try {
      await moveCase(
        { opportunity_id: card.opportunity_id, stage: card.stage, version: card.version as number },
        "negotiating",
        `El cliente respondió el ${fmtDate(reply.at)}.`,
        null, keys.current,
      );
      toast("Pasó a «Conversación».");
      leave(); onClearItem(); onChanged();
    } catch (err) {
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
      className={`crm-rise group relative flex items-center gap-3 px-4 py-2.5 transition-colors hover:bg-canvas-sunken/30 ${menuOpen ? "z-20" : ""} ${leaving ? "crm-row-out" : ""}`}
      style={{ "--i": Math.min(index, 10) } as CSSProperties}
      data-testid={`today-reply-${card.opportunity_id}`}
    >
      <DayPill ms={waitMs} />
      <div className="min-w-0 flex-1">
        <button
          type="button"
          onClick={() => navigate("oportunidades", card.opportunity_id)}
          className="block w-full min-w-0 truncate text-left text-[14px] font-semibold text-ink hover:text-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
        >
          {name}
        </button>
        <p className="truncate text-[12px] text-ink-muted">{what}</p>
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
          <Button variant="primary" onClick={() => navigate("oportunidades", card.opportunity_id)}>Resolver</Button>
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
  due, index, navigate, mayDecide, onChanged, onClearItem, now,
}: {
  due: DueTask | OverdueTask;
  index: number;
  navigate: Navigate;
  mayDecide: boolean;
  onChanged: () => void;
  onClearItem: () => void;
  now: Date;
}) {
  const { card, task, overdueDays } = due;
  const { leaving, gone, leave } = useLeave(due);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const [busy, setBusy] = useState<null | "done" | "snooze">(null);
  const keys = useRef({ done: newCaseCommandKey(), create: newCaseCommandKey(), cancel: newCaseCommandKey() });
  // "escalated" is only on OverdueTask
  const escalated = "escalated" in due ? (due as OverdueTask).escalated : false;

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
    try {
      await completeTask({ task_id: task.task_id, task_version: task.version, note: "Hecho desde «Hoy»." }, keys.current.done);
      toast("Tarea hecha.");
      leave(); onClearItem(); onChanged();
    } catch (err) {
      keys.current.done = newCaseCommandKey();
      toast(caseRefusalText(err), "bad");
    } finally { setBusy(null); }
  }

  async function snooze() {
    setBusy("snooze");
    const next = new Date(Math.max(now.getTime(), Date.parse(task.due_at)) + WEEK_MS);
    try {
      await createTask(
        { opportunity_id: card.opportunity_id, title: task.title, due_at: next.toISOString(), note: "Pospuesta una semana desde «Hoy»." },
        keys.current.create,
      );
      await cancelTask({ task_id: task.task_id, task_version: task.version, note: `Pospuesta al ${fmtDate(next.toISOString())}.` }, keys.current.cancel);
      toast(`Pospuesta al ${fmtDate(next.toISOString())}.`);
      leave(); onClearItem(); onChanged();
    } catch (err) {
      keys.current = { done: keys.current.done, create: newCaseCommandKey(), cancel: newCaseCommandKey() };
      toast(caseRefusalText(err), "bad");
      onChanged();
    } finally { setBusy(null); }
  }

  if (gone) return null;
  const menuItem = "flex w-full items-center rounded px-2.5 py-1.5 text-left text-[12.5px] text-ink hover:bg-canvas-sunken focus:bg-canvas-sunken focus:outline-none disabled:opacity-50";
  return (
    <li
      className={`crm-rise group relative flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-2.5 transition-colors hover:bg-canvas-sunken/30 ${menuOpen ? "z-20" : ""} ${leaving ? "crm-row-out" : ""}`}
      style={{ "--i": Math.min(index, 10) } as CSSProperties}
      data-testid={`today-task-${task.task_id}`}
    >
      <ChipBadge kind="task" />
      <div className="min-w-[12rem] flex-1">
        <p className="text-[13px] text-ink">{task.title}</p>
        <p className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-[11px] text-ink-muted">
          <span className={overdueDays > 0 ? "font-medium text-bad" : "font-medium text-warn"}>{dueText(overdueDays)}</span>
          {escalated && task.owner ? (
            <><span aria-hidden="true">·</span><span data-testid="today-owner-label">de {task.owner}</span></>
          ) : null}
          <span aria-hidden="true">·</span>
          <CaseLink card={card} navigate={navigate} />
        </p>
      </div>
      <div className="flex shrink-0 gap-2">
        {/* Primary: open the case */}
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
  const f = followUp;
  const { leaving, gone, leave } = useLeave(f);
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
    try {
      await completeTask({ task_id: f.task.task.task_id, task_version: f.task.task.version, note: "Hecho desde «Hoy»." }, keys.current.done);
      toast("Seguimiento hecho.");
      leave(); onClearItem(); setMenuOpen(false); onChanged();
    } catch (err) {
      keys.current.done = newCaseCommandKey();
      toast(caseRefusalText(err), "bad");
    } finally { setBusy(null); }
  }

  async function snooze() {
    setBusy("snooze");
    const base = f.task ? Math.max(now.getTime(), Date.parse(f.task.task.due_at)) : now.getTime();
    const next = new Date(base + WEEK_MS);
    try {
      await createTask(
        { opportunity_id: f.card.opportunity_id, title: f.task?.task.title ?? `Seguimiento de ${quote}`.trim(), due_at: next.toISOString(), note: "Pospuesta una semana desde «Hoy»." },
        keys.current.create,
      );
      if (f.task) {
        await cancelTask({ task_id: f.task.task.task_id, task_version: f.task.task.version, note: `Pospuesta al ${fmtDate(next.toISOString())}.` }, keys.current.cancel);
      }
      toast(`Te lo recuerdo el ${fmtDate(next.toISOString())}.`);
      leave(); onClearItem(); setMenuOpen(false); onChanged();
    } catch (err) {
      keys.current = { done: keys.current.done, create: newCaseCommandKey(), cancel: newCaseCommandKey() };
      toast(caseRefusalText(err), "bad");
      onChanged();
    } finally { setBusy(null); }
  }

  if (gone) return null;
  const menuItem = "flex w-full items-center rounded px-2.5 py-1.5 text-left text-[12.5px] text-ink hover:bg-canvas-sunken focus:bg-canvas-sunken focus:outline-none disabled:opacity-50";
  const { name } = displayName(f.card);
  const what = [
    ...(product ? [product] : []),
    ...(quote ? [quote] : []),
    ...(contactLine(f.card) ? [contactLine(f.card)!] : []),
  ].join(" · ");
  return (
    <li
      className={`crm-rise group relative flex items-center gap-3 px-4 py-2.5 transition-colors hover:bg-canvas-sunken/30 ${menuOpen ? "z-20" : ""} ${leaving ? "crm-row-out" : ""}`}
      style={{ "--i": Math.min(index, 10) } as CSSProperties}
      data-testid={f.task ? `today-task-${f.task.task.task_id}` : `today-followup-${f.card.opportunity_id}`}
    >
      <DayPill ms={waitMs} />
      <div className="min-w-0 flex-1">
        <button
          type="button"
          onClick={() => navigate("oportunidades", f.card.opportunity_id)}
          className="block w-full min-w-0 truncate text-left text-[14px] font-semibold text-ink hover:text-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
        >
          {name}
        </button>
        <p className="truncate text-[12px] text-ink-muted">{what}</p>
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
        {/* Primary: «Escribir» — open the Gmail thread */}
        <a
          href={write}
          target="_blank"
          rel="noopener noreferrer"
          title={thread ? "Abre el hilo de la cotización en Gmail" : "Abre un correo nuevo en Gmail"}
          aria-label={thread ? "Escribir en Gmail" : "Nuevo correo en Gmail"}
          className="inline-flex h-8 items-center gap-1.5 rounded-md bg-brand-600 px-3 text-xs font-semibold text-white shadow-sm hover:bg-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 focus-visible:ring-offset-1"
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
      className={`crm-rise flex flex-wrap items-start gap-x-3 gap-y-1.5 px-4 py-2.5 transition-colors hover:bg-canvas-sunken/30`}
      style={{ "--i": Math.min(index, 10) } as CSSProperties}
      data-testid={`today-forward-${item.id}`}
    >
      <ChipBadge kind="forward" />
      <div className="min-w-[12rem] flex-1 space-y-1.5">
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

/* ──────────────────────────────────────────────────── Housekeeping link ── */

/**
 * «N cosas por ordenar → Operaciones»: one line linking to the «Por decidir» area of
 * OperacionesPage, which holds TriagePanel, OrgsPanel, FollowUpsPanel, etc.
 */
function HousekeepingLink({ count, navigate }: { count: number; navigate: Navigate }) {
  return (
    <div className="rounded-xl border border-line bg-canvas-raised px-4 py-2.5" data-testid="today-housekeeping">
      <button
        type="button"
        onClick={() => navigate("datos", null, "decidir")}
        className="flex w-full items-center gap-2 text-left text-[13px] text-ink hover:text-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
      >
        <Badge tone="neutral" glyph={false}>{count}</Badge>
        <span>
          {count === 1 ? "cosa" : "cosas"} por ordenar
          <span className="ml-1 font-medium text-accent">→ Operaciones</span>
        </span>
      </button>
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
