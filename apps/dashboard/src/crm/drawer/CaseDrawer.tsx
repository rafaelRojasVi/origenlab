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
import { useMayRunCaseCommands } from "../caseCommands";
import { quoteProduct, systemNotes } from "../caseDisplay";
import { originLabel } from "../caseOrigin";
import { fetchCaseNotes } from "../crmApi";
import { crmHash } from "../crmRoute";
import type { OpportunityCardData } from "../crmTypes";
import { inSharedMailbox } from "../gmailLinks";
import { formatMoney } from "../money";
import { CaseActions, hoistedActions, type Mode } from "../pages/CaseActions";
import { CaseDocumentsBlock } from "../pages/CaseDocuments";
import { CaseMemory } from "../pages/CaseMemory";
import type { MoveTarget } from "../pages/CaseMove";
import { CaseLinks, CaseMoneySummary, QuoteList } from "../pages/CaseQuotes";
import { CaseThreads } from "../pages/CaseThreads";
import { CompleteFromMail } from "../pages/CompleteFromMail";
import { quoteToOpen, useMayOpenCotizador, useOpenOrCreateQuote } from "../quotes/openOrCreateQuote";
import { isMaskedAddress } from "../redaction";
import { STATUS_LABEL, stageDisplay } from "../stage";
import { endOfDay, followUpStep, followUpsDue, isFollowUpTask, pendingFollowUpTasks } from "../today";
import { Badge, Button, Drawer, Section, buttonClass, fmtDate } from "../ui";
import { useResource } from "../useResource";
import { CaseTimeline } from "./CaseTimeline";
import { PersonPhones } from "./PersonPhones";
import { RevisionHistory } from "./RevisionHistory";

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
 * «Seguimiento N de 3 · …» for the header: with a «Seguimiento …» reminder still pending (one an
 * email of ours already met is done: `pendingFollowUpTasks`), its step and its day — «hoy» when it
 * is due today or overdue, else its weekday; the step is the one the rhythm will be on that day,
 * read from `followUpsDue` as of then. Otherwise, when the case is due today by the rhythm alone,
 * «hoy». Null when neither.
 */
function followUpChip(card: OpportunityCardData, now: Date): { step: 1 | 2 | 3; day: string } | null {
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

function CaseDrawerBody({
  card,
  onClose,
  onChanged,
  refreshing,
  initial,
}: {
  card: OpportunityCardData;
  onClose: () => void;
  onChanged: () => void;
  refreshing: boolean;
  initial: FormRequest;
}) {
  const mayAuthor = useMayAuthorCrm();
  const mayQuote = useMayRunCaseCommands();
  const mayOpenCotizador = useMayOpenCotizador();
  const lifted = hoistedActions(card, mayQuote, mayAuthor);
  const [tab, setTab] = useState<DrawerTab>("resumen");
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

  const stage = stageDisplay(card);
  const status = STATUS_LABEL[card.status];
  const product = quoteProduct(card);
  const money = card.money?.[0];
  const net = money && money.currency !== "unread" ? formatMoney(money.net_total, money.currency) : null;
  const followUp = followUpChip(card, new Date());
  // The Contacto tab reads the person's phones only once it has been opened, then keeps them.
  const [contactSeen, setContactSeen] = useState(false);
  if (tab === "contacto" && !contactSeen) setContactSeen(true);
  const reply = replyUrl(card);
  const reminder = [...(card.open_tasks ?? [])].filter(isFollowUpTask).sort((a, b) => a.due_at.localeCompare(b.due_at))[0] ?? null;
  const nextStep = reminder
    ? `${reminder.title} · ${shortDay(reminder.due_at)}`
    : card.next_action.source === "task" && card.next_action.due_at
      ? `${card.next_action.text} · ${shortDay(card.next_action.due_at)}`
      : card.next_action.text;

  const header = (
    <div className="space-y-2.5">
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge tone={stage.tone} glyph={false} title={stage.title}>
          {stage.label}
        </Badge>
        {card.status === "ok" ? null : <Badge tone={status.tone}>{status.label}</Badge>}
        {followUp ? (
          <span
            className="inline-flex items-center rounded-full border border-warn/30 bg-warn-bg px-1.5 py-px text-[11px] font-semibold leading-4 text-warn"
            data-testid="case-drawer-followup"
          >
            Seguimiento {followUp.step} de 3 · {followUp.day}
          </span>
        ) : null}
        {net ? (
          <span className="inline-flex items-center gap-1.5 text-[13px] font-semibold text-ink tabular-nums" data-testid="case-drawer-net">
            {net} <span className="text-[11px] font-normal text-ink-muted">neto</span>
            {money?.revision_no && money.revision_no > 1 ? (
              <span className="rounded-full bg-canvas-sunken px-1.5 text-[10.5px] font-semibold text-ink-muted">rev {money.revision_no}</span>
            ) : null}
          </span>
        ) : null}
        <span data-testid="case-origin-badge">
          <Badge glyph={false} title="Por dónde llegó el caso">
            Origen: {originLabel(card.origin)}
          </Badge>
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-2" data-testid="case-drawer-actions">
        {lifted.call ? (
          <Button variant="primary" className="min-h-11" onClick={() => ask("call")}>
            Registrar llamada
          </Button>
        ) : null}
        {reply ? (
          <a href={reply} target="_blank" rel="noopener noreferrer" className={`${buttonClass("secondary")} min-h-11`}>
            Responder en Gmail <span aria-hidden="true" className="ml-1 text-ink-faint">↗</span>
          </a>
        ) : null}
        {lifted.followUp ? (
          <Button className="min-h-11" onClick={() => ask("followup")}>
            Recordarme…
          </Button>
        ) : null}
      </div>
      <Tabs base={base} tab={tab} onChange={setTab} />
    </div>
  );

  const footer =
    lifted.won || lifted.lost ? (
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2" data-testid="case-drawer-footer">
        <p className="min-w-0 basis-full truncate text-[12.5px] text-ink-muted sm:basis-auto sm:flex-1" title={nextStep}>
          Siguiente paso: <strong className="font-semibold text-ink">{nextStep}</strong>
        </p>
        <div className="ml-auto flex gap-2">
          {lifted.lost ? (
            <Button className="min-h-11" onClick={() => ask("stage", "perdida")}>
              Marcar perdida
            </Button>
          ) : null}
          {lifted.won ? (
            <Button variant="primary" className="min-h-11" onClick={() => ask("won")}>
              Marcar ganada
            </Button>
          ) : null}
        </div>
      </div>
    ) : null;

  return (
    <Drawer
      open
      onClose={onClose}
      busy={refreshing}
      width="wide"
      title={card.organization?.name ?? "Sin institución"}
      subtitle={<span className="block truncate">{product ?? card.title}</span>}
      actions={header}
      footer={footer}
    >
      <TabPanel base={base} tab="resumen" current={tab}>
        <CaseLinks card={card} />
        <CaseActions card={card} initial={request} onChanged={onChanged} hoisted />
        <NextStepLine card={card} />
        {/* What the conversation says so far, first: the case memory (owner, 2026-10-15). */}
        <CaseMemory opportunityId={card.opportunity_id} />
        <Section title="Origen">
          <OriginSelect card={card} onChanged={onChanged} />
        </Section>
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
    </Drawer>
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
function NextStepLine({ card }: { card: OpportunityCardData }) {
  const reminder = [...(card.open_tasks ?? [])].filter(isFollowUpTask).sort((a, b) => a.due_at.localeCompare(b.due_at))[0] ?? null;
  const reminderNote = reminder ? (card.last_note?.body?.trim() ?? null) : null;
  // Due today or earlier: the case is on today's list now.
  const reminderDue = reminder ? Date.parse(reminder.due_at) <= endOfDay(new Date()).getTime() : false;
  if (reminder) {
    return (
      <p className="rounded-md bg-warn-bg/60 px-3 py-2 text-[13px] text-ink" data-testid="drawer-reminder">
        {reminderDue ? (
          <>
            Está hoy en <strong>Hoy → Seguimientos</strong>
          </>
        ) : (
          <>
            Te lo recuerdo el <strong>{fmtDate(reminder.due_at)}</strong> en <strong>Hoy → Seguimientos</strong>
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
