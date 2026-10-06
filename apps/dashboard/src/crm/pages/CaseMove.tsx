/**
 * «Cambiar estado»: move a case to one of the operator's states — from the drawer, or by dropping
 * its card on a Tablero column. One form per kind of target:
 *
 * - **Solicitada / En estudio / Enviada / Conversación** — as many `advance-case-stage` steps as
 *   the stage table needs (`stagePath`), each with its receipt, the note prefilled and editable.
 * - **Perdida** — one click on a reason (`LOST_REASONS`); «Sin respuesta» closes as `abandoned`.
 * - **En pausa** — a date and a reason: a `create-task` «Retomar: <motivo>» due that day. The case
 *   keeps its stage; the Tablero shows it under «En pausa» until the task comes due.
 *
 * A paused case that moves anywhere is resumed too (its future tasks are cancelled), after the
 * move, so a refused move leaves the pause as it was. Every key belongs to the form and is
 * renewed only after a refusal; a refusal part-way says which steps stayed recorded.
 */
import { useMemo, useRef, useState, type FormEvent } from "react";
import {
  WonFlowError,
  cancelTask,
  caseRefusalText,
  createTask,
  isStaleRefusal,
  moveCase,
  newCaseCommandKey,
  resumeCase,
  stagePath,
  type WonStep,
} from "../caseCommands";
import type { OpportunityCardData } from "../crmTypes";
import {
  BOARD_COLUMNS,
  LOST_REASONS,
  PAUSE_REASONS,
  STAGE_LABEL,
  boardColumnOf,
  pauseTasks,
  pausedUntil,
  type BoardColumnKey,
} from "../stage";
import { Button, ChoiceChips, FormField, TextInput, TextareaInput, fmtDate } from "../ui";

export interface MoveOutcome {
  tone: "good" | "bad" | "warn";
  lines: string[];
}

/** The targets «Cambiar estado» offers; «Ganada» is «Marcar ganada», which names a revision. */
export type MoveTarget = Exclude<BoardColumnKey, "ganada">;

export const COLUMN_LABEL: Record<BoardColumnKey, string> = Object.fromEntries(
  BOARD_COLUMNS.map((c) => [c.key, c.label]),
) as Record<BoardColumnKey, string>;

/** `YYYY-MM-DD` of `now` plus `days`, in the browser's zone. */
export function isoDay(now: Date, days: number): string {
  const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() + days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** 09:00 of `day` in the browser's zone, as the ISO instant the API stores. */
export function morningOf(day: string): string {
  return new Date(`${day}T09:00:00`).toISOString();
}

function receiptLine(step: WonStep, n: number, total: number): string {
  const replay = step.receipt.replayed ? " (ya estaba registrado)" : "";
  return `${total > 1 ? `${n}/${total} · ` : ""}${step.label}: registrado${replay} · recibo ${step.receipt.command_receipt_id.slice(0, 8)}`;
}

/** Why `card` cannot go to `target`, or null when it can. */
export function moveRefusal(card: OpportunityCardData, target: MoveTarget, now: Date = new Date()): string | null {
  if (card.closed_at || ["won", "lost", "abandoned"].includes(card.stage)) return "El caso está cerrado: un caso cerrado no se mueve.";
  if (typeof card.version !== "number") return "El API no informó la versión del caso.";
  if (target === "pausa" || target === "perdida") return null;
  const to = BOARD_COLUMNS.find((c) => c.key === target)?.target as string;
  const inColumn = BOARD_COLUMNS.find((c) => c.key === target)?.stages.includes(card.stage) ?? false;
  if (inColumn && !pausedUntil(card, now)) return `El caso ya está en «${COLUMN_LABEL[target]}».`;
  if (!inColumn && stagePath(card.stage, to) === null) return "Ese cambio no está permitido desde el estado actual.";
  return null;
}

export function CaseMoveForm({
  card,
  target,
  onCancel,
  onDone,
  now,
  initialReason = null,
}: {
  card: OpportunityCardData;
  target: MoveTarget;
  onCancel: () => void;
  onDone: (o: MoveOutcome, refetch: boolean) => void;
  now?: Date;
  /** A reason chip chosen up front («Cerrar sin respuesta» from «Hoy»). */
  initialReason?: string | null;
}) {
  const at = useMemo(() => now ?? new Date(), [now]);
  const from = boardColumnOf(card, at);
  const paused = pauseTasks(card, at);
  const column = BOARD_COLUMNS.find((c) => c.key === target);
  // Staying in the same stage column only resumes the pause.
  const stageTarget =
    target === "pausa" || target === "perdida"
      ? null
      : column?.stages.includes(card.stage)
        ? card.stage
        : (column?.target ?? null);
  const steps = stageTarget ? (stagePath(card.stage, stageTarget) ?? []) : [];

  const [reason, setReason] = useState<string | null>(initialReason);
  const [detail, setDetail] = useState("");
  const [day, setDay] = useState(isoDay(at, 14));
  const [note, setNote] = useState(
    stageTarget ? `Movido de «${COLUMN_LABEL[from]}» a «${COLUMN_LABEL[target]}».` : "",
  );
  const [busy, setBusy] = useState(false);
  const keysRef = useRef(freshKeys());

  function freshKeys() {
    return {
      move: [newCaseCommandKey(), newCaseCommandKey(), newCaseCommandKey(), newCaseCommandKey()],
      create: newCaseCommandKey(),
      cancel: {} as Record<string, string>,
    };
  }
  function cancelKeys(): Record<string, string> {
    for (const t of card.open_tasks ?? []) keysRef.current.cancel[t.task_id] ??= newCaseCommandKey();
    return keysRef.current.cancel;
  }

  const refusal = moveRefusal(card, target, at);
  const today = isoDay(at, 0);
  const ready =
    !refusal &&
    !busy &&
    (target === "perdida"
      ? reason !== null
      : target === "pausa"
        ? reason !== null && day > today
        : note.trim() !== "");

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!ready) return;
    setBusy(true);
    const done: WonStep[] = [];
    try {
      if (target === "pausa") {
        const text = detail.trim() ? `${reason}: ${detail.trim()}` : (reason as string);
        // A new date replaces the old pause: the earliest open task is what the board reads.
        done.push(...(await resumeCase(paused, `Nueva pausa hasta ${fmtDate(morningOf(day))}.`, cancelKeys())));
        const receipt = await createTask(
          { opportunity_id: card.opportunity_id, title: `Retomar: ${reason}`, due_at: morningOf(day), note: text },
          keysRef.current.create,
        );
        done.push({ label: `En pausa hasta ${fmtDate(morningOf(day))}`, receipt });
      } else if (target === "perdida") {
        const chosen = LOST_REASONS.find((r) => r.label === reason);
        const text = detail.trim() ? `${reason}: ${detail.trim()}` : (reason as string);
        done.push(
          ...(await moveCase(
            { opportunity_id: card.opportunity_id, stage: card.stage, version: card.version as number },
            chosen?.stage ?? "lost",
            text,
            text,
            keysRef.current.move,
          )),
        );
        // Nothing is left to follow up on a closed case.
        for (const t of card.open_tasks ?? []) {
          const receipt = await cancelTask(
            { task_id: t.task_id, task_version: t.version, note: "El caso se cerró como perdido." },
            cancelKeys()[t.task_id],
          );
          done.push({ label: "Tarea cancelada", receipt });
        }
      } else {
        if (steps.length > 0) {
          done.push(
            ...(await moveCase(
              { opportunity_id: card.opportunity_id, stage: card.stage, version: card.version as number },
              stageTarget as string,
              note.trim(),
              null,
              keysRef.current.move,
            )),
          );
        }
        done.push(...(await resumeCase(paused, note.trim(), cancelKeys())));
      }
      onDone({ tone: "good", lines: done.map((s, i) => receiptLine(s, i + 1, done.length)) }, true);
    } catch (err) {
      keysRef.current = freshKeys();
      const recorded = [...done, ...(err instanceof WonFlowError ? err.done : [])];
      if (recorded.length > 0) {
        onDone(
          {
            tone: "warn",
            lines: [...recorded.map((s, i) => receiptLine(s, i + 1, recorded.length + 1)), `No se registró el resto: ${caseRefusalText(err)}`],
          },
          true,
        );
      } else {
        onDone({ tone: "bad", lines: [caseRefusalText(err)] }, isStaleRefusal(err));
      }
    } finally {
      setBusy(false);
    }
  }

  const title = `Mover a «${COLUMN_LABEL[target]}»`;
  return (
    <form onSubmit={(e) => void submit(e)} className="space-y-3" aria-label={title} data-testid="case-move-form">
      <p className="text-xs text-ink-muted">
        Ahora: <strong className="text-ink">{COLUMN_LABEL[from]}</strong>
        {from === "pausa" && paused[0] ? ` hasta ${fmtDate(paused[0].due_at)}` : ""}
        {from !== "pausa" ? ` (${STAGE_LABEL[card.stage] ?? card.stage})` : ""}.
      </p>
      {refusal ? (
        <p role="alert" className="rounded-md border border-bad/40 bg-bad-bg px-3 py-2 text-xs text-bad">
          {refusal}
        </p>
      ) : target === "perdida" ? (
        <>
          <FormField label="Motivo" required hint="«Sin respuesta» cierra el caso como abandonado.">
            <ChoiceChips label="Motivo de pérdida" options={LOST_REASONS.map((r) => r.label)} value={reason} onChange={setReason} />
          </FormField>
          <FormField label="Detalle (opcional)" htmlFor="case-lost-detail">
            <TextareaInput id="case-lost-detail" value={detail} onChange={setDetail} maxLength={1500} rows={2} />
          </FormField>
        </>
      ) : target === "pausa" ? (
        <>
          <FormField label="Retomar el" htmlFor="case-pause-day" required hint="Ese día el caso vuelve a «Hoy».">
            <TextInput id="case-pause-day" type="date" value={day} onChange={setDay} />
          </FormField>
          <FormField label="Motivo" required>
            <ChoiceChips label="Motivo de la pausa" options={PAUSE_REASONS} value={reason} onChange={setReason} />
          </FormField>
          <FormField label="Detalle (opcional)" htmlFor="case-pause-detail">
            <TextareaInput id="case-pause-detail" value={detail} onChange={setDetail} maxLength={1500} rows={2} />
          </FormField>
        </>
      ) : (
        <>
          {steps.length > 1 ? (
            <p className="text-[11px] text-ink-muted" data-testid="case-move-steps">
              Se registrarán {steps.length} pasos, cada uno con su recibo: {steps.map((s) => STAGE_LABEL[s] ?? s).join(" → ")}.
            </p>
          ) : null}
          {paused.length > 0 ? (
            <p className="text-[11px] text-ink-muted">La pausa se retoma: su tarea queda cancelada.</p>
          ) : null}
          <FormField label="Nota" htmlFor="case-move-note" required hint="Queda en la auditoría del caso.">
            <TextareaInput id="case-move-note" value={note} onChange={setNote} maxLength={2000} rows={2} />
          </FormField>
        </>
      )}
      <div className="flex justify-end gap-2">
        <Button onClick={onCancel} disabled={busy}>
          Cancelar
        </Button>
        <Button type="submit" variant={target === "perdida" ? "danger" : "primary"} disabled={!ready} busy={busy} busyLabel="Registrando…">
          {target === "perdida" ? "Marcar perdida" : target === "pausa" ? "Pausar" : "Mover"}
        </Button>
      </div>
    </form>
  );
}
