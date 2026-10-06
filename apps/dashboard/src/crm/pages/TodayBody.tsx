/**
 * The body of «Hoy» (`OverviewPage`): the lists `today.ts` computes from the pipeline, each row
 * with the one action it needs.
 *
 * - **Te toca responder** — first: a client is waiting. The client's email, and «Pasar a
 *   Conversación» when the case is still «Enviada».
 * - **Seguimientos** — one list, a traffic light by the 3 · 14 · 30-day rhythm (green, yellow,
 *   red), each row with what the quote is for and the email to write on. A row a «Seguimiento …»
 *   task put there carries «Hecho» (completes it) and «+1 semana» (writes it a week later and
 *   cancels this one); a red row offers «Cerrar sin respuesta», the «Perdida» form with that reason.
 * - **Otras tareas** — the due tasks that are not follow-ups («Retomar: …»), same two buttons.
 * - Aside: the historical cases still to decide, the institutions to confirm, the people the quote
 *   emails name, and the blocked cases.
 *
 * Writes go through the same commands as the drawer (`caseCommands.ts`, `crmAuthoringApi.ts`),
 * gated the same way; a viewer sees the lists without the buttons. Every recorded write refetches.
 */
import { useMemo, useRef, useState, type CSSProperties } from "react";
import { confirmOrganizationRecord, fetchPersonSuggestions } from "../authoring/crmAuthoringApi";
import { PersonSuggestionList } from "../authoring/PersonSuggestionList";
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
import type { CrmSection } from "../crmRoute";
import type { OpportunityCardData } from "../crmTypes";
import {
  RHYTHM,
  followUpsDue,
  historicalCount,
  organizationsToConfirm,
  repliesToAnswer,
  tasksDue,
  type DueTask,
  type FollowUp,
  type OrgToConfirm,
  type Reply,
} from "../today";
import { Badge, Button, Modal, Panel, ResourceGate, Skeleton, fmtDate, toast } from "../ui";
import { useResource } from "../useResource";
import { CaseMoveForm } from "./CaseMove";

type Navigate = (s: CrmSection, id?: string) => void;

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

function Stat({ label, value, tone }: { label: string; value: number; tone: string }) {
  return (
    <div className="min-w-[6.5rem] rounded-lg border border-line bg-canvas-raised px-3 py-2">
      <div className={`text-xl font-semibold tabular-nums ${value ? tone : "text-ink-faint"}`}>{value}</div>
      <div className="text-[11px] text-ink-muted">{label}</div>
    </div>
  );
}

export function TodayBody({
  items,
  navigate,
  onChanged,
  refreshing = false,
  now,
}: {
  items: OpportunityCardData[];
  navigate: Navigate;
  onChanged: () => void;
  refreshing?: boolean;
  now?: Date;
}) {
  const at = useMemo(() => now ?? new Date(), [now]);
  const tasks = useMemo(() => tasksDue(items, at), [items, at]);
  const replies = useMemo(() => repliesToAnswer(items, at), [items, at]);
  const followUps = useMemo(() => followUpsDue(items, at), [items, at]);
  const orgs = useMemo(() => organizationsToConfirm(items), [items]);
  const historical = historicalCount(items);
  const blocked = items.filter((i) => i.status === "blocked");
  const mayDecide = useMayRunCaseCommands();
  const mayAuthor = useMayAuthorCrm();
  const [closing, setClosing] = useState<OpportunityCardData | null>(null);

  return (
    <div className="space-y-4" aria-busy={refreshing || undefined}>
      <div className="flex flex-wrap gap-2" data-testid="today-stats">
        <Stat label="te toca responder" value={replies.length} tone="text-warn" />
        <Stat label="seguimientos" value={followUps.length} tone="text-brand-700" />
        <Stat label="otras tareas" value={tasks.length} tone="text-ink" />
        <Stat label="por decidir" value={historical} tone="text-info" />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_21rem]">
        <div className="min-w-0 space-y-4">
          <RepliesPanel replies={replies} navigate={navigate} mayDecide={mayDecide} onChanged={onChanged} />
          <FollowUpsPanel followUps={followUps} navigate={navigate} mayDecide={mayDecide} onChanged={onChanged} onClose={setClosing} now={at} />
          <TasksPanel tasks={tasks} navigate={navigate} mayDecide={mayDecide} onChanged={onChanged} now={at} />
          <PeoplePanel mayAuthor={mayAuthor} navigate={navigate} />
        </div>
        <aside className="min-w-0 space-y-4">
          {historical > 0 ? (
            <section className="crm-rise rounded-xl border border-info/30 bg-info-bg/40 p-4" data-testid="today-historical">
              <h2 className="text-[14px] font-semibold text-ink">
                {historical} {historical === 1 ? "caso" : "casos"} por decidir
              </h2>
              <p className="mt-1 text-xs leading-5 text-ink-muted">
                Siguen como «Enviada · histórico»: nadie ha dicho si siguen vivos. Cada uno trae una propuesta a partir del correo.
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
                <div key={c.opportunity_id} className="px-3 py-2">
                  <CaseLink card={c} navigate={navigate} />
                  <span className="block truncate text-[11px] text-bad">{c.attention.find((a) => a.blocking)?.label}</span>
                </div>
              ))}
            </Panel>
          ) : null}
          <button
            type="button"
            onClick={() => navigate("revision")}
            className="w-full rounded-lg border border-dashed border-line px-3 py-3 text-left transition-colors hover:border-line-strong hover:bg-canvas-raised"
          >
            <span className="block text-[13px] font-medium text-ink">Acciones del correo y estado de los datos</span>
            <span className="mt-0.5 block text-[11px] text-ink-muted">Órdenes de compra y respuestas que leyó el sistema, en Revisión.</span>
          </button>
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

function TasksPanel({
  tasks,
  navigate,
  mayDecide,
  onChanged,
  now,
}: {
  tasks: DueTask[];
  navigate: Navigate;
  mayDecide: boolean;
  onChanged: () => void;
  now: Date;
}) {
  return (
    <Panel title="Otras tareas de hoy" aside={<Badge tone={tasks.length ? "warn" : "good"} glyph={false}>{tasks.length}</Badge>} bodyClassName="divide-y divide-line">
      {tasks.length === 0 ? (
        <p className="px-4 py-3 text-xs text-ink-muted" data-testid="today-no-tasks">
          Nada más vence hoy. Los seguimientos programados están arriba, en «Seguimientos».
        </p>
      ) : (
        tasks.map((t, i) => <TaskRow key={t.task.task_id} due={t} index={i} navigate={navigate} mayDecide={mayDecide} onChanged={onChanged} now={now} />)
      )}
    </Panel>
  );
}

function TaskRow({
  due,
  index,
  navigate,
  mayDecide,
  onChanged,
  now,
}: {
  due: DueTask;
  index: number;
  navigate: Navigate;
  mayDecide: boolean;
  onChanged: () => void;
  now: Date;
}) {
  const { card, task, overdueDays } = due;
  const actions = useTaskActions(due, now, onChanged);

  return (
    <div
      className="crm-rise flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-2.5"
      style={{ "--i": Math.min(index, 10) } as CSSProperties}
      data-testid={`today-task-${task.task_id}`}
    >
      <div className="min-w-[12rem] flex-1">
        <p className="text-[13px] text-ink">{task.title}</p>
        <p className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-[11px] text-ink-muted">
          <span className={overdueDays > 0 ? "font-medium text-bad" : "font-medium text-warn"}>
            {dueText(overdueDays)}
          </span>
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
function useTaskActions(due: DueTask, now: Date, onChanged: () => void) {
  const { card, task } = due;
  const [busy, setBusy] = useState<null | "done" | "snooze">(null);
  const keys = useRef({ done: newCaseCommandKey(), create: newCaseCommandKey(), cancel: newCaseCommandKey() });

  async function done() {
    setBusy("done");
    try {
      await completeTask({ task_id: task.task_id, task_version: task.version, note: "Hecho desde «Hoy»." }, keys.current.done);
      toast("Tarea hecha.");
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
}: {
  replies: Reply[];
  navigate: Navigate;
  mayDecide: boolean;
  onChanged: () => void;
}) {
  return (
    <Panel title="Te toca responder" aside={<Badge tone={replies.length ? "warn" : "good"} glyph={false}>{replies.length}</Badge>} bodyClassName="divide-y divide-line">
      {replies.length === 0 ? (
        <p className="px-4 py-4 text-xs text-ink-muted">Ningún cliente espera respuesta.</p>
      ) : (
        replies.map((r, i) => <ReplyRow key={r.card.opportunity_id} reply={r} index={i} navigate={navigate} mayDecide={mayDecide} onChanged={onChanged} />)
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
}: {
  reply: Reply;
  index: number;
  navigate: Navigate;
  mayDecide: boolean;
  onChanged: () => void;
}) {
  const { card } = reply;
  const [busy, setBusy] = useState(false);
  const keys = useRef([newCaseCommandKey()]);
  const canMove = mayDecide && card.stage === "quoting" && typeof card.version === "number";

  async function toConversation() {
    setBusy(true);
    try {
      await moveCase(
        { opportunity_id: card.opportunity_id, stage: card.stage, version: card.version as number },
        "negotiating",
        `El cliente respondió el ${fmtDate(reply.at)}.`,
        null,
        keys.current,
      );
      toast("Pasó a «Conversación».");
      onChanged();
    } catch (err) {
      keys.current = [newCaseCommandKey()];
      toast(caseRefusalText(err), "bad");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="crm-rise flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-2.5" style={{ "--i": Math.min(index, 10) } as CSSProperties}>
      <div className="min-w-[12rem] flex-1">
        <CaseLink card={card} navigate={navigate} />
        <p className="mt-0.5 truncate text-[11px] text-ink-muted">
          <span className="font-medium text-warn">Respondió el {fmtDate(reply.at)}</span> · {quoteOf(card)}
          {contactLine(card) ? ` · ${contactLine(card)}` : ""}
        </p>
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
        {canMove ? (
          <Button variant="primary" onClick={() => void toConversation()} busy={busy} busyLabel="Moviendo…">
            Pasar a Conversación
          </Button>
        ) : null}
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────── follow-ups ── */

const LIGHT = {
  good: { bar: "border-l-good", chip: "bg-good-bg text-good", head: "bg-good-bg/50 text-good", dot: "bg-good" },
  warn: { bar: "border-l-warn", chip: "bg-warn-bg text-warn", head: "bg-warn-bg/50 text-warn", dot: "bg-warn" },
  bad: { bar: "border-l-bad", chip: "bg-bad-bg text-bad", head: "bg-bad-bg/50 text-bad", dot: "bg-bad" },
} as const;

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
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line px-4 py-2.5">
        <h2 className="text-[14px] font-semibold text-ink">Seguimientos</h2>
        <Badge tone={followUps.length ? "info" : "good"} glyph={false}>{followUps.length}</Badge>
        <span className="ml-auto flex flex-wrap items-center gap-x-3 text-[11px] text-ink-faint">
          {RHYTHM.map((r) => (
            <span key={r.key} className="inline-flex items-center gap-1">
              <span aria-hidden="true" className={`h-2 w-2 rounded-full ${LIGHT[r.tone].dot}`} />
              día {r.from}+
            </span>
          ))}
          <span>· un correo tuyo reinicia el conteo</span>
        </span>
      </header>
      {followUps.length === 0 ? (
        <p className="px-4 py-4 text-xs text-ink-muted">Ningún seguimiento pendiente.</p>
      ) : (
        RHYTHM.map((r) => {
          const rows = followUps.filter((f) => f.rhythm === r.key);
          if (rows.length === 0) return null;
          return (
            <div key={r.key} data-testid={`today-rhythm-${r.key}`}>
              <p className={`flex items-center gap-2 px-4 py-1 text-[10px] font-semibold uppercase tracking-wider ${LIGHT[r.tone].head}`}>
                <span aria-hidden="true" className={`h-2 w-2 rounded-full ${LIGHT[r.tone].dot}`} />
                {r.label} <span className="tabular-nums">{rows.length}</span>
                <span className="font-normal normal-case tracking-normal text-ink-muted">· {r.hint}</span>
              </p>
              <ul className="divide-y divide-line">
                {rows.map((f, i) => (
                  <FollowUpRow
                    key={f.card.opportunity_id}
                    followUp={f}
                    tone={r.tone}
                    index={i}
                    navigate={navigate}
                    mayDecide={mayDecide}
                    onChanged={onChanged}
                    onClose={onClose}
                    now={now}
                  />
                ))}
              </ul>
            </div>
          );
        })
      )}
    </section>
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
  const contact = contactLine(f.card);
  // Reply on the case's own thread: the email stays in it, so the system sees the follow-up and
  // restarts the count. With no thread to reply on, a new email from the shared mailbox.
  const thread = inSharedMailbox(f.card.last_contact?.outbound?.url ?? f.card.latest_revision?.gmail?.url);
  const quote = f.card.latest_revision?.quote_number ?? f.card.quote_numbers[0] ?? "";
  const to = f.card.contact?.address && !isMaskedAddress(f.card.contact.address) ? f.card.contact.address : null;
  const write = thread ?? composeInSharedMailbox(to, `Seguimiento cotización N° ${quote}`.trim());
  return (
    <li
      className={`crm-rise flex flex-wrap items-center gap-x-3 gap-y-1.5 border-l-4 px-4 py-2.5 ${LIGHT[tone].bar}`}
      style={{ "--i": Math.min(index, 10) } as CSSProperties}
      data-testid={f.task ? `today-task-${f.task.task.task_id}` : `today-followup-${f.card.opportunity_id}`}
    >
      <span
        className={`flex h-10 w-10 shrink-0 flex-col items-center justify-center rounded-lg leading-none ${LIGHT[tone].chip}`}
        title={f.byEmail ? "Días desde tu último correo" : "Días desde la cotización"}
      >
        <span className="text-sm font-bold tabular-nums">{f.days}</span>
        <span className="text-[8px] font-semibold uppercase">días</span>
      </span>
      <div className="min-w-[12rem] flex-1">
        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
          <CaseLink card={f.card} navigate={navigate} />
          {product ? (
            <span className="max-w-[16rem] truncate rounded-full border border-brand-600/25 bg-brand-50 px-1.5 py-px text-[10.5px] leading-4 text-brand-700" data-testid="today-product">
              {product}
            </span>
          ) : null}
        </div>
        <p className="truncate text-[11px] text-ink-muted">
          {f.task ? <span className={f.task.overdueDays > 0 ? "font-medium text-bad" : "font-medium text-warn"}>Programado · {dueText(f.task.overdueDays)} · </span> : null}
          {quoteOf(f.card)}
          {f.byEmail ? " · desde tu último correo" : ""}
          {contact ? ` · ${contact}` : ""}
        </p>
      </div>
      <div className="flex shrink-0 flex-wrap gap-2">
        <a
          href={write}
          target="_blank"
          rel="noopener noreferrer"
          title={thread ? "Abre el hilo de la cotización en Gmail: responde ahí y el seguimiento se registra solo" : "Abre un correo nuevo en Gmail"}
          className="inline-flex h-8 items-center gap-1.5 rounded-md border border-brand-600/40 bg-brand-50 px-3 text-xs font-semibold text-brand-700 hover:border-brand-600"
        >
          <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
            <path d="M2.5 4h11v8h-11zM2.5 4.5 8 8.5l5.5-4" strokeLinejoin="round" />
          </svg>
          {thread ? "Responder en Gmail ↗" : "Nuevo correo ↗"}
        </a>
        {f.task && mayDecide ? <TaskRowButtons due={f.task} now={now} onChanged={onChanged} /> : null}
        {f.rhythm === "cerrar" && mayDecide ? (
          <Button variant="danger" onClick={() => onClose(f.card)}>
            Cerrar sin respuesta
          </Button>
        ) : null}
      </div>
    </li>
  );
}

function TaskRowButtons({ due, now, onChanged }: { due: DueTask; now: Date; onChanged: () => void }) {
  const actions = useTaskActions(due, now, onChanged);
  return <TaskButtons actions={actions} />;
}

/* ─────────────────────────────────────────────────────────────── aside ── */

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
  if (orgs.length === 0) return null;
  return (
    <Panel title="Instituciones por confirmar" aside={<Badge tone="warn" glyph={false}>{orgs.length}</Badge>} bodyClassName="divide-y divide-line">
      <p className="px-3 py-2 text-[11px] text-ink-faint">Las propuso una regla del correo; nadie las ha revisado.</p>
      {orgs.map((o) => (
        <OrgRow key={o.organization_id} org={o} navigate={navigate} mayAuthor={mayAuthor} onChanged={onChanged} />
      ))}
    </Panel>
  );
}

function OrgRow({ org, navigate, mayAuthor, onChanged }: { org: OrgToConfirm; navigate: Navigate; mayAuthor: boolean; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const key = useRef(newCaseCommandKey());
  const suggested = displayName(org.cases[0]).name;

  async function confirm() {
    if (org.version === null) return;
    setBusy(true);
    try {
      await confirmOrganizationRecord({ organization_id: org.organization_id, expected_version: org.version }, key.current);
      toast(`«${org.name}» confirmada.`);
      onChanged();
    } catch (err) {
      key.current = newCaseCommandKey();
      toast(caseRefusalText(err), "bad");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex items-center gap-2 px-3 py-2">
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

function PeoplePanel({ mayAuthor, navigate }: { mayAuthor: boolean; navigate: Navigate }) {
  const [state, reload] = useResource(fetchPersonSuggestions);
  return (
    <Panel title="Personas por agregar" aside={state.kind === "ready" ? <Badge glyph={false}>{state.data.total}</Badge> : null}>
      <div className="p-3">
        <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={2} />}>
          {(data) => (
            <>
              <PersonSuggestionList items={data.items.slice(0, 5)} mayAuthor={mayAuthor} showOrganization onCreated={reload} />
              {data.items.length > 5 ? (
                <button type="button" onClick={() => navigate("personas")} className="mt-2 text-[11px] text-brand-700 hover:underline">
                  Ver las {data.items.length}
                </button>
              ) : null}
            </>
          )}
        </ResourceGate>
      </div>
    </Panel>
  );
}
