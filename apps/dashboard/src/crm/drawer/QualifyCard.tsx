/**
 * «Calificar»: five qualifying questions saved as one case note with prefix «Calificación:».
 *
 * - On the Resumen tab of the case drawer.
 * - When no «Calificación:» note exists: shows the form with 5 short inputs.
 * - When one exists: shows it read-only with «Editar» (editing writes a NEW note via `addNote` —
 *   no need for `reviseNote`; both old and new note are kept in history).
 * - Saves via the existing `addNote(subject_kind="opportunity")` command — no new API routes.
 *
 * The prefix «Calificación:» is a recognizable marker so the note can be found later.
 */
import { useCallback, useId, useRef, useState } from "react";
import { addNote, newIdempotencyKey, type NoteRow } from "../authoring/crmAuthoringApi";
import { newCaseCommandKey } from "../caseCommands";
import { fetchCaseNotes } from "../crmApi";
import type { OpportunityCardData } from "../crmTypes";
import { Button, FormField, Section, TextareaInput, toast } from "../ui";
import { useResource } from "../useResource";

/** The prefix that marks a note written by this form. */
export const QUALIFY_PREFIX = "Calificación:";

/** Build the note body from the five answers. */
export function buildQualifyNote(answers: QualifyAnswers): string {
  return [
    QUALIFY_PREFIX,
    `1. Para qué lo usa: ${answers.uso || "—"}`,
    `2. Para cuándo: ${answers.plazo || "—"}`,
    `3. Presupuesto: ${answers.presupuesto || "—"}`,
    `4. Decisores: ${answers.decisores || "—"}`,
    `5. Destino: ${answers.destino || "—"}`,
  ].join("\n");
}

/** Parse the five answers from an existing qualify note body. */
function parseQualifyNote(body: string): QualifyAnswers {
  const line = (n: number) =>
    body
      .split("\n")
      .find((l) => l.startsWith(`${n}.`))
      ?.replace(/^\d+\.\s*[^:]+:\s*/, "")
      ?.trim() ?? "";
  return {
    uso: line(1),
    plazo: line(2),
    presupuesto: line(3),
    decisores: line(4),
    destino: line(5),
  };
}

interface QualifyAnswers {
  uso: string;
  plazo: string;
  presupuesto: string;
  decisores: string;
  destino: string;
}

const EMPTY: QualifyAnswers = { uso: "", plazo: "", presupuesto: "", decisores: "", destino: "" };

/** Inner form; note may be null (new) or the existing note (edit). */
function QualifyForm({
  opportunityId,
  note,
  onDone,
  onCancel,
}: {
  opportunityId: string;
  note: NoteRow | null;
  onDone: () => void;
  onCancel: () => void;
}) {
  const ids = {
    uso: useId(),
    plazo: useId(),
    presupuesto: useId(),
    decisores: useId(),
    destino: useId(),
  };
  const initial = note ? parseQualifyNote(note.body) : EMPTY;
  const [answers, setAnswers] = useState<QualifyAnswers>(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const key = useRef(newIdempotencyKey());

  function set(field: keyof QualifyAnswers) {
    return (v: string) => setAnswers((prev) => ({ ...prev, [field]: v }));
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await addNote(
        { subject_kind: "opportunity", subject_id: opportunityId, body: buildQualifyNote(answers) },
        key.current,
      );
      toast("Calificación guardada.");
      onDone();
    } catch {
      setError("No se pudo guardar. Intenta de nuevo.");
      key.current = newCaseCommandKey();
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3 rounded-lg border border-line bg-canvas-raised p-3" data-testid="qualify-form">
      <FormField label="¿Para qué lo va a usar?" htmlFor={ids.uso} hint="Muestra, producción, norma técnica…">
        <TextareaInput id={ids.uso} value={answers.uso} onChange={set("uso")} rows={2} maxLength={1000} disabled={busy} />
      </FormField>
      <FormField label="¿Para cuándo lo necesita?" htmlFor={ids.plazo}>
        <TextareaInput id={ids.plazo} value={answers.plazo} onChange={set("plazo")} rows={1} maxLength={500} disabled={busy} />
      </FormField>
      <FormField
        label="¿Presupuesto aprobado o depende de un fondo?"
        htmlFor={ids.presupuesto}
        hint="Si depende de fondo: ¿cuándo sale el resultado?"
      >
        <TextareaInput id={ids.presupuesto} value={answers.presupuesto} onChange={set("presupuesto")} rows={2} maxLength={1000} disabled={busy} />
      </FormField>
      <FormField label="¿Quién más participa en la decisión?" htmlFor={ids.decisores}>
        <TextareaInput id={ids.decisores} value={answers.decisores} onChange={set("decisores")} rows={1} maxLength={500} disabled={busy} />
      </FormField>
      <FormField
        label="¿Es para su laboratorio o para un cliente suyo?"
        htmlFor={ids.destino}
        hint="Laboratorio propio, institución u otro · o reventa"
      >
        <TextareaInput id={ids.destino} value={answers.destino} onChange={set("destino")} rows={1} maxLength={500} disabled={busy} />
      </FormField>
      {error ? (
        <p role="alert" className="text-[11px] text-bad">
          {error}
        </p>
      ) : null}
      <div className="flex gap-2">
        <Button type="button" variant="primary" disabled={busy} busy={busy} busyLabel="Guardando…" onClick={() => void save()}>
          Guardar calificación
        </Button>
        <Button type="button" onClick={onCancel} disabled={busy}>
          Cancelar
        </Button>
      </div>
    </div>
  );
}

/** Read-only view of an existing qualify note. */
function QualifyReadOnly({
  note,
  mayAuthor,
  onEdit,
}: {
  note: NoteRow;
  mayAuthor: boolean;
  onEdit: () => void;
}) {
  const answers = parseQualifyNote(note.body);
  return (
    <div className="rounded-lg border border-line bg-canvas-raised p-3 text-[12.5px]" data-testid="qualify-readonly">
      <dl className="space-y-1.5">
        {([
          ["Para qué lo usa", answers.uso],
          ["Para cuándo", answers.plazo],
          ["Presupuesto", answers.presupuesto],
          ["Decisores", answers.decisores],
          ["Destino", answers.destino],
        ] as [string, string][]).map(([label, value]) => (
          <div key={label} className="flex flex-col gap-0.5">
            <dt className="text-[11px] font-semibold text-ink-muted">{label}</dt>
            <dd className="text-ink">{value || <span className="text-ink-faint">—</span>}</dd>
          </div>
        ))}
      </dl>
      {mayAuthor ? (
        <div className="mt-3 border-t border-line pt-2">
          <Button type="button" onClick={onEdit}>
            Editar
          </Button>
        </div>
      ) : null}
    </div>
  );
}

/**
 * «Calificar»: qualify-before-quote card for the case drawer's Resumen tab.
 * Fetches case notes to find an existing «Calificación:» note; shows the form if none found.
 */
export function QualifyCard({
  card,
  mayAuthor,
  onChanged,
}: {
  card: OpportunityCardData;
  mayAuthor: boolean;
  onChanged: () => void;
}) {
  const load = useCallback(
    () => fetchCaseNotes(card.opportunity_id),
    [card.opportunity_id],
  );
  // Re-read when the case's notes change (a new calification was just saved).
  const [state, reload] = useResource(load, [card.opportunity_id, card.last_note?.created_at ?? ""]);
  const [editing, setEditing] = useState(false);

  const notes = state.kind === "ready" ? state.data.notes : [];
  const existing = notes.filter((n) => n.is_latest && n.status === "active" && n.body.startsWith(QUALIFY_PREFIX))
    .sort((a, b) => b.created_at.localeCompare(a.created_at))[0] ?? null;

  function onDone() {
    setEditing(false);
    reload();
    onChanged();
  }

  const content = (() => {
    if (state.kind === "loading") {
      return <p className="text-[11px] text-ink-faint">Cargando…</p>;
    }
    if (state.kind !== "ready") {
      return <p className="text-[11px] text-warn">No se pudieron leer las notas del caso.</p>;
    }
    if (existing && !editing) {
      return <QualifyReadOnly note={existing} mayAuthor={mayAuthor} onEdit={() => setEditing(true)} />;
    }
    if (mayAuthor) {
      return (
        <QualifyForm
          opportunityId={card.opportunity_id}
          note={editing ? existing : null}
          onDone={onDone}
          onCancel={() => setEditing(false)}
        />
      );
    }
    return <p className="text-[12px] text-ink-faint">Sin calificación registrada.</p>;
  })();

  return (
    <Section
      title="Calificar"
      aside={
        !existing && mayAuthor && state.kind === "ready" ? (
          <span className="inline-flex items-center rounded-full border border-brand-600/25 bg-brand-50 px-1.5 py-px text-[10.5px] font-medium text-accent">
            Sin calificar
          </span>
        ) : null
      }
    >
      {content}
    </Section>
  );
}
