/**
 * The body of «Hoy» (`OverviewPage`): the lists `today.ts` computes from the pipeline, each row
 * with the one action it needs.
 *
 * - **Te toca responder** — first: a client is waiting. The client's email, «Pasar a
 *   Conversación» when the case is still «Enviada», and «No requiere respuesta» for a «gracias, le
 *   aviso»: it schedules a «Seguimiento …» a week out, which takes the row off the list until the
 *   client writes again (`today.ts`).
 * - **Seguimientos** — one list, a traffic light by the 3 · 14 · 30-day rhythm (green, yellow,
 *   red), five rows per colour before «Ver N más». A row is the days, the case, what the quote is
 *   for, the quote number and who, and one button: «Responder en Gmail» on the case's thread (the
 *   captured email clears the row by itself). «⋯» holds the rest: «Recordar en 1 semana», «Ya le
 *   escribí» (a row a task put there) and «Cerrar sin respuesta» (the «Perdida» form).
 * - **Otras tareas** — the due tasks that are not follow-ups («Retomar: …»), same two buttons.
 * - Aside: the historical cases still to decide, the institutions to confirm, the people the quote
 *   emails name, and the blocked cases.
 *
 * Writes go through the same commands as the drawer (`caseCommands.ts`, `crmAuthoringApi.ts`),
 * gated the same way; a viewer sees the lists without the buttons. Every recorded write refetches.
 */
import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { addNote, confirmOrganizationRecord, fetchPersonSuggestions, refusalOf } from "../authoring/crmAuthoringApi";
import { useMayAuthorCrm } from "../authoring/authoring";
import {
  cancelTask,
  caseRefusalText,
  completeTask,
  createTask,
  moveCase,
  newCaseCommandKey,
  useMayRunCaseCommands,
} from "../caseCommands";
import { contactLine, displayName, quoteProduct } from "../caseDisplay";
import { composeInSharedMailbox, inSharedMailbox } from "../gmailLinks";
import { isMaskedAddress } from "../redaction";
import { useAuthSession } from "../../context/AuthSessionContext";
import type { CrmSection, DatosTab } from "../crmRoute";
import type { OpportunityCardData } from "../crmTypes";
import {
  OVERDUE_ALL_DAYS,
  OVERDUE_OWN_DAYS,
  RHYTHM,
  followUpsDue,
  historicalCount,
  wroteAfterWinning,
  organizationsToConfirm,
  overdueTasks,
  repliesToAnswer,
  tasksDue,
  type DueTask,
  type FollowUp,
  type OrgToConfirm,
  type OverdueTask,
  type Reply,
} from "../today";
import { Badge, Button, Modal, Panel, fmtDate, toast } from "../ui";
import { type ResourceState } from "../useResource";
import { useLeave } from "../useLeave";
import { CaseMoveForm } from "./CaseMove";

type Navigate = (s: CrmSection, id?: string | null, tab?: DatosTab) => void;

const WEEK_MS = 7 * 86_400_000;

function CaseLink({ card, navigate }: { card: OpportunityCardData; navigate: Navigate }) {
  const { name } = displayName(card);
  return (
    <button
      type="button"
      onClick={() => navigate("oportunidades", card.opportunity_id)}
      className="max-w-full truncate rounded text-left text-[13px] font-medium text-ink hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
    >
      {name}
    </button>
  );
}

function quoteOf(card: OpportunityCardData): string {
  const latest = card.latest_revision;
  return latest ? `${latest.quote_number} · enviada ${fmtDate(latest.sent_at)}` : "sin cotización";
}

export function TodayBody({
  items,
  navigate,
  onChanged,
  refreshing = false,
  now,
  people,
  aside,
  after,
}: {
  items: OpportunityCardData[];
  navigate: Navigate;
  onChanged: () => void;
  refreshing?: boolean;
  now?: Date;
  /** Person suggestions, read by the page beside the pipeline so neither waits on the other. */
  people?: ResourceState<Awaited<ReturnType<typeof fetchPersonSuggestions>>>;
  /** What the page keeps at hand on the side, above the lists: the quote number and the rates. */
  aside?: ReactNode;
  /** The main column's last panel: the emails no case holds. */
  after?: ReactNode;
}) {
  const at = useMemo(() => now ?? new Date(), [now]);
  const { session } = useAuthSession();
  const me = session.kind === "signed_in" ? (session.profile?.displayName ?? session.operator.displayName ?? null) : null;
  const overdue = useMemo(() => overdueTasks(items, at, me), [items, at, me]);
  const tasks = useMemo(() => {
    const shownAbove = new Set(overdue.map((o) => o.task.task_id));
    return tasksDue(items, at).filter((t) => !shownAbove.has(t.task.task_id));
  }, [items, at, overdue]);
  const replies = useMemo(() => repliesToAnswer(items, at), [items, at]);
  const followUps = useMemo(() => followUpsDue(items, at), [items, at]);
  const orgs = useMemo(() => organizationsToConfirm(items), [items]);
  const historical = historicalCount(items, at);
  // A blocked case a client is waiting on shows once, in «Te toca responder», with what blocks it.
  const blocked = items.filter((i) => i.status === "blocked" && !replies.some((r) => r.card.opportunity_id === i.opportunity_id));
  const mayDecide = useMayRunCaseCommands();
  const mayAuthor = useMayAuthorCrm();
  const [closing, setClosing] = useState<OpportunityCardData | null>(null);

  return (
    <div className="space-y-4" aria-busy={refreshing || undefined}>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_21rem]">
        <div className="min-w-0 space-y-4">
          {/* Three lists and the new mail (owner decision 2026-10-10): who wrote, who to chase, what
              is due — then the emails no case holds. Everything else sits in the side column. */}
          <RepliesPanel replies={replies} navigate={navigate} mayDecide={mayDecide} onChanged={onChanged} now={at} />
          <FollowUpsPanel followUps={followUps} navigate={navigate} mayDecide={mayDecide} onChanged={onChanged} onClose={setClosing} now={at} />
          <TasksPanel overdue={overdue} tasks={tasks} navigate={navigate} mayDecide={mayDecide} onChanged={onChanged} now={at} />
          {after}
        </div>
        <aside className="min-w-0 space-y-4 lg:sticky lg:top-4 lg:self-start">
          {aside}
          <PeopleCount state={people} navigate={navigate} />
          {historical > 0 ? (
            <section className="crm-rise rounded-xl border border-info/30 bg-info-bg/40 p-4" data-testid="today-historical">
              <h2 className="text-[14px] font-semibold text-ink">
                {historical} {historical === 1 ? "caso" : "casos"} por decidir
              </h2>
              <p className="mt-1 text-xs leading-5 text-ink-muted">
                Siguen «sin decidir»: nadie ha dicho si siguen vivos. Cada uno trae una propuesta a partir del correo.
              </p>
              <Button className="mt-3" variant="primary" onClick={() => navigate("oportunidades")}>
                Decidirlos en Oportunidades
              </Button>
            </section>
          ) : null}
          <OrgsPanel orgs={orgs} navigate={navigate} mayAuthor={mayAuthor} onChanged={onChanged} />
          {blocked.length > 0 ? (
            <Panel title="Bloqueados" aside={<Badge tone="bad" glyph={false}>{blocked.length}</Badge>} bodyClassName="divide-y divide-line">
              {blocked.map((c) => (
                <div key={c.opportunity_id} className="flex items-center gap-2 px-3 py-2">
                  <div className="min-w-0 flex-1">
                    <CaseLink card={c} navigate={navigate} />
                    <span className="block truncate text-[11px] text-bad">{c.attention.find((a) => a.blocking)?.label}</span>
                  </div>
                  <Button onClick={() => navigate("oportunidades", c.opportunity_id)}>Resolver</Button>
                </div>
              ))}
            </Panel>
          ) : null}
        </aside>
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

/* ─────────────────────────────────────────────────────────────── tasks ── */

/**
 * «Tareas»: one list. Overdue first — one's own from 3 days late, anyone's from 7 (`overdueTasks`,
 * owner decision 2026-10-10), the escalated ones naming whose they are — then the rest due by
 * tonight. Follow-ups live in «Seguimientos». No empty panel.
 */
function TasksPanel({
  overdue,
  tasks,
  navigate,
  mayDecide,
  onChanged,
  now,
}: {
  overdue: OverdueTask[];
  tasks: DueTask[];
  navigate: Navigate;
  mayDecide: boolean;
  onChanged: () => void;
  now: Date;
}) {
  const total = overdue.length + tasks.length;
  if (total === 0) return null;
  return (
    <div data-testid="today-tasks">
      <Panel
        title="Tareas"
        note={overdue.length ? `vencidas primero · tuyas desde ${OVERDUE_OWN_DAYS} días, de cualquiera desde ${OVERDUE_ALL_DAYS}` : undefined}
        aside={<Badge tone={overdue.length ? "bad" : "warn"} glyph={false}>{total}</Badge>}
        bodyClassName="divide-y divide-line"
      >
        {overdue.map((t, i) => (
          <TaskRow key={t.task.task_id} due={t} index={i} navigate={navigate} mayDecide={mayDecide} onChanged={onChanged} now={now}
            ownerLabel={t.escalated ? `de ${t.task.owner ?? "otro perfil"}` : null} />
        ))}
        {tasks.map((t, i) => (
          <TaskRow key={t.task.task_id} due={t} index={overdue.length + i} navigate={navigate} mayDecide={mayDecide} onChanged={onChanged} now={now} />
        ))}
      </Panel>
    </div>
  );
}

/** «Personas por agregar» as a count in the side column; the list itself lives on Personas. */
function PeopleCount({ state, navigate }: {
  state?: ResourceState<Awaited<ReturnType<typeof fetchPersonSuggestions>>>;
  navigate: Navigate;
}) {
  if (!state || state.kind !== "ready" || state.data.total === 0) return null;
  const n = state.data.total;
  return (
    <section className="rounded-xl border border-line bg-canvas-raised p-4" data-testid="today-people-count">
      <h2 className="text-[14px] font-semibold text-ink">{n} {n === 1 ? "persona" : "personas"} por agregar</h2>
      <p className="mt-1 text-xs leading-5 text-ink-muted">Nombres y direcciones de los correos de cotización que aún no están en el CRM.</p>
      <Button className="mt-3" onClick={() => navigate("personas")}>Revisarlas en Personas</Button>
    </section>
  );
}

function TaskRow({
  due,
  index,
  navigate,
  mayDecide,
  onChanged,
  now,
  ownerLabel = null,
}: {
  due: DueTask;
  index: number;
  navigate: Navigate;
  mayDecide: boolean;
  onChanged: () => void;
  now: Date;
  /** «de Tatiana»: an escalated task shown to a profile that does not own it. */
  ownerLabel?: string | null;
}) {
  const { card, task, overdueDays } = due;
  const { leaving, gone, leave } = useLeave(due);
  const actions = useTaskActions(due, now, onChanged, leave);
  if (gone) return null;

  return (
    <div
      className={`crm-rise flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-2.5 ${leaving ? "crm-row-out" : ""}`}
      style={{ "--i": Math.min(index, 10) } as CSSProperties}
      data-testid={`today-task-${task.task_id}`}
    >
      <div className="min-w-[12rem] flex-1">
        <p className="text-[13px] text-ink">{task.title}</p>
        <p className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-[11px] text-ink-muted">
          <span className={overdueDays > 0 ? "font-medium text-bad" : "font-medium text-warn"}>
            {dueText(overdueDays)}
          </span>
          {ownerLabel ? (
            <>
              <span aria-hidden="true">·</span>
              <span data-testid="today-owner-label">{ownerLabel}</span>
            </>
          ) : null}
          <span aria-hidden="true">·</span>
          <CaseLink card={card} navigate={navigate} />
        </p>
      </div>
      {mayDecide ? (
        <div className="flex shrink-0 gap-2">
          <TaskButtons actions={actions} />
        </div>
      ) : null}
    </div>
  );
}


/** «Hecho» and «+1 semana» for a due task — shared by «Seguimientos» and «Otras tareas». */
function useTaskActions(due: DueTask, now: Date, onChanged: () => void, leave?: () => void) {
  const { card, task } = due;
  const [busy, setBusy] = useState<null | "done" | "snooze">(null);
  const keys = useRef({ done: newCaseCommandKey(), create: newCaseCommandKey(), cancel: newCaseCommandKey() });

  async function done() {
    setBusy("done");
    try {
      await completeTask({ task_id: task.task_id, task_version: task.version, note: "Hecho desde «Hoy»." }, keys.current.done);
      toast("Tarea hecha.");
      leave?.();
      onChanged();
    } catch (err) {
      keys.current.done = newCaseCommandKey();
      toast(caseRefusalText(err), "bad");
    } finally {
      setBusy(null);
    }
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
      leave?.();
      onChanged();
    } catch (err) {
      keys.current = { done: keys.current.done, create: newCaseCommandKey(), cancel: newCaseCommandKey() };
      toast(caseRefusalText(err), "bad");
      onChanged();
    } finally {
      setBusy(null);
    }
  }

  return { busy, done, snooze };
}

function TaskButtons({ actions }: { actions: ReturnType<typeof useTaskActions> }) {
  const { busy, done, snooze } = actions;
  return (
    <>
      <Button onClick={() => void snooze()} busy={busy === "snooze"} busyLabel="Posponiendo…" disabled={busy !== null}>
        +1 semana
      </Button>
      <Button variant="primary" onClick={() => void done()} busy={busy === "done"} busyLabel="Guardando…" disabled={busy !== null}>
        Hecho
      </Button>
    </>
  );
}

function dueText(overdueDays: number): string {
  return overdueDays === 0 ? "Vence hoy" : overdueDays === 1 ? "Atrasada 1 día" : `Atrasada ${overdueDays} días`;
}

/* ───────────────────────────────────────────────────────────── replies ── */

function RepliesPanel({
  replies,
  navigate,
  mayDecide,
  onChanged,
  now,
}: {
  replies: Reply[];
  navigate: Navigate;
  mayDecide: boolean;
  onChanged: () => void;
  now: Date;
}) {
  return (
    <Panel title="Te toca responder" aside={<Badge tone={replies.length ? "warn" : "good"} glyph={false}>{replies.length}</Badge>} bodyClassName="divide-y divide-line">
      {replies.length === 0 ? (
        <p className="px-4 py-4 text-xs text-ink-muted">Ningún cliente espera respuesta.</p>
      ) : (
        replies.map((r, i) => <ReplyRow key={r.card.opportunity_id} reply={r} index={i} navigate={navigate} mayDecide={mayDecide} onChanged={onChanged} now={now} />)
      )}
    </Panel>
  );
}

function ReplyRow({
  reply,
  index,
  navigate,
  mayDecide,
  onChanged,
  now,
}: {
  reply: Reply;
  index: number;
  navigate: Navigate;
  mayDecide: boolean;
  onChanged: () => void;
  now: Date;
}) {
  const { card } = reply;
  const { leaving, gone, leave } = useLeave(reply);
  const [busy, setBusy] = useState<null | "move" | "skip" | "handled">(null);
  const keys = useRef([newCaseCommandKey()]);
  const skipKey = useRef(newCaseCommandKey());
  const handledKey = useRef(newCaseCommandKey());
  const mayAuthor = useMayAuthorCrm();
  const blocking = card.attention.find((a) => a.blocking) ?? null;
  // A won case takes no task, so the only way off the list is answering the email.
  const won = wroteAfterWinning(card);
  const canMove = !blocking && mayDecide && card.stage === "quoting" && typeof card.version === "number";

  async function noAnswerNeeded() {
    setBusy("skip");
    const next = new Date(now.getTime() + WEEK_MS);
    const quote = card.latest_revision?.quote_number ?? card.quote_numbers[0] ?? "";
    try {
      await createTask(
        {
          opportunity_id: card.opportunity_id,
          title: `Seguimiento de ${quote}`.trim(),
          due_at: next.toISOString(),
          note: `El cliente escribió el ${fmtDate(reply.at)}; no requiere respuesta.`,
        },
        skipKey.current,
      );
      toast(`Listo. Te lo recuerdo el ${fmtDate(next.toISOString())}.`);
      leave();
      onChanged();
    } catch (err) {
      skipKey.current = newCaseCommandKey();
      toast(caseRefusalText(err), "bad");
    } finally {
      setBusy(null);
    }
  }

  // A won case takes no task, so «Atendido» leaves a note on the case: the client's email was
  // answered by other means or needs nothing. `notedAfter` then keeps the row out until the
  // client writes again. The note is visible in the case drawer like any other.
  async function handled() {
    setBusy("handled");
    try {
      await addNote(
        {
          subject_kind: "opportunity",
          subject_id: card.opportunity_id,
          body: `Atendido: el cliente escribió el ${fmtDate(reply.at)} tras ganar el caso; respondido por otro medio o sin respuesta pendiente.`,
        },
        handledKey.current,
      );
      toast("Listo. Queda anotado en el caso.");
      leave();
      onChanged();
    } catch (err) {
      handledKey.current = newCaseCommandKey();
      toast(refusalOf(err)?.message ?? "No se pudo anotar el caso.", "bad");
    } finally {
      setBusy(null);
    }
  }

  async function toConversation() {
    setBusy("move");
    try {
      await moveCase(
        { opportunity_id: card.opportunity_id, stage: card.stage, version: card.version as number },
        "negotiating",
        `El cliente respondió el ${fmtDate(reply.at)}.`,
        null,
        keys.current,
      );
      toast("Pasó a «Conversación».");
      leave();
      onChanged();
    } catch (err) {
      keys.current = [newCaseCommandKey()];
      toast(caseRefusalText(err), "bad");
    } finally {
      setBusy(null);
    }
  }

  if (gone) return null;
  return (
    <div className={`crm-rise flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-2.5 ${leaving ? "crm-row-out" : ""}`} style={{ "--i": Math.min(index, 10) } as CSSProperties}>
      <div className="min-w-[12rem] flex-1">
        <CaseLink card={card} navigate={navigate} />
        <p className="mt-0.5 truncate text-[11px] text-ink-muted">
          <span className="font-medium text-warn">
            {won ? "Escribió tras ganar" : card.latest_revision ? "Respondió" : "Escribió"} el {fmtDate(reply.at)}
          </span>
          {card.latest_revision ? ` · ${quoteOf(card)}` : ""}
          {contactLine(card) ? ` · ${contactLine(card)}` : ""}
        </p>
        {blocking ? <p className="mt-0.5 truncate text-[11px] font-medium text-bad">{blocking.label}</p> : null}
      </div>
      <div className="flex shrink-0 gap-2">
        {reply.url ? (
          <a
            href={inSharedMailbox(reply.url) ?? undefined}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex h-8 items-center rounded-md border border-line bg-canvas-raised px-3 text-xs font-medium text-ink hover:border-line-strong"
          >
            Abrir respuesta ↗
          </a>
        ) : null}
        {blocking ? (
          <Button variant="primary" onClick={() => navigate("oportunidades", card.opportunity_id)}>
            Resolver
          </Button>
        ) : mayDecide && !won ? (
          <Button
            variant="secondary"
            onClick={() => void noAnswerNeeded()}
            busy={busy === "skip"}
            busyLabel="Guardando…"
            disabled={busy !== null}
            title="Un «gracias, le aviso»: sale de la lista y vuelve como seguimiento en una semana"
          >
            No requiere respuesta
          </Button>
        ) : won && mayAuthor ? (
          <Button
            variant="secondary"
            onClick={() => void handled()}
            busy={busy === "handled"}
            busyLabel="Anotando…"
            disabled={busy !== null}
            title="Respondiste por otro medio o no requiere respuesta: queda anotado en el caso y sale de la lista hasta que el cliente vuelva a escribir"
          >
            Atendido
          </Button>
        ) : null}
        {canMove ? (
          <Button
            variant="primary"
            onClick={() => void toConversation()}
            busy={busy === "move"}
            busyLabel="Moviendo…"
            disabled={busy !== null}
          >
            Pasar a Conversación
          </Button>
        ) : null}
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────── follow-ups ── */

const LIGHT = {
  good: { chip: "bg-good-bg text-good", dot: "bg-good", text: "text-good" },
  warn: { chip: "bg-warn-bg text-warn", dot: "bg-warn", text: "text-warn" },
  bad: { chip: "bg-bad-bg text-bad", dot: "bg-bad", text: "text-bad" },
} as const;

/** Rows a group shows before «Ver N más». */
const GROUP_PREVIEW = 5;

function FollowUpsPanel({
  followUps,
  navigate,
  mayDecide,
  onChanged,
  onClose,
  now,
}: {
  followUps: FollowUp[];
  navigate: Navigate;
  mayDecide: boolean;
  onChanged: () => void;
  onClose: (c: OpportunityCardData) => void;
  now: Date;
}) {
  return (
    <section className="crm-rise overflow-hidden rounded-xl border border-line bg-canvas-raised" data-testid="today-followups">
      <header className="flex flex-wrap items-baseline gap-x-2 gap-y-1 px-4 pb-1 pt-3">
        <h2 className="text-[14px] font-semibold text-ink">Seguimientos</h2>
        <span className="text-[12px] tabular-nums text-ink-muted">{followUps.length}</span>
        <span className="ml-auto text-[11px] text-ink-faint">Responde en el hilo: el sistema lo registra solo.</span>
      </header>
      {followUps.length === 0 ? (
        <p className="px-4 pb-4 pt-1 text-xs text-ink-muted">Ningún seguimiento pendiente.</p>
      ) : (
        <div className="pb-1">
          {RHYTHM.map((r) => {
            const rows = followUps.filter((f) => f.rhythm === r.key);
            if (rows.length === 0) return null;
            return (
              <FollowUpGroup key={r.key} rhythm={r} rows={rows} navigate={navigate} mayDecide={mayDecide} onChanged={onChanged} onClose={onClose} now={now} />
            );
          })}
        </div>
      )}
    </section>
  );
}

function FollowUpGroup({
  rhythm,
  rows,
  navigate,
  mayDecide,
  onChanged,
  onClose,
  now,
}: {
  rhythm: (typeof RHYTHM)[number];
  rows: FollowUp[];
  navigate: Navigate;
  mayDecide: boolean;
  onChanged: () => void;
  onClose: (c: OpportunityCardData) => void;
  now: Date;
}) {
  const [all, setAll] = useState(false);
  const shown = all ? rows : rows.slice(0, GROUP_PREVIEW);
  return (
    <div data-testid={`today-rhythm-${rhythm.key}`} className="mt-1">
      <p className="flex items-center gap-2 px-4 pb-1 pt-2 text-[11px] text-ink-muted">
        <span aria-hidden="true" className={`h-2 w-2 rounded-full ${LIGHT[rhythm.tone].dot}`} />
        <span className={`font-semibold ${LIGHT[rhythm.tone].text}`}>{rhythm.label}</span>
        <span className="tabular-nums">{rows.length}</span>
        <span className="hidden text-ink-faint sm:inline">· {rhythm.hint}</span>
      </p>
      <ul>
        {shown.map((f, i) => (
          <FollowUpRow
            key={f.card.opportunity_id}
            followUp={f}
            tone={rhythm.tone}
            index={i}
            navigate={navigate}
            mayDecide={mayDecide}
            onChanged={onChanged}
            onClose={onClose}
            now={now}
          />
        ))}
      </ul>
      {rows.length > GROUP_PREVIEW ? (
        <button
          type="button"
          onClick={() => setAll((v) => !v)}
          className="mx-4 mb-1 mt-0.5 rounded text-[11px] font-medium text-brand-700 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
        >
          {all ? "Ver menos" : `Ver ${rows.length - GROUP_PREVIEW} más`}
        </button>
      ) : null}
    </div>
  );
}

function FollowUpRow({
  followUp,
  tone,
  index,
  navigate,
  mayDecide,
  onChanged,
  onClose,
  now,
}: {
  followUp: FollowUp;
  tone: keyof typeof LIGHT;
  index: number;
  navigate: Navigate;
  mayDecide: boolean;
  onChanged: () => void;
  onClose: (c: OpportunityCardData) => void;
  now: Date;
}) {
  const f = followUp;
  const product = quoteProduct(f.card);
  const who = f.card.contact?.name?.trim() || f.card.contact?.address || null;
  const quote = f.card.latest_revision?.quote_number ?? f.card.quote_numbers[0] ?? "";
  // Reply on the case's own thread: the email stays in it, so the system sees the follow-up and
  // restarts the count. With no thread to reply on, a new email from the shared mailbox.
  const thread = inSharedMailbox(f.card.last_contact?.outbound?.url ?? f.card.latest_revision?.gmail?.url);
  const to = f.card.contact?.address && !isMaskedAddress(f.card.contact.address) ? f.card.contact.address : null;
  const write = thread ?? composeInSharedMailbox(to, `Seguimiento cotización N° ${quote}`.trim());
  const since = f.byEmail ? "desde tu último correo" : "desde la cotización";
  const [menuOpen, setMenuOpen] = useState(false);
  const { leaving, gone, leave } = useLeave(f);
  if (gone) return null;
  return (
    <li
      // Each row animates in (a transform), so an open menu needs its row lifted over the next one.
      className={`crm-rise group relative flex items-center gap-3 px-4 py-2 transition-colors hover:bg-canvas-sunken/50 ${menuOpen ? "z-20" : ""} ${leaving ? "crm-row-out" : ""}`}
      style={{ "--i": Math.min(index, 10) } as CSSProperties}
      data-testid={f.task ? `today-task-${f.task.task.task_id}` : `today-followup-${f.card.opportunity_id}`}
    >
      <span
        className={`flex h-9 w-9 shrink-0 flex-col items-center justify-center rounded-full leading-none ${LIGHT[tone].chip}`}
        title={`${f.days} días ${since}`}
      >
        <span className="text-[13px] font-bold tabular-nums">{f.days}</span>
        <span className="text-[7px] font-semibold uppercase tracking-wide">días</span>
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-center gap-2">
          <span className="min-w-0 truncate">
            <CaseLink card={f.card} navigate={navigate} />
          </span>
          {product ? (
            <span
              className="hidden max-w-[14rem] shrink truncate rounded-full bg-brand-50 px-1.5 py-px text-[10.5px] leading-4 text-brand-700 sm:inline"
              data-testid="today-product"
              title={product}
            >
              {product}
            </span>
          ) : null}
        </div>
        <p className="truncate text-[11px] text-ink-muted" title={contactLine(f.card) ?? undefined}>
          <span className="tabular-nums">{quote}</span>
          {who ? ` · ${who}` : ""}
        </p>
      </div>
      <a
        href={write}
        target="_blank"
        rel="noopener noreferrer"
        title={thread ? "Abre el hilo de la cotización en Gmail: responde ahí y el seguimiento se registra solo" : "Abre un correo nuevo en Gmail"}
        aria-label={thread ? "Responder en Gmail" : "Nuevo correo en Gmail"}
        className="inline-flex h-8 w-9 shrink-0 items-center justify-center gap-1.5 rounded-md bg-brand-600 text-xs sm:w-auto sm:px-3 font-semibold text-white shadow-sm hover:bg-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 focus-visible:ring-offset-1"
      >
        <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
          <path d="M2.5 4h11v8h-11zM2.5 4.5 8 8.5l5.5-4" strokeLinejoin="round" />
        </svg>
        <span className="hidden sm:inline">{thread ? "Responder en Gmail" : "Nuevo correo"}</span>
      </a>
      {mayDecide ? <FollowUpMenu followUp={f} now={now} onChanged={onChanged} onLeave={leave} onClose={onClose} open={menuOpen} setOpen={setMenuOpen} /> : null}
    </li>
  );
}

/**
 * The row's quieter choices, behind «⋯»: «Recordar en 1 semana» (any row: writes a «Seguimiento …»
 * task a week out, cancelling the due one), «Ya le escribí» (a row a task put there) and «Cerrar
 * sin respuesta».
 */
function FollowUpMenu({
  followUp,
  now,
  onChanged,
  onLeave,
  onClose,
  open,
  setOpen,
}: {
  followUp: FollowUp;
  now: Date;
  onChanged: () => void;
  /** The row leaves at once after a recorded change; the reload confirms behind it. */
  onLeave?: () => void;
  onClose: (c: OpportunityCardData) => void;
  open: boolean;
  setOpen: (v: boolean | ((v: boolean) => boolean)) => void;
}) {
  const f = followUp;
  const [busy, setBusy] = useState<null | "done" | "snooze">(null);
  const keys = useRef({ done: newCaseCommandKey(), create: newCaseCommandKey(), cancel: newCaseCommandKey() });
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const away = (e: Event) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [open, setOpen]);

  async function done() {
    if (!f.task) return;
    setBusy("done");
    try {
      await completeTask({ task_id: f.task.task.task_id, task_version: f.task.task.version, note: "Hecho desde «Hoy»." }, keys.current.done);
      toast("Seguimiento hecho.");
      onLeave?.();
      setOpen(false);
      onChanged();
    } catch (err) {
      keys.current.done = newCaseCommandKey();
      toast(caseRefusalText(err), "bad");
    } finally {
      setBusy(null);
    }
  }

  async function snooze() {
    setBusy("snooze");
    const base = f.task ? Math.max(now.getTime(), Date.parse(f.task.task.due_at)) : now.getTime();
    const next = new Date(base + WEEK_MS);
    const quote = f.card.latest_revision?.quote_number ?? f.card.quote_numbers[0] ?? "";
    try {
      await createTask(
        {
          opportunity_id: f.card.opportunity_id,
          title: f.task?.task.title ?? `Seguimiento de ${quote}`.trim(),
          due_at: next.toISOString(),
          note: "Pospuesta una semana desde «Hoy».",
        },
        keys.current.create,
      );
      if (f.task) {
        await cancelTask({ task_id: f.task.task.task_id, task_version: f.task.task.version, note: `Pospuesta al ${fmtDate(next.toISOString())}.` }, keys.current.cancel);
      }
      toast(`Te lo recuerdo el ${fmtDate(next.toISOString())}.`);
      onLeave?.();
      setOpen(false);
      onChanged();
    } catch (err) {
      keys.current = { done: keys.current.done, create: newCaseCommandKey(), cancel: newCaseCommandKey() };
      toast(caseRefusalText(err), "bad");
      onChanged();
    } finally {
      setBusy(null);
    }
  }

  const item =
    "flex w-full items-center rounded px-2.5 py-1.5 text-left text-[12.5px] text-ink hover:bg-canvas-sunken focus:bg-canvas-sunken focus:outline-none disabled:opacity-50";
  return (
    <div ref={ref} className="relative shrink-0">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Más opciones"
        onClick={() => setOpen((v) => !v)}
        className="flex h-8 w-8 items-center justify-center rounded-md text-ink-muted hover:bg-canvas-sunken hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
      >
        <svg viewBox="0 0 16 16" className="h-4 w-4" fill="currentColor" aria-hidden="true">
          <circle cx="3.5" cy="8" r="1.3" />
          <circle cx="8" cy="8" r="1.3" />
          <circle cx="12.5" cy="8" r="1.3" />
        </svg>
      </button>
      {open ? (
        <div role="menu" className="absolute right-0 top-9 z-20 w-56 rounded-lg border border-line bg-canvas-raised p-1 shadow-lg">
          <button type="button" role="menuitem" className={item} disabled={busy !== null} onClick={() => void snooze()}>
            {busy === "snooze" ? "Guardando…" : "Recordar en 1 semana"}
          </button>
          {f.task ? (
            <button type="button" role="menuitem" className={item} disabled={busy !== null} onClick={() => void done()}>
              {busy === "done" ? "Guardando…" : "Ya le escribí"}
            </button>
          ) : null}
          <button
            type="button"
            role="menuitem"
            className={`${item} text-bad`}
            disabled={busy !== null}
            onClick={() => {
              setOpen(false);
              onClose(f.card);
            }}
          >
            Cerrar sin respuesta
          </button>
        </div>
      ) : null}
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────── aside ── */

/** Institutions shown before «Ver N más». */
const ORG_PREVIEW = 3;

function OrgsPanel({
  orgs,
  navigate,
  mayAuthor,
  onChanged,
}: {
  orgs: OrgToConfirm[];
  navigate: Navigate;
  mayAuthor: boolean;
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  if (orgs.length === 0) return null;
  const shown = open ? orgs : orgs.slice(0, ORG_PREVIEW);
  return (
    <Panel title="Instituciones por confirmar" aside={<Badge tone="warn" glyph={false}>{orgs.length}</Badge>} bodyClassName="divide-y divide-line">
      {shown.map((o) => (
        <OrgRow key={o.organization_id} org={o} navigate={navigate} mayAuthor={mayAuthor} onChanged={onChanged} />
      ))}
      {orgs.length > ORG_PREVIEW ? (
        <button type="button" onClick={() => setOpen(!open)} className="w-full px-3 py-2 text-left text-[12px] font-medium text-brand-700 hover:underline">
          {open ? "Ver menos" : `Ver ${orgs.length - ORG_PREVIEW} más`}
        </button>
      ) : null}
    </Panel>
  );
}

function OrgRow({ org, navigate, mayAuthor, onChanged }: { org: OrgToConfirm; navigate: Navigate; mayAuthor: boolean; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const { leaving, gone, leave } = useLeave(org);
  const key = useRef(newCaseCommandKey());
  const suggested = displayName(org.cases[0]).name;

  async function confirm() {
    if (org.version === null) return;
    setBusy(true);
    try {
      await confirmOrganizationRecord({ organization_id: org.organization_id, expected_version: org.version }, key.current);
      toast(`«${org.name}» confirmada.`);
      leave();
      onChanged();
    } catch (err) {
      key.current = newCaseCommandKey();
      toast(caseRefusalText(err), "bad");
    } finally {
      setBusy(false);
    }
  }

  if (gone) return null;
  return (
    <div className={`flex items-center gap-2 px-3 py-2 ${leaving ? "crm-row-out" : ""}`}>
      <div className="min-w-[12rem] flex-1">
        <button
          type="button"
          onClick={() => navigate("organizaciones", org.organization_id)}
          className="max-w-full truncate text-left text-[13px] font-medium text-ink hover:text-brand-700"
        >
          {org.name}
        </button>
        <p className="truncate text-[11px] text-ink-muted">
          {org.cases.length} {org.cases.length === 1 ? "caso" : "casos"}
          {suggested !== org.name ? ` · ¿${suggested}?` : ""}
        </p>
      </div>
      {mayAuthor && org.version !== null ? (
        <Button onClick={() => void confirm()} busy={busy} busyLabel="…">
          Confirmar
        </Button>
      ) : null}
    </div>
  );
}
