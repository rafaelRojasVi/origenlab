/**
 * «Cambiar estado»: move a case to one of the operator's states — from the drawer, or by dropping
 * its card on a Tablero column. One form per kind of target:
 *
 * - **Solicitada / Enviada / Conversación** — as many `advance-case-stage` steps as
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
  isReopenable,
  isStaleRefusal,
  moveCase,
  newCaseCommandKey,
  pauseCase,
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
  pauseDayIso,
  isPaused,
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
  if (target === "silenciosas") return "Silenciosas se llena sola: un caso entra a los 21 días sin respuesta.";
  const closed = card.closed_at != null || ["won", "lost", "abandoned"].includes(card.stage);
  if (closed && card.stage === "won") return "El caso está ganado: una venta ganada no se mueve.";
  if (closed && !isReopenable(card)) return "Este caso se descartó por una corrección: abre uno nuevo desde el correo.";
  if (typeof card.version !== "number") return "El API no informó la versión del caso.";
  if (closed && (target === "pausa" || target === "perdida")) {
    return "Un caso cerrado se reabre a «Solicitada», «Enviada» o «Conversación».";
  }
  if (target === "pausa" || target === "perdida") return null;

  const to = BOARD_COLUMNS.find((c) => c.key === target)?.target as string;
  const inColumn = BOARD_COLUMNS.find((c) => c.key === target)?.stages.includes(card.stage) ?? false;
  // Resuming a paused case in its current column does not advance its stage.
  if (inColumn) {
    return isPaused(card, now) ? null : `El caso ya está en «${COLUMN_LABEL[target]}».`;
  }
  if (stagePath(card.stage, to) === null) return "Ese cambio no está permitido desde el estado actual.";

  // Enviada and later require a human-confirmed requesting institution.
  // Refuse before a multi-step move partially advances the case.
  if (["enviada", "conversacion"].includes(target)) {
    if (!card.organization) {
      return "Antes de mover este caso, debes asociar y confirmar la institución solicitante.";
    }
    // The API guards the confirmed relationship on this case, not the
    // separate confirmation state of the organization's CRM profile.
    // Older API responses omit this field; the backend remains authoritative.
    if (
      card.requesting_institution_confirmation !== undefined &&
      card.requesting_institution_confirmation !== "confirmed"
    ) {
      return "Antes de mover este caso, debes confirmar la institución solicitante.";
    }
  }
  return null;
}

export function CaseMoveForm({
  card,
  target,
  onCancel,
  onDone,
  now,
  initialReason = null,
  showFrom = true,
}: {
  card: OpportunityCardData;
  target: MoveTarget;
  onCancel: () => void;
  onDone: (o: MoveOutcome, refetch: boolean) => void;
  now?: Date;
  /** A reason chip chosen up front («Cerrar sin respuesta» from «Hoy»). */
  initialReason?: string | null;
  /** «Ahora: …»; off when the form was opened for this one target (its header says it). */
  showFrom?: boolean;
}) {
  const at = useMemo(() => now ?? new Date(), [now]);
  const from = boardColumnOf(card, at);
  const paused = isPaused(card, at);
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
        // The pause is a state of the case (set-case-pause, 2026-11-13): one command sets the day and
        // the reason, replacing an earlier pause. Reminders on the case are not touched any more.
        done.push(...(await pauseCase(
          { opportunity_id: card.opportunity_id, version: card.version as number },
          day,
          text.slice(0, 200),
          `En pausa hasta ${fmtDate(morningOf(day))}.`,
          keysRef.current.create,
        )));
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
            { task_id: t.task_id, task_version: t.version, note: "El caso se cerró." },
            cancelKeys()[t.task_id],
          );
          done.push({ label: "Tarea cancelada", receipt });
        }
      } else {
        // Leaving «En pausa» ends the pause first (its own event), then the stage move runs against
        // the version that command returned.
        let version = card.version as number;
        if (paused) {
          done.push(...(await resumeCase({ opportunity_id: card.opportunity_id, version }, note.trim(), keysRef.current.create)));
          version += 1;
        }
        if (steps.length > 0) {
          done.push(
            ...(await moveCase(
              { opportunity_id: card.opportunity_id, stage: card.stage, version },
              stageTarget as string,
              note.trim(),
              null,
              keysRef.current.move,
            )),
          );
        }
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
      {showFrom ? <p className="text-xs text-ink-muted">
        Ahora: <strong className="text-ink">{COLUMN_LABEL[from]}</strong>
        {from === "pausa" && card.paused_until ? ` hasta ${fmtDate(pauseDayIso(card.paused_until))}` : ""}
        {from !== "pausa" ? ` (${STAGE_LABEL[card.stage] ?? card.stage})` : ""}.
      </p> : null}
      {refusal ? (
        <p role="alert" className="rounded-md border border-bad/40 bg-bad-bg px-3 py-2 text-xs text-bad">
          {refusal}
        </p>
      ) : target === "perdida" ? (
        <>
          <FormField label="Motivo" required hint="«Sin respuesta» y «No es una solicitud» cierran como abandonado; se conserva la evidencia.">
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
          {paused ? (
            <p className="text-[11px] text-ink-muted">La pausa se retoma.</p>
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
          {target === "perdida" ? reason === "No es una solicitud" ? "Cerrar caso incorrecto" : "Marcar perdida" : target === "pausa" ? "Pausar" : "Mover"}
        </Button>
      </div>
    </form>
  );
}

/** «No es una solicitud», in one step (owner 2026-10-19): the same command and reason the
 *  «Perdida» form sends for that chip with no detail — `abandoned`, close reason «No es una
 *  solicitud», open tasks cancelled — behind a short confirmation instead of the full form. */
export const NOT_A_REQUEST = "No es una solicitud";

export function NotRequestConfirm({
  card,
  onCancel,
  onDone,
}: {
  card: OpportunityCardData;
  onCancel: () => void;
  onDone: (o: MoveOutcome, refetch: boolean) => void;
}) {
  const [busy, setBusy] = useState(false);
  const keysRef = useRef({ move: [newCaseCommandKey(), newCaseCommandKey(), newCaseCommandKey(), newCaseCommandKey()],
    cancel: {} as Record<string, string> });
  const stage = LOST_REASONS.find((r) => r.label === NOT_A_REQUEST)?.stage ?? "abandoned";
  const refusal = moveRefusal(card, "perdida");

  async function confirm() {
    if (busy || refusal) return;
    setBusy(true);
    const done: WonStep[] = [];
    try {
      done.push(
        ...(await moveCase(
          { opportunity_id: card.opportunity_id, stage: card.stage, version: card.version as number },
          stage,
          NOT_A_REQUEST,
          NOT_A_REQUEST,
          keysRef.current.move,
        )),
      );
      for (const t of card.open_tasks ?? []) {
        keysRef.current.cancel[t.task_id] ??= newCaseCommandKey();
        const receipt = await cancelTask(
          { task_id: t.task_id, task_version: t.version, note: "El caso se cerró." },
          keysRef.current.cancel[t.task_id],
        );
        done.push({ label: "Tarea cancelada", receipt });
      }
      onDone({ tone: "good", lines: done.map((s, i) => receiptLine(s, i + 1, done.length)) }, true);
    } catch (err) {
      keysRef.current = { move: [newCaseCommandKey(), newCaseCommandKey(), newCaseCommandKey(), newCaseCommandKey()], cancel: {} };
      const recorded = [...done, ...(err instanceof WonFlowError ? err.done : [])];
      if (recorded.length > 0) {
        onDone({ tone: "warn", lines: [...recorded.map((s, i) => receiptLine(s, i + 1, recorded.length + 1)),
          `No se registró el resto: ${caseRefusalText(err)}`] }, true);
      } else {
        onDone({ tone: "bad", lines: [caseRefusalText(err)] }, isStaleRefusal(err));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div role="group" aria-label="Confirmar «No es una solicitud»" data-testid="case-not-request-confirm"
      className="space-y-2 rounded-md border border-line p-3">
      <p className="text-[13px] text-ink">
        ¿Cerrar como «No es una solicitud»? Se cierra como abandonado y se conserva el correo.
      </p>
      {refusal ? <p className="text-xs text-bad">{refusal}</p> : null}
      <div className="flex flex-wrap gap-2">
        <Button variant="primary" busy={busy} busyLabel="Cerrando…" disabled={!!refusal} onClick={() => void confirm()}>
          Cerrar
        </Button>
        <Button onClick={onCancel} disabled={busy}>Cancelar</Button>
      </div>
    </div>
  );
}
