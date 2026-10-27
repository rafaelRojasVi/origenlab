/**
 * «Pendientes de la semana»: the shared to-do list at the top of Hoy's side column. Text is the
 * only thing a line needs — type, Enter. Urgent (★) lines first, then her own order (drag, or
 * «Subir» / «Bajar» in the «…» menu; dragging a line onto the other group flips its ★); an optional day (a past day stays visible, marked
 * «atrasada») and an optional case. Ticking a line strikes it with «Deshacer» for 5 s, then it
 * leaves for «Ver hechas».
 *
 * It owns its read: a slow or refused list (404 while the API's commands are off) stays inside
 * this box and never holds up «Para ti». Every write reloads the list; only the struck-through
 * «Deshacer» row is shown ahead of the server.
 */
import { useCallback, useEffect, useId, useRef, useState, type ClipboardEvent, type DragEvent, type KeyboardEvent, type ReactNode } from "react";
import { caseRefusalText, useMayRunCaseCommands } from "../caseCommands";
import type { CrmSection } from "../crmRoute";
import type { OpportunityCardData } from "../crmTypes";
import { Button, ConfirmDialog, DisabledAction, SearchInput, toast } from "../ui";
import { useResource } from "../useResource";
import {
  completeTodo,
  createTodo,
  deleteTodo,
  fetchTodos,
  moveTodo,
  reopenTodo,
  todoRefusal,
  updateTodo,
  type CreateTodoBody,
  type TodoItem,
  type UpdateTodoBody,
} from "./todoApi";
import { dayChoices, dueLabel, isLate, neighbourAbove, neighbourForMoveDown, santiagoToday, splitPasted } from "./todoOrder";

type Navigate = (s: CrmSection, id?: string | null) => void;
type Receipt = Record<string, unknown>;
/** Runs one write, reloads the list, and says what went wrong; null when it failed. */
type Write = (run: () => Promise<Receipt>) => Promise<Receipt | null>;

const UNDO_MS = 5000;
const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;
const DRAG_TYPE = "text/plain";
const CONFLICT_TEXT = "Alguien cambió esta lista; la actualicé.";
const VIEWER_REASON = "Tu perfil puede ver la lista, no cambiarla.";

/** A ticked line still on screen, struck through, while «Deshacer» is offered. */
interface Leaving {
  item: TodoItem;
  /** Where it stood, so it does not jump while it waits. */
  index: number;
  /** Its version after `complete-todo`; null while that write is in flight. */
  version: number | null;
}

/**
 * Says what went wrong with a write. Every `todo_*` refusal (stale version, a move target that is
 * gone or done, a line already ticked, reopened or deleted, a line that no longer exists) means
 * the list moved under her: one calm line, and the caller reloads. Anything else — `case_not_found`
 * («El caso ya no existe.»), a network error — keeps the shared case wording.
 */
const refusalToast = (err: unknown) => {
  if (todoRefusal(err)?.code?.startsWith("todo_")) toast(CONFLICT_TEXT, "warn");
  else toast(caseRefusalText(err), "bad");
};

const fullState = (t: TodoItem, change: Partial<CreateTodoBody> = {}): UpdateTodoBody => ({
  todo_id: t.todo_id,
  todo_version: t.version,
  body: t.body,
  is_urgent: t.is_urgent,
  due_on: t.due_on,
  opportunity_id: t.opportunity?.id ?? null,
  ...change,
});

/** Urgent first, the server's order kept within each group. */
const ordered = (open: TodoItem[]) => [...open.filter((t) => t.is_urgent), ...open.filter((t) => !t.is_urgent)];

export function PendientesPanel({ cases, navigate, now }: { cases: OpportunityCardData[]; navigate: Navigate; now?: Date }) {
  const [state, reload] = useResource(fetchTodos);
  const may = useMayRunCaseCommands();
  const at = now ?? new Date();
  const titleId = useId();
  const [leaving, setLeaving] = useState<Leaving[]>([]);
  const timers = useRef(new Map<string, number>());
  const [showDone, setShowDone] = useState(false);
  const [dragging, setDragging] = useState<{ id: string } | null>(null);
  const [over, setOver] = useState<{ id: string; before: boolean } | null>(null);

  useEffect(() => {
    const kept = timers.current;
    return () => kept.forEach((t) => window.clearTimeout(t));
  }, []);

  const write: Write = useCallback(
    async (run) => {
      try {
        const receipt = await run();
        reload();
        return receipt;
      } catch (err) {
        refusalToast(err);
        reload();
        return null;
      }
    },
    [reload],
  );

  const forget = useCallback((id: string) => {
    const t = timers.current.get(id);
    if (t !== undefined) window.clearTimeout(t);
    timers.current.delete(id);
    setLeaving((l) => l.filter((e) => e.item.todo_id !== id));
  }, []);

  const tick = (t: TodoItem, index: number) => {
    setLeaving((l) => [...l, { item: t, index, version: null }]);
    timers.current.set(t.todo_id, window.setTimeout(() => forget(t.todo_id), UNDO_MS));
    void write(() => completeTodo({ todo_id: t.todo_id, todo_version: t.version })).then((r) => {
      if (r === null) forget(t.todo_id);
      else setLeaving((l) => l.map((e) => (e.item.todo_id === t.todo_id ? { ...e, version: Number(r.todo_version) } : e)));
    });
  };

  const undo = (e: Leaving) => {
    if (e.version === null) return;
    forget(e.item.todo_id);
    void write(() => reopenTodo({ todo_id: e.item.todo_id, todo_version: e.version as number }));
  };

  const add = async (lines: string[]): Promise<boolean> => {
    try {
      for (const body of lines) await createTodo({ body, is_urgent: false, due_on: null, opportunity_id: null });
      return true;
    } catch (err) {
      refusalToast(err);
      return false;
    } finally {
      reload();
    }
  };

  if (state.kind === "loading") {
    return (
      <Shell titleId={titleId}>
        <div className="space-y-2 px-4 pb-4" aria-hidden="true">
          {[0, 1, 2].map((i) => (
            <div key={i} className="crm-skeleton h-9 rounded-md" />
          ))}
        </div>
      </Shell>
    );
  }
  if (state.kind !== "ready") {
    return (
      <Shell titleId={titleId}>
        <div className="px-3 pb-3">
          <div role="alert" className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-warn/30 bg-warn-bg px-3 py-2.5">
            <p className="flex min-w-0 flex-1 items-center gap-1.5 text-[13px] font-semibold text-warn">
              <span aria-hidden="true">⚠</span>
              <span>No pude cargar los pendientes.</span>
            </p>
            <button
              type="button"
              onClick={reload}
              className="h-7 rounded-md border border-warn/40 bg-canvas-raised px-3 text-xs font-medium text-ink transition-colors hover:border-warn active:translate-y-px"
            >
              Reintentar
            </button>
          </div>
        </div>
      </Shell>
    );
  }

  const open = ordered(state.data.open);
  const leavingIds = new Set(leaving.map((e) => e.item.todo_id));
  // The open list the server sent, with the ticked lines held where they stood.
  const shown: ({ kind: "open"; item: TodoItem } | { kind: "leaving"; entry: Leaving })[] = open
    .filter((t) => !leavingIds.has(t.todo_id))
    .map((item) => ({ kind: "open" as const, item }));
  for (const entry of [...leaving].sort((a, b) => a.index - b.index)) {
    shown.splice(Math.min(entry.index, shown.length), 0, { kind: "leaving", entry });
  }
  const done = state.data.done.filter((t) => !leavingIds.has(t.todo_id));
  // What a move may name: a ticked line awaiting the reload is still in `open` but is not movable
  // past (the server would refuse it as a target), so «Subir» / «Bajar» / a drop skip it.
  const movable = open.filter((t) => !leavingIds.has(t.todo_id));
  const count = movable.length;

  const drop = (e: DragEvent, target: TodoItem) => {
    e.preventDefault();
    const id = e.dataTransfer.getData(DRAG_TYPE) || dragging?.id;
    const before = over?.id === target.todo_id ? over.before : false;
    setDragging(null);
    setOver(null);
    const moving = movable.find((t) => t.todo_id === id);
    if (!moving || moving.todo_id === target.todo_id || !movable.some((t) => t.todo_id === target.todo_id)) return;
    // The group it lands in: its own, or — dropped on a row of the other group — that one.
    const rest = movable.filter((t) => t.is_urgent === target.is_urgent && t.todo_id !== moving.todo_id);
    const insertAt = rest.findIndex((t) => t.todo_id === target.todo_id) + (before ? 0 : 1);
    const after = insertAt === 0 ? null : rest[insertAt - 1].todo_id;
    if (moving.is_urgent !== target.is_urgent) {
      // Across groups the drop flips ★: the server puts it on top of the other group, then (unless
      // it was dropped on top) it moves after the row it was dropped under, at its new version.
      void write(async () => {
        const receipt = await updateTodo(fullState(moving, { is_urgent: target.is_urgent }));
        if (after === null) return receipt;
        return moveTodo({ todo_id: moving.todo_id, todo_version: Number(receipt.todo_version), after_todo_id: after });
      });
      return;
    }
    const group = movable.filter((t) => t.is_urgent === moving.is_urgent);
    const at0 = group.findIndex((t) => t.todo_id === moving.todo_id);
    const now0 = at0 === 0 ? null : group[at0 - 1].todo_id;
    if (after === now0) return; // dropped where it already is
    void write(() => moveTodo({ todo_id: moving.todo_id, todo_version: moving.version, after_todo_id: after }));
  };

  return (
    <Shell titleId={titleId} count={count}>
      <div className="px-3 pb-2">
        {may ? (
          <Composer onAdd={add} />
        ) : (
          <DisabledAction id={`${titleId}-viewer`} reason={VIEWER_REASON}>
            + Agregar pendiente
          </DisabledAction>
        )}
      </div>
      {shown.length === 0 ? (
        <p className="border-t border-line px-4 py-4 text-[12.5px] text-ink-muted">Nada pendiente. Escribe lo que tengas que hacer esta semana.</p>
      ) : (
        <ul className="divide-y divide-line border-t border-line" aria-labelledby={titleId}>
          {shown.map((s, index) =>
            s.kind === "leaving" ? (
              <LeavingRow key={s.entry.item.todo_id} entry={s.entry} onUndo={() => undo(s.entry)} />
            ) : (
              <TodoRow
                key={s.item.todo_id}
                t={s.item}
                may={may}
                now={at}
                cases={cases}
                navigate={navigate}
                write={write}
                onTick={() => tick(s.item, index)}
                up={movable.filter((o) => o.is_urgent === s.item.is_urgent)[0]?.todo_id === s.item.todo_id ? undefined : neighbourAbove(movable, s.item.todo_id)}
                down={neighbourForMoveDown(movable, s.item.todo_id) ?? undefined}
                drag={
                  may
                    ? {
                        start: () => setDragging({ id: s.item.todo_id }),
                        end: () => {
                          setDragging(null);
                          setOver(null);
                        },
                        // Any other open row: its own group reorders, the other group also flips ★.
                        accepts: dragging !== null && dragging.id !== s.item.todo_id,
                        over: over?.id === s.item.todo_id ? (over.before ? "before" : "after") : null,
                        setOver: (before) => setOver({ id: s.item.todo_id, before }),
                        leave: () => setOver((o) => (o?.id === s.item.todo_id ? null : o)),
                        drop: (e) => drop(e, s.item),
                        isDragging: dragging?.id === s.item.todo_id,
                      }
                    : null
                }
              />
            ),
          )}
        </ul>
      )}
      {done.length > 0 ? (
        <div className="border-t border-line">
          <button
            type="button"
            aria-expanded={showDone}
            onClick={() => setShowDone((v) => !v)}
            className="flex h-10 w-full items-center gap-1.5 px-4 text-left text-[12px] font-medium text-ink-muted hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-600"
          >
            <span aria-hidden="true" className={`inline-block transition-transform ${showDone ? "rotate-90" : ""}`}>›</span>
            Ver hechas ({done.length})
          </button>
          {showDone ? (
            <ul className="divide-y divide-line border-t border-line" aria-label="Hechas">
              {done.map((t) => (
                <li key={t.todo_id} className="flex min-h-11 items-center gap-1 px-2" data-testid="todo-done-row">
                  <Check
                    checked
                    disabled={!may}
                    label={`Volver a abrir «${t.body}»`}
                    onChange={() => void write(() => reopenTodo({ todo_id: t.todo_id, todo_version: t.version }))}
                  />
                  <span className="min-w-0 flex-1 break-words py-2 text-[13px] leading-5 text-ink-faint line-through">{t.body}</span>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </Shell>
  );
}

function Shell({ titleId, count, children }: { titleId: string; count?: number; children: ReactNode }) {
  return (
    <section
      aria-labelledby={titleId}
      data-testid="pendientes-panel"
      className="crm-rise relative z-10 rounded-xl border border-line bg-canvas-raised shadow-[0_1px_2px_rgb(28_25_23/0.04)]"
    >
      <header className="flex items-baseline gap-2 px-4 pb-2 pt-3.5">
        <h2 id={titleId} className="text-[13px] font-semibold text-ink">
          Pendientes de la semana
        </h2>
        {count !== undefined && count > 0 ? <span className="text-[11px] tabular-nums text-ink-faint">{count}</span> : null}
      </header>
      {children}
    </section>
  );
}

/* ───────────────────────────────────────────────────────── the new line ── */

function Composer({ onAdd }: { onAdd: (lines: string[]) => Promise<boolean> }) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  // A ref, not state: two Enters in the same tick must see the first one's lock.
  const adding = useRef(false);
  // Lists pasted while a create is in flight: added, in order, once it finishes (never dropped).
  const queued = useRef<string[]>([]);
  const hintId = useId();

  const send = async (lines: string[], clear: boolean) => {
    if (adding.current || lines.length === 0) return;
    adding.current = true;
    setBusy(true);
    const ok = await onAdd(lines);
    if (ok && clear) setValue("");
    while (queued.current.length > 0) await onAdd(queued.current.splice(0));
    adding.current = false;
    setBusy(false);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key !== "Enter" || e.nativeEvent.isComposing) return;
    e.preventDefault();
    const text = value.trim();
    if (text) void send([text], true);
  };

  // A pasted list becomes one line each; a single pasted line just goes into the field.
  const onPaste = (e: ClipboardEvent<HTMLInputElement>) => {
    const lines = splitPasted(e.clipboardData.getData("text"));
    if (lines.length < 2) return;
    e.preventDefault();
    if (adding.current) queued.current.push(...lines);
    else void send(lines, false);
  };

  return (
    <div>
      <input
        type="text"
        aria-label="Nuevo pendiente"
        aria-describedby={hintId}
        aria-busy={busy || undefined}
        value={value}
        maxLength={500}
        placeholder="+ Escribe un pendiente…"
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={onKeyDown}
        onPaste={onPaste}
        className="h-11 w-full rounded-lg border border-line bg-canvas px-3 text-[13px] text-ink placeholder:text-ink-faint focus:border-brand-600 focus:outline-none focus:ring-1 focus:ring-brand-600"
      />
      <p id={hintId} className="mt-1 px-0.5 text-[11px] text-ink-faint">
        {busy ? "Agregando…" : "Enter agrega. Si pegas una lista, cada línea queda como un pendiente."}
      </p>
    </div>
  );
}

/* ──────────────────────────────────────────────────────────────── rows ── */

function Check({ checked, disabled, label, onChange }: { checked: boolean; disabled: boolean; label: string; onChange: () => void }) {
  return (
    <label className={`flex h-9 w-8 shrink-0 items-center justify-center ${disabled ? "" : "cursor-pointer"}`}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        aria-label={label}
        onChange={onChange}
        className="h-4 w-4 cursor-pointer accent-[var(--color-accent)] disabled:cursor-not-allowed"
      />
    </label>
  );
}

function LeavingRow({ entry, onUndo }: { entry: Leaving; onUndo: () => void }) {
  return (
    <li className="flex min-h-11 items-center gap-1 bg-canvas-sunken/50 px-2" data-testid="todo-leaving-row" aria-live="polite">
      <span aria-hidden="true" className="w-4 shrink-0" />
      <Check checked disabled label={`«${entry.item.body}» hecha`} onChange={() => {}} />
      <span className="min-w-0 flex-1 break-words py-2 text-[13px] leading-5 text-ink-faint line-through">{entry.item.body}</span>
      <Button variant="quiet" onClick={onUndo} disabled={entry.version === null}>
        Deshacer
      </Button>
    </li>
  );
}

interface RowDrag {
  start: () => void;
  end: () => void;
  accepts: boolean;
  over: "before" | "after" | null;
  setOver: (before: boolean) => void;
  leave: () => void;
  drop: (e: DragEvent) => void;
  isDragging: boolean;
}

const caseLabel = (c: OpportunityCardData): string => {
  const quote = c.latest_revision?.quote_number ?? c.quote_numbers[0] ?? null;
  return [quote, c.organization?.name].filter(Boolean).join(" · ") || c.title;
};

function TodoRow({
  t,
  may,
  now,
  cases,
  navigate,
  write,
  onTick,
  up,
  down,
  drag,
}: {
  t: TodoItem;
  may: boolean;
  now: Date;
  cases: OpportunityCardData[];
  navigate: Navigate;
  write: Write;
  onTick: () => void;
  /** The `after_todo_id` of «Subir» (null = to the top); undefined when it is already first. */
  up: string | null | undefined;
  /** The `after_todo_id` of «Bajar»; undefined when it is already last. */
  down: string | undefined;
  drag: RowDrag | null;
}) {
  const [editing, setEditing] = useState(false);
  // Enter, Esc and the blur that follows them must end one edit once.
  const editingRef = useRef(false);
  const [draft, setDraft] = useState(t.body);
  const [menu, setMenu] = useState<null | "main" | "day" | "case">(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const late = isLate(t.due_on, now);
  const save = (change: Partial<CreateTodoBody>) => {
    setBusy(true);
    void write(() => updateTodo(fullState(t, change))).finally(() => setBusy(false));
  };

  const finishEdit = (keep: boolean) => {
    if (!editingRef.current) return;
    editingRef.current = false;
    setEditing(false);
    const body = draft.trim();
    if (keep && body && body !== t.body) save({ body });
  };

  const move = (after: string | null) => {
    setMenu(null);
    void write(() => moveTodo({ todo_id: t.todo_id, todo_version: t.version, after_todo_id: after }));
  };

  return (
    <li
      data-testid="todo-row"
      draggable={drag != null && !editing && menu === null}
      onDragStart={
        drag
          ? (e) => {
              e.dataTransfer.setData(DRAG_TYPE, t.todo_id);
              e.dataTransfer.effectAllowed = "move";
              drag.start();
            }
          : undefined
      }
      onDragEnd={drag?.end}
      onDragOver={
        drag
          ? (e) => {
              if (!drag.accepts) return;
              e.preventDefault();
              e.dataTransfer.dropEffect = "move";
              const box = e.currentTarget.getBoundingClientRect();
              drag.setOver(e.clientY < box.top + box.height / 2);
            }
          : undefined
      }
      onDragLeave={drag ? (e) => !e.currentTarget.contains(e.relatedTarget as Node | null) && drag.leave() : undefined}
      onDrop={drag?.accepts ? drag.drop : undefined}
      className={`group relative flex min-h-11 items-start gap-0.5 px-2 py-1 transition-opacity ${drag?.isDragging ? "opacity-40" : ""} ${
        drag?.over === "before" ? "shadow-[inset_0_2px_0_var(--color-accent)]" : drag?.over === "after" ? "shadow-[inset_0_-2px_0_var(--color-accent)]" : ""
      }`}
    >
      <span
        aria-hidden="true"
        title={drag ? "Arrastra para ordenar" : undefined}
        className={`flex h-9 w-4 shrink-0 select-none items-center justify-center text-[11px] leading-none tracking-[-0.15em] text-ink-faint ${
          drag ? "cursor-grab opacity-40 group-hover:opacity-100 active:cursor-grabbing" : "opacity-0"
        }`}
      >
        ⋮⋮
      </span>
      <Check checked={false} disabled={!may} label={`Marcar «${t.body}» como hecha`} onChange={onTick} />
      <button
        type="button"
        aria-label="Urgente"
        aria-pressed={t.is_urgent}
        disabled={!may || busy}
        onClick={() => save({ is_urgent: !t.is_urgent })}
        className={`flex h-9 w-8 shrink-0 items-center justify-center rounded-md text-[15px] leading-none transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 disabled:cursor-default ${
          t.is_urgent ? "text-warn" : "text-ink-faint enabled:hover:text-warn"
        } ${!t.is_urgent && !may ? "opacity-0" : ""}`}
      >
        <span aria-hidden="true">{t.is_urgent ? "★" : "☆"}</span>
      </button>
      <div className="min-w-0 flex-1 py-1.5">
        {editing ? (
          <input
            type="text"
            aria-label="Editar pendiente"
            autoFocus
            value={draft}
            maxLength={500}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.nativeEvent.isComposing) {
                e.preventDefault();
                finishEdit(true);
              } else if (e.key === "Escape") {
                e.preventDefault();
                finishEdit(false);
              }
            }}
            onBlur={() => finishEdit(true)}
            className="-my-1 h-8 w-full rounded-md border border-brand-600 bg-canvas px-2 text-[13px] text-ink focus:outline-none focus:ring-1 focus:ring-brand-600"
          />
        ) : may ? (
          <button
            type="button"
            title="Clic para editar"
            onClick={() => {
              setDraft(t.body);
              editingRef.current = true;
              setEditing(true);
            }}
            className={`block w-full whitespace-pre-wrap break-words rounded text-left text-[13px] leading-5 text-ink hover:text-accent-strong focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 ${t.is_urgent ? "font-medium" : ""}`}
          >
            {t.body}
          </button>
        ) : (
          <p className={`whitespace-pre-wrap break-words text-[13px] leading-5 text-ink ${t.is_urgent ? "font-medium" : ""}`}>{t.body}</p>
        )}
        {t.due_on || t.opportunity ? (
          <div className="mt-1 flex flex-wrap items-center gap-1">
            {t.due_on ? (
              <span
                data-testid="todo-day"
                className={`inline-flex h-5 items-center gap-1 rounded-full border px-1.5 text-[11px] font-medium tabular-nums ${
                  late ? "border-bad/30 bg-bad-bg text-bad" : "border-line-strong bg-canvas-sunken text-ink-muted"
                }`}
              >
                {dueLabel(t.due_on, now)}
                {late ? <span>· atrasada</span> : null}
              </span>
            ) : null}
            {t.opportunity ? (
              <button
                type="button"
                onClick={() => navigate("oportunidades", t.opportunity!.id)}
                title={`Abrir el caso «${t.opportunity.title}»`}
                className="inline-flex h-5 max-w-full items-center gap-1 rounded-full border border-brand-600/30 bg-brand-50 px-1.5 text-[11px] font-medium text-accent hover:border-brand-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
              >
                <span className="truncate">{t.opportunity.quote_number ?? t.opportunity.title}</span>
                <span aria-hidden="true">↗</span>
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
      {may ? (
        <RowMenu
          t={t}
          now={now}
          cases={cases}
          mode={menu}
          setMode={setMenu}
          onDay={(due_on) => {
            setMenu(null);
            save({ due_on });
          }}
          onCase={(opportunity_id) => {
            setMenu(null);
            save({ opportunity_id });
          }}
          up={up}
          down={down}
          onMove={move}
          onDelete={() => {
            setMenu(null);
            setConfirming(true);
          }}
        />
      ) : null}
      {confirming ? (
        <ConfirmDialog
          title="Eliminar pendiente"
          lines={[`Se borra «${t.body}» de la lista de todos.`, "Si solo lo terminaste, mejor márcalo como hecho."]}
          confirmLabel="Eliminar"
          busy={busy}
          onCancel={() => setConfirming(false)}
          onConfirm={() => {
            setBusy(true);
            void write(() => deleteTodo({ todo_id: t.todo_id, todo_version: t.version })).finally(() => {
              setBusy(false);
              setConfirming(false);
            });
          }}
        />
      ) : null}
    </li>
  );
}

/* ─────────────────────────────────────────────────────────── the «…» menu ── */

const MENU_BASE = "block w-full px-3 py-2 text-left text-[13px] hover:bg-canvas-sunken focus:bg-canvas-sunken focus:outline-none disabled:cursor-not-allowed disabled:text-ink-faint disabled:hover:bg-transparent";
const MENU_ITEM = `${MENU_BASE} text-ink`;

function RowMenu({
  t,
  now,
  cases,
  mode,
  setMode,
  onDay,
  onCase,
  up,
  down,
  onMove,
  onDelete,
}: {
  t: TodoItem;
  now: Date;
  cases: OpportunityCardData[];
  mode: null | "main" | "day" | "case";
  setMode: (m: null | "main" | "day" | "case") => void;
  onDay: (due_on: string | null) => void;
  onCase: (opportunity_id: string | null) => void;
  up: string | null | undefined;
  down: string | undefined;
  onMove: (after: string | null) => void;
  onDelete: () => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    if (mode === null) return;
    const outside = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setMode(null);
    };
    const esc = (e: globalThis.KeyboardEvent) => e.key === "Escape" && setMode(null);
    document.addEventListener("mousedown", outside);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", outside);
      document.removeEventListener("keydown", esc);
    };
  }, [mode, setMode]);

  const q = query.trim().toLowerCase();
  const matches = (q
    ? cases.filter((c) =>
        [c.title, c.organization?.name ?? "", ...c.quote_numbers, c.latest_revision?.quote_number ?? ""].some((s) => s.toLowerCase().includes(q)),
      )
    : cases
  ).slice(0, 6);

  return (
    <div ref={box} className="relative shrink-0">
      <button
        type="button"
        aria-label={`Más acciones de «${t.body}»`}
        aria-haspopup="menu"
        aria-expanded={mode !== null}
        onClick={() => setMode(mode === null ? "main" : null)}
        className="flex h-9 w-8 items-center justify-center rounded-md text-lg leading-none text-ink-muted opacity-60 transition-opacity hover:bg-canvas-sunken hover:text-ink hover:opacity-100 focus:outline-none focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-brand-600 group-hover:opacity-100 aria-expanded:opacity-100"
      >
        ⋯
      </button>
      {mode === "main" ? (
        <div role="menu" aria-label="Acciones del pendiente" className="absolute right-0 top-full z-20 mt-1 w-48 rounded-lg border border-line bg-canvas-raised py-1 shadow-lg">
          <button type="button" role="menuitem" className={MENU_ITEM} onClick={() => setMode("day")}>
            Día…
          </button>
          {t.opportunity ? (
            <button type="button" role="menuitem" className={MENU_ITEM} onClick={() => onCase(null)}>
              Quitar caso
            </button>
          ) : (
            <button type="button" role="menuitem" className={MENU_ITEM} onClick={() => setMode("case")}>
              Vincular caso…
            </button>
          )}
          <div className="my-1 border-t border-line" />
          <button type="button" role="menuitem" className={MENU_ITEM} disabled={up === undefined} onClick={() => onMove(up ?? null)}>
            Subir
          </button>
          <button type="button" role="menuitem" className={MENU_ITEM} disabled={down === undefined} onClick={() => down !== undefined && onMove(down)}>
            Bajar
          </button>
          <div className="my-1 border-t border-line" />
          <button type="button" role="menuitem" className={`${MENU_BASE} text-bad hover:bg-bad-bg`} onClick={onDelete}>
            Eliminar
          </button>
        </div>
      ) : null}
      {mode === "day" ? (
        <div role="dialog" aria-label="Día del pendiente" className="absolute right-0 top-full z-20 mt-1 w-56 space-y-2 rounded-lg border border-line bg-canvas-raised p-2.5 shadow-lg">
          <div className="flex flex-wrap gap-1.5" role="group" aria-label="Día">
            {dayChoices(now).map((d) => (
              <Button key={d.value} variant={t.due_on === d.value ? "primary" : undefined} aria-pressed={t.due_on === d.value} onClick={() => onDay(d.value)}>
                {d.label}
              </Button>
            ))}
          </div>
          <input
            type="date"
            aria-label="Otro día"
            min={santiagoToday(now)}
            max="9999-12-31"
            defaultValue={t.due_on ?? ""}
            // Typing a year emits each partial value (0002-…, 0202-…, and a fifth digit, 20261-…, which
            // compares above any day as a string): only a four-digit-year day from today on saves.
            onChange={(e) => ISO_DAY.test(e.target.value) && e.target.value >= santiagoToday(now) && onDay(e.target.value)}
            className="h-8 w-full rounded-md border border-line bg-canvas px-2 text-[12.5px] tabular-nums text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
          />
          {t.due_on ? (
            <Button variant="quiet" className="w-full" onClick={() => onDay(null)}>
              Sin día
            </Button>
          ) : null}
        </div>
      ) : null}
      {mode === "case" ? (
        <div role="dialog" aria-label="Vincular caso" className="absolute right-0 top-full z-20 mt-1 w-64 space-y-2 rounded-lg border border-line bg-canvas-raised p-2.5 shadow-lg">
          <SearchInput value={query} onChange={setQuery} placeholder="Número, institución o título…" label="Buscar caso" />
          {matches.length === 0 ? (
            <p className="px-1 text-[12px] text-ink-muted">Ningún caso coincide.</p>
          ) : (
            <ul className="max-h-60 overflow-y-auto">
              {matches.map((c) => (
                <li key={c.opportunity_id}>
                  <button type="button" className={`${MENU_ITEM} rounded-md px-2`} onClick={() => onCase(c.opportunity_id)}>
                    <span className="block truncate">{caseLabel(c)}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </div>
  );
}
