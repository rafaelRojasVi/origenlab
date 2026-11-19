/**
 * «Llamé»: the inline row under a Seguimiento case. One click per outcome, one per next contact, an
 * optional note. Saving records the call (`add-note`, `note_kind: "llamada"`: every call its own
 * note, so three tries count three) and then the «Seguimiento de …» reminder for the next contact,
 * replacing the earlier one. A retry of the same call reuses its keys, so it never counts twice.
 */
import { useId, useRef, useState, type FormEvent } from "react";
import { addNote } from "../authoring/crmAuthoringApi";
import { CALL_OUTCOMES, CALL_OUTCOME_LABEL, buildCallNote, type CallOutcome } from "../callOutcome";
import { caseRefusalText, newCaseCommandKey } from "../caseCommands";
import { personLabel } from "../caseDisplay";
import type { SegRow } from "../seguimiento";
import { Button, ChoiceChips } from "../ui";
import { dayInput, newFollowUpKeys, scheduleFollowUp } from "./FollowUpReminder";

type NextKey = "later" | "tomorrow" | "3d" | "week";

const LATER_MS = 2 * 3_600_000;

/** The next-contact choices: the chip, the toast's wording and the day from today. */
const NEXTS: { key: NextKey; label: string; said: string; days: number }[] = [
  { key: "later", label: "Otra vez hoy (en 2 h)", said: "Hoy en 2 h", days: 0 },
  { key: "tomorrow", label: "Mañana", said: "Mañana", days: 1 },
  { key: "3d", label: "En 3 días", said: "En 3 días", days: 3 },
  { key: "week", label: "En 1 semana", said: "En 1 semana", days: 7 },
];

/** No answer or voicemail: try again in a while today. */
const RETRY_TODAY: ReadonlySet<CallOutcome> = new Set(["no_contesto", "buzon"]);

const OUTCOME_BY_LABEL = new Map(CALL_OUTCOMES.map((o) => [CALL_OUTCOME_LABEL[o], o]));
const NEXT_BY_LABEL = new Map(NEXTS.map((n) => [n.label, n]));

export function SeguimientoCallRow({
  row,
  colSpan,
  clock = () => new Date(),
  onSaved,
  onCancel,
}: {
  row: SegRow;
  colSpan: number;
  /** The real time at save (the page's «now» is as of its last read, maybe hours ago). */
  clock?: () => Date;
  /** The call and its reminder were recorded; gets the next contact as said in the toast. */
  onSaved: (nextSaid: string) => void;
  onCancel: () => void;
}) {
  const card = row.card;
  const noteId = useId();
  const [outcome, setOutcome] = useState<CallOutcome>("contesto");
  const [next, setNext] = useState<NextKey>("tomorrow");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // One set of keys per distinct request, with the time it was first sent: a retry of the same call
  // replays the very same bodies (same due date), an edited one is a new request at a new time.
  const keys = useRef<{ sig: string; at: Date; note: string; follow: ReturnType<typeof newFollowUpKeys> }>({
    sig: "",
    at: new Date(0),
    note: newCaseCommandKey(),
    follow: newFollowUpKeys(),
  });
  const who = personLabel(card.contact?.name) ?? "el contacto";

  function pickOutcome(o: CallOutcome) {
    setOutcome(o);
    if (RETRY_TODAY.has(o)) setNext("later");
  }

  async function save(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    const choice = NEXTS.find((n) => n.key === next)!;
    const body = buildCallNote(card.opportunity_id, { outcome, nextStep: "", text });
    const sig = JSON.stringify([body, next]);
    if (keys.current.sig !== sig) keys.current = { sig, at: clock(), note: newCaseCommandKey(), follow: newFollowUpKeys() };
    const now = keys.current.at;
    setBusy(true);
    setError(null);
    try {
      await addNote(body, keys.current.note);
      await scheduleFollowUp({
        card,
        day: dayInput(now, choice.days),
        note: body.body,
        now,
        keys: keys.current.follow,
        at: next === "later" ? new Date(now.getTime() + LATER_MS) : undefined,
      });
      onSaved(choice.said);
    } catch (err) {
      setError(caseRefusalText(err));
    } finally {
      setBusy(false);
    }
  }

  const fieldLabel = "text-[11px] font-bold text-ink-muted";
  return (
    <tr data-testid={`seg-call-${card.opportunity_id}`}>
      <td colSpan={colSpan} className="border-b border-brand-100 bg-brand-50 px-4 py-3.5">
        {/* Sticky to the left edge, and narrower than the visible sheet, so it stays in view while the sheet is scrolled sideways. */}
        <form onSubmit={save} className="sticky left-4 flex w-max max-w-[min(48rem,calc(100vw-6rem))] flex-wrap items-end gap-x-7 gap-y-4 motion-safe:animate-fade-in-up">
          <div className="flex flex-col gap-1.5">
            <span className={fieldLabel}>¿Cómo terminó la llamada a {who}?</span>
            <ChoiceChips
              label="Resultado de la llamada"
              options={CALL_OUTCOMES.map((o) => CALL_OUTCOME_LABEL[o])}
              value={CALL_OUTCOME_LABEL[outcome]}
              onChange={(v) => pickOutcome(OUTCOME_BY_LABEL.get(v)!)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <span className={fieldLabel}>Próximo contacto</span>
            <ChoiceChips
              label="Próximo contacto"
              options={NEXTS.map((n) => n.label)}
              value={NEXTS.find((n) => n.key === next)!.label}
              onChange={(v) => setNext(NEXT_BY_LABEL.get(v)!.key)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <label htmlFor={noteId} className={fieldLabel}>
              Nota (opcional)
            </label>
            <input
              id={noteId}
              name="nota"
              autoComplete="off"
              value={text}
              maxLength={8000}
              onChange={(e) => setText(e.target.value)}
              placeholder="Ej.: la orden de compra sale el viernes…"
              disabled={busy}
              className="h-9 min-w-60 rounded-md border border-line-strong bg-canvas-raised px-2.5 text-[13px] text-ink placeholder:text-ink-faint focus:border-brand-600 focus:outline-none focus:ring-1 focus:ring-brand-600"
            />
          </div>
          <div className="flex items-center gap-2">
            <Button onClick={onCancel} disabled={busy}>
              Cancelar
            </Button>
            <Button type="submit" variant="primary" busy={busy} busyLabel="Guardando…">
              Guardar llamada
            </Button>
          </div>
          {error ? (
            <p role="alert" className="basis-full text-[12.5px] font-medium text-bad">
              {error}
            </p>
          ) : null}
        </form>
      </td>
    </tr>
  );
}
