/**
 * «Origen del caso»: the channel a case came from, correctable by an operator who may decide
 * cases (`set-case-origin`). Choosing a value does not send it: the command needs a short note for
 * the case's audit trail, so the choice waits for «Guardar origen». A closed case is correctable
 * too (origin matters most on the won ones). The API stays the authority: a refusal is shown in
 * Spanish and a stale version refetches the case.
 */
import { useRef, useState, type FormEvent } from "react";
import { CASE_ORIGINS, NO_ORIGIN_LABEL, ORIGIN_LABEL, originLabel, type CaseOrigin } from "../caseOrigin";
import { caseRefusalText, isStaleRefusal, newCaseCommandKey, setCaseOrigin, useMayRunCaseCommands } from "../caseCommands";
import type { OpportunityCardData } from "../crmTypes";
import { OperatorApiError } from "../../api/operatorClient";
import { Button, FormField, SelectInput, TextareaInput, WRITE_DISABLED_REASON, toast } from "../ui";

export function OriginSelect({ card, onChanged }: { card: OpportunityCardData; onChanged: () => void }) {
  const mayDecide = useMayRunCaseCommands();
  const hasVersion = typeof card.version === "number";
  const current = card.origin ?? "";
  // `undefined`: nothing chosen yet. `""` is «Sin origen» (clears it).
  const [pending, setPending] = useState<string | undefined>(undefined);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const key = useRef(newCaseCommandKey());

  const options = [
    { value: "", label: NO_ORIGIN_LABEL },
    ...CASE_ORIGINS.map((o) => ({ value: o, label: ORIGIN_LABEL[o] })),
    // A value this build does not know stays visible instead of silently reading as another one.
    ...(current && !(CASE_ORIGINS as readonly string[]).includes(current) ? [{ value: current, label: originLabel(current) }] : []),
  ];
  const reason = !mayDecide ? WRITE_DISABLED_REASON : !hasVersion ? "El API no informó la versión del caso" : null;
  const choosing = pending !== undefined && pending !== current;

  function reset() {
    setPending(undefined);
    setNote("");
    setError(null);
  }

  async function save(e: FormEvent) {
    e.preventDefault();
    if (!choosing || busy || !note.trim() || !hasVersion) return;
    setBusy(true);
    setError(null);
    try {
      await setCaseOrigin(
        {
          opportunity_id: card.opportunity_id,
          opportunity_version: card.version as number,
          origin: pending === "" ? null : (pending as CaseOrigin),
          note: note.trim(),
        },
        key.current,
      );
      key.current = newCaseCommandKey();
      toast(`Origen: ${originLabel(pending)}.`);
      reset();
      onChanged();
    } catch (err) {
      // A definite refusal frees the key; a lost connection keeps it, so a retry cannot record twice.
      if (err instanceof OperatorApiError) key.current = newCaseCommandKey();
      setError(caseRefusalText(err));
      if (isStaleRefusal(err)) {
        setPending(undefined);
        onChanged();
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={save} className="space-y-2" data-testid="origin-select">
      <FormField
        label="Origen del caso"
        htmlFor="case-origin-select"
        // The explanation only while changing it: on every case it was a paragraph nobody needed.
        hint={reason ?? (choosing ? "Si lo cambias, queda en el historial." : undefined)}
      >
        <SelectInput
          id="case-origin-select"
          value={pending ?? current}
          onChange={(v) => {
            setError(null);
            setPending(v === current ? undefined : v);
          }}
          options={options}
          disabled={reason !== null || busy}
        />
      </FormField>
      {choosing ? (
        <>
          <FormField label="Nota" htmlFor="case-origin-note" required hint="Queda en la auditoría del caso.">
            <TextareaInput id="case-origin-note" value={note} onChange={setNote} maxLength={2000} rows={2} />
          </FormField>
          <div className="flex justify-end gap-2">
            <Button onClick={reset} disabled={busy}>
              Cancelar
            </Button>
            <Button type="submit" variant="primary" disabled={!note.trim()} busy={busy} busyLabel="Guardando…">
              Guardar origen
            </Button>
          </div>
        </>
      ) : null}
      {error ? (
        <p role="alert" className="text-[11px] text-bad">
          {error}
        </p>
      ) : null}
    </form>
  );
}
