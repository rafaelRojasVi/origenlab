/**
 * The body of «Hoy» (`OverviewPage`): the lists `today.ts` computes from the pipeline, each row
 * with the one action it needs.
 *
 * - **Tareas de hoy** — «Hecho» completes the task; «+1 semana» writes the same task a week later
 *   and cancels this one.
 * - **Te toca responder** — the client's email, and «Pasar a Conversación» when the case is still
 *   «Enviada».
 * - **Seguimientos** (3 · 14 · 30 days) — the case's email to answer on; at 30 days, «Cerrar sin
 *   respuesta» opens the «Perdida» form with that reason chosen.
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
import { contactLine, displayName } from "../caseDisplay";
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
        <Stat label="tareas para hoy" value={tasks.length} tone="text-ink" />
        <Stat label="te toca responder" value={replies.length} tone="text-warn" />
        <Stat label="seguimientos" value={followUps.length} tone="text-brand-700" />
        <Stat label="por decidir" value={historical} tone="text-info" />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_21rem]">
        <div className="min-w-0 space-y-4">
          <TasksPanel tasks={tasks} navigate={navigate} mayDecide={mayDecide} onChanged={onChanged} now={at} />
          <RepliesPanel replies={replies} navigate={navigate} mayDecide={mayDecide} onChanged={onChanged} />
          <FollowUpsPanel followUps={followUps} navigate={navigate} mayDecide={mayDecide} onClose={setClosing} />
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
          <PeoplePanel mayAuthor={mayAuthor} navigate={navigate} />
          {blocked.length > 0 ? (
            <Panel title="Bloqueados" aside={<Badge tone="bad">{blocked.length}</Badge>} bodyClassName="divide-y divide-line">
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
    <Panel title="Tareas de hoy" aside={<Badge tone={tasks.length ? "warn" : "good"}>{tasks.length}</Badge>} bodyClassName="divide-y divide-line">
      {tasks.length === 0 ? (
        <p className="px-4 py-4 text-xs text-ink-muted" data-testid="today-no-tasks">
          Nada vence hoy. Las tareas nacen de «Decidir casos», de «En pausa hasta…» y de los seguimientos.
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

  return (
    <div
      className="crm-rise flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-2.5"
      style={{ "--i": Math.min(index, 10) } as CSSProperties}
      data-testid={`today-task-${task.task_id}`}
    >
      <div className="min-w-0 flex-1">
        <p className="text-[13px] text-ink">{task.title}</p>
        <p className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-[11px] text-ink-muted">
          <span className={overdueDays > 0 ? "font-medium text-bad" : "font-medium text-warn"}>
            {overdueDays === 0 ? "Vence hoy" : overdueDays === 1 ? "Atrasada 1 día" : `Atrasada ${overdueDays} días`}
          </span>
          <span aria-hidden="true">·</span>
          <CaseLink card={card} navigate={navigate} />
        </p>
      </div>
      {mayDecide ? (
        <div className="flex shrink-0 gap-2">
          <Button onClick={() => void snooze()} busy={busy === "snooze"} busyLabel="Posponiendo…" disabled={busy !== null}>
            +1 semana
          </Button>
          <Button variant="primary" onClick={() => void done()} busy={busy === "done"} busyLabel="Guardando…" disabled={busy !== null}>
            Hecho
          </Button>
        </div>
      ) : null}
    </div>
  );
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
    <Panel title="Te toca responder" aside={<Badge tone={replies.length ? "warn" : "good"}>{replies.length}</Badge>} bodyClassName="divide-y divide-line">
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
      <div className="min-w-0 flex-1">
        <CaseLink card={card} navigate={navigate} />
        <p className="mt-0.5 truncate text-[11px] text-ink-muted">
          <span className="font-medium text-warn">Respondió el {fmtDate(reply.at)}</span> · {quoteOf(card)}
          {contactLine(card) ? ` · ${contactLine(card)}` : ""}
        </p>
      </div>
      <div className="flex shrink-0 gap-2">
        {reply.url ? (
          <a
            href={reply.url}
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

function FollowUpsPanel({
  followUps,
  navigate,
  mayDecide,
  onClose,
}: {
  followUps: FollowUp[];
  navigate: Navigate;
  mayDecide: boolean;
  onClose: (c: OpportunityCardData) => void;
}) {
  return (
    <section className="crm-rise overflow-hidden rounded-xl border border-line bg-canvas-raised" data-testid="today-followups">
      <header className="flex flex-wrap items-baseline gap-x-2 border-b border-line px-4 py-2.5">
        <h2 className="text-[14px] font-semibold text-ink">Seguimientos</h2>
        <Badge tone={followUps.length ? "info" : "good"}>{followUps.length}</Badge>
        <span className="ml-auto text-[11px] text-ink-faint">Día 3 · 14 · 30. Un correo tuyo en el hilo reinicia el conteo.</span>
      </header>
      {followUps.length === 0 ? (
        <p className="px-4 py-4 text-xs text-ink-muted">Ningún seguimiento pendiente.</p>
      ) : (
        RHYTHM.map((r) => {
          const rows = followUps.filter((f) => f.rhythm === r.key);
          if (rows.length === 0) return null;
          return (
            <div key={r.key} data-testid={`today-rhythm-${r.key}`}>
              <p className="flex items-center gap-2 bg-canvas-sunken/60 px-4 py-1 text-[10px] font-semibold uppercase tracking-wider text-ink-faint">
                {r.label} <span className="tabular-nums">{rows.length}</span>
                <span className="font-normal normal-case tracking-normal">· {r.hint}</span>
              </p>
              <ul className="divide-y divide-line">
                {rows.map((f) => (
                  <li key={f.card.opportunity_id} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-2.5">
                    <span
                      className={`flex h-10 w-10 shrink-0 flex-col items-center justify-center rounded-lg leading-none ${
                        f.rhythm === "cerrar" ? "bg-bad-bg text-bad" : f.rhythm === "segundo" ? "bg-warn-bg text-warn" : "bg-info-bg text-info"
                      }`}
                      title={f.byEmail ? "Días desde tu último correo" : "Días desde la cotización"}
                    >
                      <span className="text-sm font-bold tabular-nums">{f.days}</span>
                      <span className="text-[8px] font-semibold uppercase">días</span>
                    </span>
                    <div className="min-w-0 flex-1">
                      <CaseLink card={f.card} navigate={navigate} />
                      <p className="truncate text-[11px] text-ink-muted">
                        {quoteOf(f.card)}
                        {f.byEmail ? " · desde tu último correo" : ""}
                        {contactLine(f.card) ? ` · ${contactLine(f.card)}` : ""}
                      </p>
                    </div>
                    <div className="flex shrink-0 gap-2">
                      {f.card.latest_revision?.gmail?.url ? (
                        <a
                          href={f.card.latest_revision.gmail.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex h-8 items-center rounded-md border border-line bg-canvas-raised px-3 text-xs font-medium text-ink hover:border-line-strong"
                        >
                          Escribir ↗
                        </a>
                      ) : null}
                      {f.rhythm === "cerrar" && mayDecide ? (
                        <Button variant="danger" onClick={() => onClose(f.card)}>
                          Cerrar sin respuesta
                        </Button>
                      ) : null}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          );
        })
      )}
    </section>
  );
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
    <Panel title="Instituciones por confirmar" aside={<Badge tone="warn">{orgs.length}</Badge>} bodyClassName="divide-y divide-line">
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
      <div className="min-w-0 flex-1">
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
    <Panel title="Personas por agregar" aside={state.kind === "ready" ? <Badge>{state.data.total}</Badge> : null}>
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
