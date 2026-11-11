/**
 * The one case drawer (Cajón): a header that says what the case is and where it stands, the
 * everyday actions in front («Registrar llamada», «Responder en Gmail», «Recordarme…»), five tabs
 * — Resumen · Cotización · Contacto · Notas y llamadas · Correos — and a footer that pushes the
 * next step («Marcar ganada» / «Marcar perdida»). Every tab stays mounted while the drawer is
 * open, so a half-written note survives a tab change; only the selected one is shown.
 */
import { useCallback, useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { NoteList } from "../authoring/NoteList";
import { OriginSelect } from "../authoring/OriginSelect";
import { useMayAuthorCrm } from "../authoring/authoring";
import { isReopenable, mayCarryAQuote, useMayRunCaseCommands, winnableRevisions } from "../caseCommands";
import { quoteProduct, systemNotes } from "../caseDisplay";
import { fetchCaseNotes } from "../crmApi";
import { crmHash } from "../crmRoute";
import type { OpportunityCardData } from "../crmTypes";
import { inSharedMailbox } from "../gmailLinks";
import { formatMoney } from "../money";
import { CaseActions, hoistedActions, type Mode } from "../pages/CaseActions";
import { CaseDocumentsBlock } from "../pages/CaseDocuments";
import { CaseMemory } from "../pages/CaseMemory";
import type { MoveTarget } from "../pages/CaseMove";
import { CaseLinkIcons, CaseMoneySummary, QuoteList } from "../pages/CaseQuotes";
import { CaseThreads } from "../pages/CaseThreads";
import { CompleteFromMail } from "../pages/CompleteFromMail";
import { quoteToOpen, useMayOpenCotizador, useOpenOrCreateQuote } from "../quotes/openOrCreateQuote";
import { isMaskedAddress } from "../redaction";
import { STATUS_LABEL } from "../stage";
import { endOfDay, followUpStep, followUpsDue, pendingFollowUpTasks } from "../today";
import { Badge, Button, Drawer, Section, fmtDate } from "../ui";
import { useResource } from "../useResource";
import { CaseTimeline } from "./CaseTimeline";
import { PersonPhones } from "./PersonPhones";
import { RevisionHistory } from "./RevisionHistory";
import { QueueIcon, type IconName } from "../pages/HoyQueueParts";

export type DrawerTab = "resumen" | "cotizacion" | "contacto" | "notas" | "correos";

const TABS: { key: DrawerTab; label: string }[] = [
  { key: "resumen", label: "Resumen" },
  { key: "cotizacion", label: "Cotización" },
  { key: "contacto", label: "Contacto" },
  { key: "notas", label: "Notas y llamadas" },
  { key: "correos", label: "Correos" },
];

type FormRequest = { mode: Exclude<Mode, null>; target?: MoveTarget } | null;

/** «mié 11 nov»: a day near enough to need no year. */
function shortDay(value: string | Date): string {
  const d = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString("es-CL", { weekday: "short", day: "numeric", month: "short" });
}

/** «miércoles 11»: the canvas's day for a follow-up chip, in the browser's zone. */
function weekdayDay(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return `${d.toLocaleDateString("es-CL", { weekday: "long" })} ${d.getDate()}`;
}

/**
 * «Seguimiento N de 3 · …» (steps 1–3) or «Último correo · …» (step 4) for the header: with a
 * «Seguimiento …» reminder still pending (one an email of ours already met is done:
 * `pendingFollowUpTasks`), its step and its day — «hoy» when it is due today or overdue, else its
 * weekday; the step is the one the rhythm will be on that day, read from `followUpsDue` as of
 * then. Otherwise, when the case is due today by the rhythm alone, «hoy». Null when neither.
 */
function followUpChip(card: OpportunityCardData, now: Date): { step: 1 | 2 | 3 | 4; day: string } | null {
  const reminder = pendingFollowUpTasks(card)[0] ?? null;
  const dueNow = followUpsDue([card], now)[0] ?? null;
  if (reminder) {
    const due = Date.parse(reminder.due_at);
    const at = Math.max(now.getTime(), due);
    const onItsDay = dueNow ?? followUpsDue([card], new Date(Number.isNaN(at) ? now.getTime() : at))[0] ?? null;
    const today = !Number.isNaN(due) && due <= endOfDay(now).getTime();
    return { step: onItsDay ? followUpStep(onItsDay) : 1, day: today ? "hoy" : weekdayDay(reminder.due_at) };
  }
  return dueNow ? { step: followUpStep(dueNow), day: "hoy" } : null;
}

/** «Ana Ficticia <ana@…>» → «Ana Ficticia»; a bare address stays as it is. */
function displayNameOf(address: string): string {
  // The display part is whatever precedes the «<»; React renders it as text, nothing is sanitised here.
  const cut = address.indexOf("<");
  const name = (cut >= 0 ? address.slice(0, cut) : "").trim();
  const unquoted = name.startsWith('"') && name.endsWith('"') && name.length > 1 ? name.slice(1, -1).trim() : name;
  return unquoted || address;
}

/** Gmail's search for one address, in the shared contacto@ mailbox. */
function gmailSearchUrl(address: string): string {
  const bare = (address.match(/<([^>]+)>/)?.[1] ?? address).trim();
  return `https://mail.google.com/mail/?authuser=${encodeURIComponent("contacto@origenlab.cl")}#search/${encodeURIComponent(bare)}`;
}

/** «Responder en Gmail»: the client's last email, else a Gmail search for their address. */
function replyUrl(card: OpportunityCardData): string | null {
  const inbound = card.last_contact?.inbound?.url;
  if (inbound) return inSharedMailbox(inbound) ?? inbound;
  const address = card.contact?.address;
  return address && !isMaskedAddress(address) ? gmailSearchUrl(address) : null;
}

export function CaseDrawer({
  card,
  onClose,
  onChanged,
  refreshing = false,
  initial = null,
}: {
  card: OpportunityCardData | null;
  onClose: () => void;
  onChanged: () => void;
  refreshing?: boolean;
  initial?: FormRequest;
}) {
  if (!card) return null;
  // A new case starts on «Resumen» with nothing open.
  return <CaseDrawerBody key={card.opportunity_id} card={card} onClose={onClose} onChanged={onChanged} refreshing={refreshing} initial={initial} />;
}

/**
 * The same case, inline (owner 2026-11-11, Hoy's detail pane): the drawer's tabs and forms without
 * the drawer, and the decisions as a strip of labelled icons instead of the header and footer buttons.
 */
export function CaseInline({ card, onChanged }: { card: OpportunityCardData; onChanged: () => void }) {
  return <CaseDrawerBody key={card.opportunity_id} card={card} onClose={() => {}} onChanged={onChanged} refreshing={false} initial={null} embedded />;
}

function CaseDrawerBody({
  card,
  onClose,
  onChanged,
  refreshing,
  initial,
  embedded = false,
}: {
  card: OpportunityCardData;
  onClose: () => void;
  onChanged: () => void;
  refreshing: boolean;
  initial: FormRequest;
  embedded?: boolean;
}) {
  const mayAuthor = useMayAuthorCrm();
  const mayQuote = useMayRunCaseCommands();
  const mayOpenCotizador = useMayOpenCotizador();
  const lifted = hoistedActions(card, mayQuote, mayAuthor);
  const [tab, setTab] = useState<DrawerTab>("resumen");
  const [moreOpen, setMoreOpen] = useState(false);
  const base = useId();
  // The form the header or footer asked `CaseActions` to open; a new object each time, so asking
  // again reopens it.
  const [request, setRequest] = useState<FormRequest>(initial);
  useEffect(() => {
    if (initial) {
      setRequest(initial);
      setTab("resumen");
    }
  }, [initial]);
  function ask(mode: Exclude<Mode, null>, target?: MoveTarget) {
    setTab("resumen");
    setRequest({ mode, target });
  }

  const status = STATUS_LABEL[card.status];
  const product = quoteProduct(card);
  const money = card.money?.[0];
  const net = money && money.currency !== "unread" ? formatMoney(money.net_total, money.currency) : null;
  const followUp = followUpChip(card, new Date());
  // The Contacto tab reads the person's phones only once it has been opened, then keeps them.
  const [contactSeen, setContactSeen] = useState(false);
  if (tab === "contacto" && !contactSeen) setContactSeen(true);
  const reply = replyUrl(card);
  // The same reminder the chip reads: a pending one only (one our later email already met is done).
  const reminder = pendingFollowUpTasks(card)[0] ?? null;
  const nextStep = reminder
    ? `${reminder.title} · ${shortDay(reminder.due_at)}`
    : card.next_action.source === "task" && card.next_action.due_at
      ? `${card.next_action.text} · ${shortDay(card.next_action.due_at)}`
      : card.next_action.text;

  const decide: { key: string; label: string; icon: IconName; on: boolean; go: () => void }[] = [
    { key: "call", label: "Llamada", icon: "phone", on: lifted.call, go: () => ask("call") },
    { key: "followup", label: "Recordar", icon: "bell", on: lifted.followUp, go: () => ask("followup") },
    { key: "pausa", label: "Pausar", icon: "pause", on: lifted.lost, go: () => ask("stage", "pausa") },
    { key: "won", label: "Ganada", icon: "trophy", on: lifted.won, go: () => ask("won") },
    { key: "lost", label: "Perdida", icon: "x", on: lifted.lost, go: () => ask("stage", "perdida") },
  ];
  // A closed case: its one decision is to reopen it (where it allows), in the strip's place.
  const closedCase = card.closed_at != null || ["won", "lost", "abandoned"].includes(card.stage);
  if (closedCase) {
    decide.splice(2, 3, { key: "reopen", label: "Reabrir", icon: "reply", on: mayQuote && isReopenable(card), go: () => ask("stage") });
  }
  const tile = "relative flex h-[4.25rem] min-w-0 flex-col items-center justify-center gap-1.5 rounded-lg border border-line bg-canvas-raised px-0.5 text-[11.5px] font-semibold leading-none text-ink-muted transition-colors hover:border-line-strong hover:bg-canvas-sunken/40 hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 disabled:cursor-not-allowed disabled:opacity-40 aria-expanded:border-ink aria-expanded:text-ink";
  const iconStrip = (
    <div className="grid grid-cols-[repeat(auto-fit,minmax(4.25rem,1fr))] gap-1.5" role="group" aria-label="Decidir" data-testid="case-inline-decide">
      {reply ? (
        <a href={reply} target="_blank" rel="noopener noreferrer" className={tile} data-testid="case-inline-reply">
          <QueueIcon name="reply" /><span className="whitespace-nowrap tracking-tight">Responder</span>
          <span aria-hidden="true" className="absolute right-1.5 top-1 text-[10px] text-ink-faint">↗</span>
        </a>
      ) : (
        <button type="button" className={tile} disabled title="Sin correo para responder"><QueueIcon name="reply" />Responder</button>
      )}
      {decide.map((d) => (
        <button key={d.key} type="button" className={tile} disabled={!d.on} onClick={d.go} title={d.on ? d.label : `${d.label}: no disponible para este caso`} data-testid={`case-inline-${d.key}`}>
          <QueueIcon name={d.icon} /><span className="whitespace-nowrap tracking-tight">{d.label}</span>
        </button>
      ))}
      <button type="button" className={tile} aria-expanded={moreOpen} onClick={() => setMoreOpen(!moreOpen)} data-testid="case-inline-more-toggle">
        <span className="inline-grid h-7 w-7 place-items-center rounded-lg bg-canvas-sunken text-[15px] font-bold leading-none text-ink-muted" aria-hidden="true">⋯</span><span>Más</span>
      </button>
    </div>
  );

  // Where the case is, as a stepper you can click to move it (Hoy and the drawer alike, owner 2026-11-11).
  const STEPS: { key: string; label: string; stages: string[]; go: () => void }[] = [
    { key: "solicitada", label: "Solicitada", stages: ["lead", "qualifying", "qualified"], go: () => ask("stage", "solicitada") },
    { key: "enviada", label: "Enviada", stages: ["quoting"], go: () => ask("stage", "enviada") },
    { key: "conversacion", label: "Conversación", stages: ["negotiating"], go: () => ask("stage", "conversacion") },
    { key: "ganada", label: "Ganada", stages: ["won"], go: () => ask("won") },
  ];
  const at = STEPS.findIndex((st) => st.stages.includes(card.stage));
  const lostState = card.stage === "lost" ? "Perdida" : card.stage === "abandoned" ? "Cerrada" : null;
  const pausedDay = card.paused_until && !card.closed_at ? card.paused_until : null;
  const stepper = (
    <section className="rounded-lg border border-line bg-canvas-sunken/30 p-2.5" aria-label="Estado del caso" data-testid="case-inline-state">
      <ol className="grid grid-cols-4 gap-1">
        {STEPS.map((st, i) => {
          const done = at >= 0 && i < at;
          const here = i === at && !lostState;
          return (
            <li key={st.key} className="min-w-0">
              <button type="button" onClick={st.go} disabled={here || !lifted.lost && st.key !== "ganada"}
                title={here ? "Estado actual" : `Pasar a ${st.label}`}
                className={`flex h-8 w-full min-w-0 items-center justify-center gap-1.5 rounded-md px-1.5 text-[12px] font-semibold transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 ${
                  here ? "bg-ink text-canvas" : done ? "bg-accent/10 text-accent hover:bg-accent/20" : "bg-canvas-sunken text-ink-muted hover:text-ink"
                } disabled:cursor-default`}>
                <span className={`grid h-4 w-4 shrink-0 place-items-center rounded-full text-[10px] ${here ? "bg-canvas text-ink" : done ? "bg-accent text-white" : "border border-line-strong"}`}>{done ? "✓" : i + 1}</span>
                <span className="truncate">{st.label}</span>
              </button>
            </li>
          );
        })}
      </ol>
      <div className="mt-2 flex flex-wrap items-baseline gap-x-4 gap-y-1 px-1 text-[12.5px]">
        {card.status === "ok" ? null : <Badge tone={status.tone}>{status.label}</Badge>}
        {followUp ? (
          <span className="font-semibold text-warn" data-testid="case-drawer-followup">
            {followUp.step < 4 ? <>Seguimiento {followUp.step} de 3 · {followUp.day}</> : <>Último correo · {followUp.day}</>}
          </span>
        ) : null}
        {lostState ? <span className="font-semibold text-bad">{lostState}{card.close_reason ? ` · ${card.close_reason}` : ""}</span> : null}
        {pausedDay ? <span className="font-semibold text-warn">En pausa hasta {shortDay(pausedDay)}{card.pause_reason ? ` · ${card.pause_reason}` : ""}</span> : null}
        {net ? <span className="font-semibold tabular-nums text-ink">{net} <span className="font-normal text-ink-muted">neto</span></span> : null}
        <span className="min-w-0 text-ink-muted">Siguiente: <strong className="font-semibold text-ink">{nextStep}</strong></span>
      </div>
    </section>
  );
  const moreItems: { label: string; go: () => void; show: boolean }[] = [
    { label: "Cambiar estado", go: () => ask("stage"), show: lifted.lost },
    // Only where the command takes them: a quotation from «Enviada» on, a revision over a sent one.
    { label: "Registrar una cotización ya enviada", go: () => ask("quotation"), show: lifted.lost && mayCarryAQuote(card.stage) },
    { label: "Nueva revisión de una cotización", go: () => ask("revision"), show: lifted.lost && mayCarryAQuote(card.stage) && winnableRevisions(card).length > 0 },
    { label: "Buscar la OC en otro hilo", go: () => ask("po"), show: card.stage === "negotiating" && card.quotes.length > 0 },
    { label: "No es una solicitud", go: () => ask("notrequest"), show: lifted.lost && card.quotes.length === 0 },
    { label: "Asignar institución", go: () => ask("institution"), show: !card.organization && !card.closed_at },
  ];

  // One layout for Hoy's pane and the drawer: the decisions as labelled icons over the tabs.
  const body = (children: ReactNode, titled: boolean) => (
    <div className="space-y-3.5" data-testid="case-inline">
      {titled ? (
        <div className="flex items-start gap-3">
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-[19px] font-bold tracking-tight text-ink">{card.organization?.name ?? "Sin institución"}</h2>
            <p className="truncate text-[13px] text-ink-muted">{product ?? card.title}</p>
          </div>
          <CaseLinkIcons card={card} />
        </div>
      ) : null}
      {stepper}
      {iconStrip}
      {moreOpen ? (
        <ul className="grid gap-1 rounded-lg border border-line p-1.5" data-testid="case-inline-more">
          {moreItems.filter((m) => m.show).map((m) => (
            <li key={m.label}>
              <button type="button" onClick={() => { m.go(); setMoreOpen(false); }}
                className="w-full rounded-md px-2.5 py-1.5 text-left text-[13px] text-ink hover:bg-canvas-sunken">{m.label}</button>
            </li>
          ))}
        </ul>
      ) : null}
      <div className="sticky top-0 z-10 bg-canvas-raised pb-1"><Tabs base={base} tab={tab} onChange={setTab} /></div>
      <div className="space-y-4">{children}</div>
    </div>
  );
  // A plain function, not a component: a component made here would remount the tabs on each render.
  const frame = (children: ReactNode) =>
    embedded ? body(children, true) : (
      <Drawer open onClose={onClose} busy={refreshing} width="wide" title={card.organization?.name ?? "Sin institución"}
        subtitle={<span className="block truncate">{product ?? card.title}</span>} actions={<CaseLinkIcons card={card} />}>
        {body(children, false)}
      </Drawer>
    );

  return frame(
    <>
      <TabPanel base={base} tab="resumen" current={tab}>
        <CaseActions card={card} initial={request} onChanged={onChanged} hoisted bare />
        {/* The stepper already says the plain next step; this line only adds a reminder or task. */}
        <NextStepLine card={card} inFooter />
        {/* What the conversation says so far, first: the case memory (owner, 2026-10-15). */}
        <CaseMemory opportunityId={card.opportunity_id} />
        <OriginSelect card={card} onChanged={onChanged} />
        <CompleteFromMail card={card} onChanged={onChanged} />
        <CaseDocumentsBlock card={card} onChanged={onChanged} />
        <CaseTimeline key={`${card.opportunity_id}:${card.version ?? ""}`} opportunityId={card.opportunity_id} />
      </TabPanel>

      <TabPanel base={base} tab="cotizacion" current={tab}>
        <CaseMoneySummary card={card} />
        <Section
          title={card.quotes.length === 1 ? "Cotización" : `Cotizaciones (${card.quotes.length})`}
          aside={mayQuote && !card.closed_at ? <NewQuoteButton card={card} /> : null}
        >
          <QuoteList card={card} mayOpenCotizador={mayOpenCotizador} />
          <SystemNotes card={card} />
        </Section>
        <RevisionHistory card={card} />
      </TabPanel>

      <TabPanel base={base} tab="contacto" current={tab}>
        <CaseContact card={card} showPhones={contactSeen} />
      </TabPanel>

      <TabPanel base={base} tab="notas" current={tab}>
        {/* Re-read when the case's newest note changes: a reminder from «Recordarme…», a call. */}
        <CaseNotes key={`${card.opportunity_id}:${card.last_note?.created_at ?? ""}`} opportunityId={card.opportunity_id} mayAuthor={mayAuthor} />
      </TabPanel>

      <TabPanel base={base} tab="correos" current={tab}>
        <CaseThreads key={card.opportunity_id} opportunityId={card.opportunity_id} />
      </TabPanel>
    </>,
  );
}

/* ── tabs ────────────────────────────────────────────────────────────────── */

/** The ids that tie each tab to its panel, under one `useId` base per drawer. */
function tabIds(base: string, key: DrawerTab): { tab: string; panel: string } {
  return { tab: `${base}-tab-${key}`, panel: `${base}-panel-${key}` };
}

function Tabs({ base, tab, onChange }: { base: string; tab: DrawerTab; onChange: (t: DrawerTab) => void }) {
  const ids = (key: DrawerTab) => tabIds(base, key);
  const refs = useRef<Partial<Record<DrawerTab, HTMLButtonElement | null>>>({});
  function onKey(e: KeyboardEvent<HTMLButtonElement>, at: number) {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    e.preventDefault();
    const next = TABS[(at + (e.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length].key;
    onChange(next);
    refs.current[next]?.focus();
  }
  return (
    <div role="tablist" aria-label="Pestañas del caso" className="-mx-4 -mb-3 flex overflow-x-auto border-t border-line px-2">
      {TABS.map((t, i) => {
        const selected = t.key === tab;
        return (
          <button
            key={t.key}
            ref={(el) => {
              refs.current[t.key] = el;
            }}
            type="button"
            role="tab"
            id={ids(t.key).tab}
            aria-controls={ids(t.key).panel}
            aria-selected={selected}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(t.key)}
            onKeyDown={(e) => onKey(e, i)}
            className={`min-h-11 shrink-0 whitespace-nowrap border-b-2 px-2.5 text-[13px] font-medium focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-600 ${
              selected ? "border-brand-600 text-ink" : "border-transparent text-ink-muted hover:text-ink"
            }`}
          >
            {t.label}
          </button>
        );
      })}
    </div>
  );
}

function TabPanel({ base, tab, current, children }: { base: string; tab: DrawerTab; current: DrawerTab; children: ReactNode }) {
  const ids = tabIds(base, tab);
  return (
    <div role="tabpanel" id={ids.panel} aria-labelledby={ids.tab} tabIndex={0} hidden={tab !== current} className="space-y-5 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600">
      {children}
    </div>
  );
}

/* ── Resumen ─────────────────────────────────────────────────────────────── */

/** The case's follow-up reminder («Recordarme…») when it has one, else its next task or suggestion. */
function NextStepLine({ card, inFooter }: { card: OpportunityCardData; inFooter: boolean }) {
  // The same pending reminder the chip and footer read: one our later email or call already met is done.
  const reminder = pendingFollowUpTasks(card)[0] ?? null;
  const reminderNote = reminder ? (card.last_note?.body?.trim() ?? null) : null;
  // Due today or earlier: the case is on today's list now.
  const reminderDue = reminder ? Date.parse(reminder.due_at) <= endOfDay(new Date()).getTime() : false;
  if (reminder) {
    return (
      <p className="rounded-md bg-warn-bg/60 px-3 py-2 text-[13px] text-ink" data-testid="drawer-reminder">
        {reminderDue ? (
          <>
            Está hoy en <strong>Hoy → Llamar</strong>
          </>
        ) : (
          <>
            Te lo recuerdo el <strong>{fmtDate(reminder.due_at)}</strong> en <strong>Hoy → Llamar</strong>
          </>
        )}
        {reminderNote ? <span className="block text-[12px] text-ink-muted">{reminderNote}</span> : null}
      </p>
    );
  }
  if (card.next_action.source === "task") {
    return (
      <p className="rounded-md bg-canvas-sunken/60 px-3 py-2 text-[13px] text-ink">
        <span className="text-ink-faint">Próxima tarea: </span>
        {card.next_action.text}
        <span className="block text-[11px] text-ink-faint">
          Para el {fmtDate(card.next_action.due_at)}
          {card.open_tasks?.[0]?.owner ? `, ${card.open_tasks[0].owner}` : ""}
          {(card.open_tasks?.length ?? 0) > 1 ? ` (${(card.open_tasks?.length ?? 0) - 1} más)` : ""}
        </span>
      </p>
    );
  }
  if (inFooter) return null;
  return (
    <p className="rounded-md bg-canvas-sunken/60 px-3 py-2 text-[13px] text-ink">
      <span className="text-ink-faint">Siguiente: </span>
      {card.next_action.text}
    </p>
  );
}

/* ── Cotización ──────────────────────────────────────────────────────────── */

/**
 * What the system noted about the record, folded away: nothing here is for anyone to do (owner
 * 2026-10-25 — «there's no button to fix it»), so it never reads as a task.
 */
function SystemNotes({ card }: { card: OpportunityCardData }) {
  const notes = systemNotes(card);
  if (!notes.length) return null;
  return (
    <details className="group mt-2 text-[12px] text-ink-muted" data-testid="case-system-notes">
      <summary className="inline-flex cursor-pointer list-none items-center gap-1 rounded-sm text-ink-faint hover:text-ink-muted focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600">
        <span aria-hidden="true" className="transition-transform group-open:rotate-90">›</span>
        Notas del sistema ({notes.length})
      </summary>
      <ul className="mt-1.5 space-y-1 border-l border-line pl-3">
        {notes.map((n) => (
          <li key={n.code}>
            {n.label}
            {n.owner === "system" ? <span className="ml-1.5 text-[11px] text-good">· se resuelve solo</span> : null}
          </li>
        ))}
      </ul>
      <p className="mt-1.5 pl-3 text-[11px] text-ink-faint">No tienes que hacer nada con esto.</p>
    </details>
  );
}

/**
 * The drawer's way into the Cotizador: «Abrir borrador» when the case has an open draft, else
 * «Nueva cotización» (`create-quote`). Whether the case may get one is the API's to say.
 */
function NewQuoteButton({ card }: { card: OpportunityCardData }) {
  const quote = useOpenOrCreateQuote();
  const opensDraft = quoteToOpen(card) !== null;
  return (
    <Button variant="primary" busy={quote.busy} busyLabel="Creando…" onClick={() => void quote.run(card)}>
      {opensDraft ? "Abrir borrador" : "Nueva cotización"}
    </Button>
  );
}

/* ── Contacto ────────────────────────────────────────────────────────────── */

const CONTACT_LINK =
  "flex min-w-0 items-baseline gap-2 rounded-md px-2 py-1 -mx-2 text-ink hover:bg-canvas-sunken hover:text-accent-strong";

function CaseContact({ card, showPhones }: { card: OpportunityCardData; showPhones: boolean }) {
  return (
    <Section title="Contacto">
      <ul className="space-y-1.5 text-[13px]">
        <li>
          {card.organization ? (
            <a href={crmHash("organizaciones", card.organization.organization_id)} className={CONTACT_LINK} data-testid="case-org-link">
              <span className="text-ink-faint">Empresa</span>
              <span className="truncate font-medium">{card.organization.name ?? "Sin nombre"}</span>
            </a>
          ) : (
            <span className="text-bad">Sin institución</span>
          )}
        </li>
        {card.contact ? (
          <li>
            {card.contact.person_id ? (
              <a href={crmHash("personas", card.contact.person_id)} className={CONTACT_LINK} data-testid="case-person-link">
                <span className="text-ink-faint">Persona</span>
                <span className="truncate font-medium">{card.contact.name ?? "Sin nombre"}</span>
              </a>
            ) : card.contact.address ? (
              <a
                href={gmailSearchUrl(card.contact.address)}
                target="_blank"
                rel="noopener noreferrer"
                className={CONTACT_LINK}
                title="Buscar sus correos en Gmail"
                data-testid="case-person-link"
              >
                <span className="text-ink-faint">Persona</span>
                <span className="truncate font-medium">{card.contact.name ?? displayNameOf(card.contact.address)}</span>
                <span aria-hidden="true" className="text-[10px] text-ink-faint">↗</span>
              </a>
            ) : (
              <span className="text-ink">{card.contact.name}</span>
            )}
            {card.contact.person_id && showPhones ? <PersonPhones personId={card.contact.person_id} /> : null}
          </li>
        ) : null}
      </ul>
    </Section>
  );
}

/* ── Notas y llamadas ────────────────────────────────────────────────────── */

/** The case's notes, read from the API; a call is a typed note («Llamada · Contestó»). */
function CaseNotes({ opportunityId, mayAuthor }: { opportunityId: string; mayAuthor: boolean }) {
  const load = useCallback(() => fetchCaseNotes(opportunityId), [opportunityId]);
  const [state, reload] = useResource(load, [opportunityId]);
  return (
    <>
      <NoteList
        notes={state.kind === "ready" ? state.data.notes : []}
        subjectKind="opportunity"
        subjectId={opportunityId}
        mayAuthor={mayAuthor}
        onRefresh={reload}
      />
      {state.kind === "loading" ? <p className="-mt-2 text-[11px] text-ink-faint">Cargando notas…</p> : null}
      {state.kind !== "ready" && state.kind !== "loading" ? (
        <p className="-mt-2 text-[11px] text-warn" data-testid="case-notes-unavailable">
          No se pudieron leer las notas de este caso{state.kind === "unavailable" ? " (no habilitado en este entorno)" : ""}.
        </p>
      ) : null}
    </>
  );
}
