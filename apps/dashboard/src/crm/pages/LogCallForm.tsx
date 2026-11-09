/**
 * «Registrar llamada»: one phone call, recorded as a typed note on the case (owner, 2026-11-07).
 * How it ended is required; what comes next and a few words are optional; a reminder for the next
 * call can ride along. The outcome and the next step travel as fields of the note, never as body
 * text, so calls can be counted by outcome; a note needs a body, so with none written the call
 * says what happened («Llamada: Contestó»).
 *
 * Nothing here counts as a contact with the client: only an email does (`lastTouch`).
 */
import { useId, useRef, useState, type FormEvent } from "react";
import { addNote } from "../authoring/crmAuthoringApi";
import { CALL_NEXT_STEP_MAX, CALL_OUTCOMES, CALL_OUTCOME_LABEL, buildCallNote, type CallOutcome } from "../callOutcome";
import { caseRefusalText, newCaseCommandKey } from "../caseCommands";
import type { OpportunityCardData } from "../crmTypes";
import { Button, FormField, SelectInput, TextareaInput, toast } from "../ui";
import { FollowUpDay, dayInput, followUpSavedText, newFollowUpKeys, scheduleFollowUp } from "./FollowUpReminder";

const OUTCOME_OPTIONS = CALL_OUTCOMES.map((value) => ({ value, label: CALL_OUTCOME_LABEL[value] }));

export function LogCallForm({
  card,
  canRemind,
  onDone,
  onCancel,
  now = new Date(),
}: {
  card: OpportunityCardData;
  /** The case takes a dated reminder (it is open and the role may run case commands). */
  canRemind: boolean;
  /** The call was recorded (or recorded in part): refetch. */
  onDone: () => void;
  onCancel: () => void;
  now?: Date;
}) {
  const ids = { outcome: useId(), next: useId(), text: useId(), remind: useId() };
  const [outcome, setOutcome] = useState<CallOutcome | "">("");
  const [nextStep, setNextStep] = useState("");
  const [text, setText] = useState("");
  const [remind, setRemind] = useState(false);
  const [day, setDay] = useState(dayInput(now, 3));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // One key per distinct request: a retry of the same call replays, an edited one is a new request.
  const noteKey = useRef<{ sig: string; key: string }>({ sig: "", key: newCaseCommandKey() });
  const followKeys = useRef(newFollowUpKeys());

  async function save(e: FormEvent) {
    e.preventDefault();
    if (!outcome || busy) return;
    const body = buildCallNote(card.opportunity_id, { outcome, nextStep, text });
    const sig = JSON.stringify(body);
    if (noteKey.current.sig !== sig) noteKey.current = { sig, key: newCaseCommandKey() };
    setBusy(true);
    setError(null);
    try {
      await addNote(body, noteKey.current.key);
    } catch (err) {
      setError(caseRefusalText(err));
      setBusy(false);
      return;
    }
    try {
      if (canRemind && remind) {
        const due = await scheduleFollowUp({
          card,
          day,
          note: body.call_next_step ?? "Recordatorio desde «Registrar llamada».",
          now,
          keys: followKeys.current,
        });
        toast(`Llamada registrada. ${followUpSavedText(day, due, now)}`);
      } else {
        toast("Llamada registrada.");
      }
    } catch (err) {
      toast(`La llamada quedó registrada, pero el recordatorio no: ${caseRefusalText(err)}`, "bad");
    }
    setBusy(false);
    onDone();
  }

  return (
    <form onSubmit={save} className="space-y-3 rounded-lg border border-line bg-canvas-raised p-3" data-testid="log-call">
      <FormField label="¿Cómo terminó la llamada?" htmlFor={ids.outcome} required>
        <SelectInput
          id={ids.outcome}
          value={outcome}
          onChange={(v) => setOutcome(v as CallOutcome | "")}
          options={OUTCOME_OPTIONS}
          placeholder="Elige un resultado"
          required
          disabled={busy}
        />
      </FormField>
      <FormField label="Siguiente paso" htmlFor={ids.next} hint="Opcional: qué quedó pendiente después de la llamada.">
        <TextareaInput
          id={ids.next}
          value={nextStep}
          onChange={setNextStep}
          rows={2}
          maxLength={CALL_NEXT_STEP_MAX}
          placeholder="Ej.: mandar la ficha técnica el jueves"
          disabled={busy}
        />
      </FormField>
      <FormField label="Notas de la llamada" htmlFor={ids.text} hint="Opcional. Sin notas queda «Llamada: …» con el resultado.">
        <TextareaInput id={ids.text} value={text} onChange={setText} rows={2} maxLength={8000} disabled={busy} />
      </FormField>
      {canRemind ? (
        <div className="space-y-2">
          <label htmlFor={ids.remind} className="flex items-center gap-2 text-[12.5px] font-medium text-ink">
            <input
              id={ids.remind}
              type="checkbox"
              checked={remind}
              onChange={(e) => setRemind(e.target.checked)}
              disabled={busy}
              className="h-4 w-4 accent-brand-600 focus-visible:ring-2 focus-visible:ring-brand-600"
            />
            Recordarme volver a llamar
          </label>
          {remind ? <FollowUpDay day={day} onChange={setDay} now={now} /> : null}
        </div>
      ) : null}
      {error ? (
        <p role="alert" className="text-[11px] text-bad">
          {error}
        </p>
      ) : null}
      <div className="flex gap-2">
        <Button type="submit" variant="primary" disabled={!outcome} busy={busy} busyLabel="Guardando…">
          Registrar llamada
        </Button>
        <Button onClick={onCancel} disabled={busy}>
          Cancelar
        </Button>
      </div>
    </form>
  );
}
