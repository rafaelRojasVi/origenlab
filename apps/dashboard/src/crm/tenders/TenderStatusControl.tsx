import { useId, useRef, useState, type FormEvent } from "react";
import { Button, toast } from "../ui";
import { newTenderCommandKey, setTenderStatus, tenderRefusal, type TenderStatus } from "./tenderApi";
import { definitive, refusalText } from "./tenderLabels";

/**
 * The tender's estado: «Preparando», «Postulada» and «Perdida» (which asks for the motivo) are the
 * salesperson's to record; «Adjudicada» is shown, disabled, until award capture arrives (phase 3).
 * Each change sends the version the page was read at; a stale one reloads the page.
 */
type Settable = "preparando" | "postulada" | "perdida";

const CHOICES: { value: Settable; label: string }[] = [
  { value: "preparando", label: "Preparando" },
  { value: "postulada", label: "Postulada" },
  { value: "perdida", label: "Perdida" },
];
export const AWARD_LATER = "Se registra al publicarse la adjudicación (próxima fase).";
const REASON_MAX = 2000;

const CHIP =
  "inline-flex min-h-[44px] items-center rounded-full border px-4 text-[13px] font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 disabled:cursor-not-allowed";

export function TenderStatusControl({
  tenderId,
  status,
  version,
  onChanged,
}: {
  tenderId: string;
  status: TenderStatus;
  version: number;
  onChanged: () => void;
}) {
  const noteId = useId();
  const reasonId = useId();
  const [busy, setBusy] = useState(false);
  const [asking, setAsking] = useState(false);
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const keyRef = useRef<{ sig: string; key: string } | null>(null);

  const send = async (target: Settable, why?: string) => {
    if (busy) return;
    const body = { opportunity_id: tenderId, status: target, expected_version: version, ...(why ? { reason: why } : {}) };
    const sig = JSON.stringify(body);
    if (keyRef.current?.sig !== sig) keyRef.current = { sig, key: newTenderCommandKey() };
    setBusy(true);
    setProblem(null);
    try {
      await setTenderStatus(body, keyRef.current.key);
      keyRef.current = null;
      setAsking(false);
      setReason("");
      toast("Estado guardado");
      onChanged();
    } catch (err) {
      if (definitive(err)) keyRef.current = null;
      setProblem(refusalText(err));
      if (tenderRefusal(err)?.code === "case_version_conflict") onChanged();
    } finally {
      setBusy(false);
    }
  };

  const choose = (target: Settable) => {
    if (target === "perdida") {
      setAsking(true);
      setProblem(null);
      return;
    }
    setAsking(false);
    if (target !== status) void send(target);
  };

  const submitLost = (e: FormEvent) => {
    e.preventDefault();
    const why = reason.trim();
    if (!why) {
      setProblem("Escribe el motivo.");
      return;
    }
    void send("perdida", why);
  };

  return (
    <div className="space-y-2">
      <div role="group" aria-label="Estado de la licitación" className="flex flex-wrap items-center gap-2">
        {CHOICES.map((c) => {
          const pressed = status === c.value;
          return (
            <button
              key={c.value}
              type="button"
              aria-pressed={pressed}
              aria-expanded={c.value === "perdida" ? asking : undefined}
              disabled={busy}
              onClick={() => choose(c.value)}
              className={`${CHIP} ${
                pressed
                  ? "border-brand-700 bg-brand-700 text-white"
                  : "border-line-strong bg-canvas-raised text-ink hover:border-brand-600 hover:text-accent"
              }`}
            >
              {c.label}
            </button>
          );
        })}
        <button
          type="button"
          disabled
          aria-pressed={status === "adjudicada"}
          aria-describedby={noteId}
          className={`${CHIP} border-line bg-canvas-sunken text-ink-muted`}
        >
          Adjudicada
        </button>
      </div>
      <p id={noteId} className="text-[12px] text-ink-muted">
        {AWARD_LATER}
      </p>
      {asking ? (
        <form onSubmit={submitLost} noValidate className="flex max-w-xl flex-col gap-2">
          <label htmlFor={reasonId} className="text-[12.5px] font-medium text-ink-muted">
            Motivo
          </label>
          <textarea
            id={reasonId}
            value={reason}
            maxLength={REASON_MAX}
            rows={2}
            aria-invalid={problem ? true : undefined}
            aria-describedby={problem ? `${reasonId}-problem` : undefined}
            onChange={(e) => {
              setReason(e.target.value);
              setProblem(null);
            }}
            className="w-full min-w-0 rounded-lg border border-line-strong bg-canvas-raised px-3 py-2 text-[14px] text-ink focus:border-brand-600 focus:outline-none focus:ring-1 focus:ring-brand-600"
          />
          <div className="flex flex-wrap gap-2">
            <Button type="submit" variant="primary" className="min-h-[44px] px-4 text-[13px]" busy={busy} busyLabel="Guardando…">
              Guardar como perdida
            </Button>
            <Button className="min-h-[44px] px-4 text-[13px]" disabled={busy} onClick={() => setAsking(false)}>
              Cancelar
            </Button>
          </div>
        </form>
      ) : null}
      {problem ? (
        <p id={`${reasonId}-problem`} role="alert" className="text-[12.5px] text-bad">
          {problem}
        </p>
      ) : null}
    </div>
  );
}
