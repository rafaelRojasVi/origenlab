/**
 * «¿Es Pedro Soto (Universidad Ficticia)?» — one weak guess about who wrote an email, answered with
 * one «Sí» or «No» (sales or admin). Shared by the contact drawer and the case's emails.
 */
import { useRef, useState } from "react";
import { caseRefusalText } from "../caseCommands";
import type { PersonGuess } from "../crmTypes";
import { Button } from "../ui";
import { answerPersonGuess, newIdempotencyKey } from "./crmAuthoringApi";

export function PersonGuessRow({ guess, mayAnswer, onAnswered }: {
  guess: PersonGuess;
  mayAnswer: boolean;
  onAnswered: () => void;
}) {
  const [busy, setBusy] = useState<"si" | "no" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const keys = useRef({ si: newIdempotencyKey(), no: newIdempotencyKey() });

  async function answer(a: "si" | "no") {
    setBusy(a);
    setError(null);
    try {
      await answerPersonGuess({ guess_id: guess.guess_id, answer: a }, keys.current[a]);
      onAnswered();
    } catch (err) {
      setError(caseRefusalText(err));
      setBusy(null);
    }
  }

  return (
    <div data-testid="person-guess" className="rounded-md border border-line bg-canvas-sunken/60 px-3 py-2 text-xs">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <p className="min-w-[10rem] flex-1 font-medium text-ink">{guess.question_es}</p>
        {mayAnswer ? (
          <div className="flex shrink-0 gap-2">
            <Button variant="secondary" onClick={() => void answer("si")} busy={busy === "si"} disabled={busy !== null}>Sí</Button>
            <Button variant="quiet" onClick={() => void answer("no")} busy={busy === "no"} disabled={busy !== null}>No</Button>
          </div>
        ) : null}
      </div>
      {guess.reasons.length > 0 ? (
        <p className="mt-0.5 text-[11px] text-ink-muted">Por qué: {guess.reasons.join("; ")}</p>
      ) : null}
      {error ? <p role="alert" className="mt-1 text-[11px] text-bad">{error}</p> : null}
    </div>
  );
}
