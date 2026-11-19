/**
 * «Llamé»: the inline row under a Seguimiento case. One click per outcome, one per next contact, an
 * optional note. Saving records the call (`add-note`, `note_kind: "llamada"`: every call its own
 * note, so three tries count three) and then the «Seguimiento de …» reminder for the next contact,
 * replacing the earlier one. A retry of the same call reuses its keys, so it never counts twice.
 * «¿Cuándo fue?» logs a call that happened earlier (`occurred_at`, Santiago wall time); the
 * reminder is still counted from the moment of saving.
 *
 * What is typed lives in `segStore`, keyed by the case: a refetch, a receipt, a re-sort or a
 * re-mount of the list never closes the form or loses the note.
 */
import { useId, useRef, useState, type FormEvent } from "react";
import { addNote } from "../authoring/crmAuthoringApi";
import { CALL_OUTCOMES, CALL_OUTCOME_LABEL, buildCallNote, type CallOutcome } from "../callOutcome";
import { caseRefusalText, newCaseCommandKey } from "../caseCommands";
import { personLabel } from "../caseDisplay";
import { dateSantiago, santiagoHour, santiagoInstant } from "../goals";
import { addDays } from "../prospects/santiagoDate";
import type { SegRow } from "../seguimiento";
import { useSegUi, type CallDraft, type NextKey, type WhenKey } from "../segStore";
import { Button, ChoiceChips, fmtDate } from "../ui";
import { dayInput, scheduleFollowUp } from "./FollowUpReminder";

const LATER_MS = 2 * 3_600_000;

const DATE_TIME_INPUT =
  "h-11 rounded-md border border-line-strong bg-canvas-raised px-2 text-[13px] tabular-nums text-ink focus:border-brand-600 focus:outline-none focus:ring-1 focus:ring-brand-600 aria-[invalid=true]:border-bad sm:h-8 sm:text-[12.5px]";

/** The next-contact choices: the chip, the toast's wording and the day from today. */
const NEXTS: { key: Exclude<NextKey, "date">; label: string; said: string; days: number }[] = [
  { key: "later", label: "Otra vez hoy (en 2 h)", said: "Hoy en 2 h", days: 0 },
  { key: "tomorrow", label: "Mañana", said: "Mañana", days: 1 },
  { key: "3d", label: "En 3 días", said: "En 3 días", days: 3 },
  { key: "week", label: "En 1 semana", said: "En 1 semana", days: 7 },
];
const OTHER_NEXT = "Otro día…";

/** No answer or voicemail: try again in a while today. */
const RETRY_TODAY: ReadonlySet<CallOutcome> = new Set(["no_contesto", "buzon"]);

/** «¿Cuándo fue?»: now (no `occurred_at`), or earlier today, yesterday, another day, at a time. */
const WHENS: { key: WhenKey; label: string }[] = [
  { key: "now", label: "Ahora" },
  { key: "today", label: "Hoy, más temprano" },
  { key: "yesterday", label: "Ayer" },
  { key: "other", label: "Otro día" },
];
/** A call that happened yesterday or before: «otra vez hoy (en 2 h)» means nothing for it. */
const BACKDATED: ReadonlySet<WhenKey> = new Set(["yesterday", "other"]);

/** How far back a call may be dated (the API refuses older). */
export const BACKDATE_DAYS = 60;

const OUTCOME_BY_LABEL = new Map(CALL_OUTCOMES.map((o) => [CALL_OUTCOME_LABEL[o], o]));
const NEXT_BY_LABEL = new Map(NEXTS.map((n) => [n.label, n]));
const WHEN_BY_LABEL = new Map(WHENS.map((w) => [w.label, w.key]));

const freshKeys = (): CallDraft["keys"] => ({ sig: "", at: new Date(0).toISOString(), note: newCaseCommandKey(), task: newCaseCommandKey(), cancel: newCaseCommandKey() });

/** A new form's state, as of `opened`. */
export function newCallDraft(opened: Date): CallDraft {
  const today = dateSantiago(opened);
  return {
    outcome: "contesto",
    next: "tomorrow",
    nextDay: addDays(today, 1),
    nextTouched: false,
    text: "",
    when: "now",
    time: santiagoHour(opened),
    otherDay: addDays(today, -1),
    openedAt: opened.toISOString(),
    keys: freshKeys(),
  };
}

/** Why the form cannot be saved as it stands, or null. Shown under the form; «Guardar» waits. */
export function callDraftProblem(d: CallDraft, now: Date): string | null {
  const today = dateSantiago(new Date(d.openedAt));
  if (d.when === "other" && !d.otherDay) return "Elige el día de la llamada.";
  if (d.when !== "now" && !d.time) return "Escribe la hora de la llamada.";
  const at = occurredAt(d);
  if (at && Date.parse(at) > now.getTime()) return "Esa hora todavía no llega: elige una anterior.";
  if (at && Date.parse(at) < now.getTime() - BACKDATE_DAYS * 86_400_000) {
    return `Solo se registran llamadas de los últimos ${BACKDATE_DAYS} días: elige un día desde el ${fmtDate(addDays(today, -BACKDATE_DAYS))}.`;
  }
  if (d.next === "date" && (!d.nextDay || d.nextDay <= today)) return "Elige un día desde mañana para el próximo contacto.";
  return null;
}

/** When the call happened, as ISO 8601; null for «Ahora» (or an incomplete date, see `callDraftProblem`). */
function occurredAt(d: CallDraft): string | null {
  if (d.when === "now" || !d.time) return null;
  const today = dateSantiago(new Date(d.openedAt));
  const day = d.when === "today" ? today : d.when === "yesterday" ? addDays(today, -1) : d.otherDay;
  return day ? santiagoInstant(day, d.time) : null;
}

interface CallFormProps {
  row: SegRow;
  /** The real time at save (the page's «now» is as of its last read, maybe hours ago). */
  clock?: () => Date;
  /** The call and its reminder were recorded; gets the next contact as said in the toast. */
  onSaved: (nextSaid: string) => void;
  onCancel: () => void;
}

/** The call row under a sheet row. */
export function SeguimientoCallRow({ colSpan, ...props }: CallFormProps & { colSpan: number }) {
  return (
    <tr data-testid={`seg-call-${props.row.card.opportunity_id}`}>
      <td colSpan={colSpan} className="border-b border-brand-100 bg-brand-50 px-4 py-3.5">
        <CallForm {...props} />
      </td>
    </tr>
  );
}

/** «Llamé» as a form of its own (Agenda, Por encargado and the cards put it in a card). */
export function CallForm({ row, clock = () => new Date(), onSaved, onCancel, className = "" }: CallFormProps & { className?: string }) {
  const card = row.card;
  const id = card.opportunity_id;
  const noteId = useId();
  const [ui, act] = useSegUi();
  // Until the first change the form shows a draft of its own; from then on the store keeps it.
  const fresh = useRef<CallDraft | null>(null);
  fresh.current ??= newCallDraft(clock());
  const d = ui.drafts[id] ?? fresh.current;
  const set = (patch: Partial<CallDraft>) => act.setDraft(id, { ...d, ...patch });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const today = dateSantiago(new Date(d.openedAt));
  const name = personLabel(card.contact?.name);
  const problem = callDraftProblem(d, clock());

  function pickOutcome(o: CallOutcome) {
    // No answer: try again in a while today — unless the call was yesterday or before, or the
    // person already chose the next contact.
    const retry = RETRY_TODAY.has(o) && !BACKDATED.has(d.when) && !d.nextTouched;
    set({ outcome: o, ...(retry ? { next: "later" as const } : {}) });
  }

  function pickWhen(w: WhenKey) {
    setError(null);
    // A back-dated call never keeps «otra vez hoy (en 2 h)» as its default: tomorrow instead.
    set({ when: w, ...(BACKDATED.has(w) && d.next === "later" ? { next: "tomorrow" as const } : {}) });
  }

  async function save(e: FormEvent) {
    e.preventDefault();
    if (busy || problem) return;
    const choice = NEXTS.find((n) => n.key === d.next);
    // `occurred_at` is part of the body, so a retry replays it under the same keys.
    const body = buildCallNote(id, { outcome: d.outcome as CallOutcome, nextStep: "", text: d.text, occurredAt: occurredAt(d) });
    const sig = JSON.stringify([body, d.next, d.nextDay]);
    // One set of keys per distinct request, with the time it was first sent: a retry of the same call
    // replays the very same bodies (same due date), an edited one is a new request at a new time.
    let keys = d.keys;
    if (keys.sig !== sig) {
      keys = { ...freshKeys(), sig, at: clock().toISOString() };
      act.setDraft(id, { ...d, keys });
    }
    const now = new Date(keys.at);
    setBusy(true);
    setError(null);
    try {
      await addNote(body, keys.note);
      const due = await scheduleFollowUp({
        card,
        day: d.next === "date" ? d.nextDay : dayInput(now, choice?.days ?? 1),
        note: body.body,
        now,
        keys: { task: keys.task, cancel: keys.cancel },
        at: d.next === "later" ? new Date(now.getTime() + LATER_MS) : undefined,
      });
      act.dropDraft(id);
      onSaved(choice?.said ?? fmtDate(due.toISOString()));
    } catch (err) {
      setError(caseRefusalText(err));
    } finally {
      setBusy(false);
    }
  }

  const fieldLabel = "text-[11px] font-bold text-ink-muted";
  const message = error ?? problem;
  const nextLabel = d.next === "date" ? OTHER_NEXT : NEXTS.find((n) => n.key === d.next)!.label;
  return (
    <form onSubmit={save} className={`seg-call-form flex flex-wrap items-end gap-x-7 gap-y-4 motion-safe:animate-fade-in-up ${className}`} data-testid="seg-call-form">
      <div className="flex flex-col gap-1.5">
        <span className={fieldLabel}>¿Cuándo fue?</span>
        <div className="flex flex-wrap items-center gap-2">
          <ChoiceChips label="¿Cuándo fue?" options={WHENS.map((w) => w.label)} value={WHENS.find((w) => w.key === d.when)!.label} onChange={(v) => pickWhen(WHEN_BY_LABEL.get(v)!)} />
          {d.when === "other" ? (
            <input
              type="date"
              aria-label="Día de la llamada"
              aria-invalid={problem !== null && problem.includes("día de la llamada") ? true : undefined}
              min={addDays(today, -BACKDATE_DAYS)}
              max={today}
              value={d.otherDay}
              onChange={(e) => set({ otherDay: e.target.value })}
              disabled={busy}
              className={DATE_TIME_INPUT}
            />
          ) : null}
          {d.when !== "now" ? (
            <input type="time" aria-label="Hora de la llamada" value={d.time} onChange={(e) => set({ time: e.target.value })} disabled={busy} className={DATE_TIME_INPUT} />
          ) : null}
        </div>
      </div>
      <div className="flex flex-col gap-1.5">
        <span className={fieldLabel}>¿Cómo terminó la llamada {name ? `a ${name}` : "al contacto"}?</span>
        <ChoiceChips
          label="Resultado de la llamada"
          options={CALL_OUTCOMES.map((o) => CALL_OUTCOME_LABEL[o])}
          value={CALL_OUTCOME_LABEL[d.outcome as CallOutcome]}
          onChange={(v) => pickOutcome(OUTCOME_BY_LABEL.get(v)!)}
        />
      </div>
      <div className="flex flex-col gap-1.5">
        <span className={fieldLabel}>Próximo contacto</span>
        <div className="flex flex-wrap items-center gap-2">
          <ChoiceChips
            label="Próximo contacto"
            options={[...NEXTS.filter((n) => n.key !== "later" || !BACKDATED.has(d.when)).map((n) => n.label), OTHER_NEXT]}
            value={nextLabel}
            onChange={(v) => set({ next: v === OTHER_NEXT ? "date" : NEXT_BY_LABEL.get(v)!.key, nextTouched: true })}
          />
          {d.next === "date" ? (
            <input
              type="date"
              aria-label="Día del próximo contacto"
              min={addDays(today, 1)}
              value={d.nextDay}
              onChange={(e) => set({ nextDay: e.target.value })}
              disabled={busy}
              className={DATE_TIME_INPUT}
            />
          ) : null}
        </div>
      </div>
      <div className="flex min-w-0 flex-col gap-1.5">
        <label htmlFor={noteId} className={fieldLabel}>
          Nota (opcional)
        </label>
        <input
          id={noteId}
          name="nota"
          autoComplete="off"
          value={d.text}
          maxLength={8000}
          onChange={(e) => set({ text: e.target.value })}
          placeholder="Ej.: la orden de compra sale el viernes…"
          disabled={busy}
          className="h-11 w-full min-w-0 rounded-md border border-line-strong bg-canvas-raised px-2.5 text-[14px] text-ink placeholder:text-ink-faint focus:border-brand-600 focus:outline-none focus:ring-1 focus:ring-brand-600 sm:h-9 sm:min-w-60 sm:text-[13px]"
        />
      </div>
      <div className="flex items-center gap-2">
        <Button
          className="max-sm:h-11"
          onClick={() => {
            act.dropDraft(id);
            onCancel();
          }}
          disabled={busy}
        >
          Cancelar
        </Button>
        <Button type="submit" variant="primary" className="max-sm:h-11" disabled={problem !== null} busy={busy} busyLabel="Guardando…">
          Guardar llamada
        </Button>
      </div>
      {message ? (
        <p role="alert" className="basis-full text-[12.5px] font-medium text-bad">
          {message}
        </p>
      ) : null}
    </form>
  );
}
