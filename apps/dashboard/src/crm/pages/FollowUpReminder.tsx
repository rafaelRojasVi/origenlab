/**
 * «Recordarme…»: remind me to follow this case up (owner, 2026-10-12). One note — what
 * to do — and one day. It writes the note (when there is one) and a «Seguimiento de <cotización>»
 * task due that day, cancelling the case's earlier follow-up reminder, so the case shows in
 * «Hoy → Seguimientos» on that day with the note under it, and stays off the list until then.
 *
 * Nothing here counts as a contact with the client: only an email does (`lastTouch`).
 */
import { useRef, useState, type FormEvent } from "react";
import { addNote } from "../authoring/crmAuthoringApi";
import { caseRefusalText, cancelTask, createTask, newCaseCommandKey } from "../caseCommands";
import type { OpportunityCardData } from "../crmTypes";
import { isFollowUpTask } from "../today";
import { Button, TextareaInput, fmtDate, toast } from "../ui";

const DAY_MS = 86_400_000;

/** The quick choices; the date field covers any other day. */
const QUICK: { label: string; days: number }[] = [
  { label: "Hoy", days: 0 },
  { label: "Mañana", days: 1 },
  { label: "En 3 días", days: 3 },
  { label: "En 1 semana", days: 7 },
];

/** `YYYY-MM-DD` of the browser's day `days` from `now`. */
export function dayInput(now: Date, days: number): string {
  const d = new Date(now.getTime() + days * DAY_MS);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** The reminder's instant: now for today (due at once), else 09:00 local on that day. */
export function reminderDue(day: string, now: Date): Date {
  const [y, m, d] = day.split("-").map(Number);
  const at = new Date(y, m - 1, d, 9, 0, 0, 0);
  return dayInput(now, 0) === day || at.getTime() < now.getTime() ? now : at;
}

export function FollowUpReminder({
  card,
  onDone,
  onCancel,
  now = new Date(),
}: {
  card: OpportunityCardData;
  onDone: () => void;
  onCancel: () => void;
  now?: Date;
}) {
  const [text, setText] = useState("");
  const [day, setDay] = useState(dayInput(now, 0));
  const [busy, setBusy] = useState(false);
  const keys = useRef({ note: newCaseCommandKey(), task: newCaseCommandKey(), cancel: newCaseCommandKey() });
  const quote = card.latest_revision?.quote_number ?? card.quote_numbers[0] ?? "";
  const earlier = (card.open_tasks ?? []).filter(isFollowUpTask);

  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    const due = reminderDue(day, now);
    const body = text.trim();
    try {
      if (body) {
        await addNote({ subject_kind: "opportunity", subject_id: card.opportunity_id, body }, keys.current.note);
      }
      await createTask(
        {
          opportunity_id: card.opportunity_id,
          title: `Seguimiento de ${quote}`.trim(),
          due_at: due.toISOString(),
          note: body || "Recordatorio desde «Recordarme…».",
        },
        keys.current.task,
      );
      for (const t of earlier) {
        await cancelTask({ task_id: t.task_id, task_version: t.version, note: `Reemplazado por el recordatorio del ${fmtDate(due.toISOString())}.` }, keys.current.cancel);
      }
      toast(day === dayInput(now, 0) ? "Listo: aparece hoy en Seguimientos." : `Listo: te lo recuerdo el ${fmtDate(due.toISOString())}.`);
      onDone();
    } catch (err) {
      keys.current = { note: newCaseCommandKey(), task: newCaseCommandKey(), cancel: newCaseCommandKey() };
      toast(caseRefusalText(err), "bad");
      onDone();
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={save} className="space-y-3 rounded-lg border border-line bg-canvas-raised p-3" data-testid="followup-reminder">
      <div className="space-y-1">
        <label htmlFor="followup-text" className="text-[12.5px] font-medium text-ink">
          ¿Qué tienes que hacer?
        </label>
        <TextareaInput id="followup-text" value={text} onChange={setText} rows={2} maxLength={2000} placeholder="Ej.: llamar a Gustavo, mandar la ficha técnica…" />
      </div>
      <div className="space-y-1.5">
        <span className="text-[12.5px] font-medium text-ink">¿Cuándo?</span>
        <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="¿Cuándo?">
          {QUICK.map((q) => {
            const value = dayInput(now, q.days);
            return (
              <Button key={q.label} variant={day === value ? "primary" : undefined} aria-pressed={day === value} onClick={() => setDay(value)}>
                {q.label}
              </Button>
            );
          })}
          <input
            type="date"
            aria-label="Otro día"
            min={dayInput(now, 0)}
            value={day}
            onChange={(e) => e.target.value && setDay(e.target.value)}
            className="h-8 rounded-md border border-line bg-canvas px-2 text-[12.5px] tabular-nums text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
          />
        </div>
      </div>
      <p className="text-[11.5px] text-ink-muted">
        Ese día el caso aparece en <strong>Hoy → Seguimientos</strong> con tu nota; hasta entonces no sale.
        {earlier.length > 0 ? " Reemplaza el recordatorio anterior." : ""}
      </p>
      <div className="flex gap-2">
        <Button type="submit" variant="primary" busy={busy} busyLabel="Guardando…">
          Guardar recordatorio
        </Button>
        <Button onClick={onCancel} disabled={busy}>
          Cancelar
        </Button>
      </div>
    </form>
  );
}
