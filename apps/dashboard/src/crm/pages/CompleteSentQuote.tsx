import { useRef, useState, type FormEvent } from "react";
import { fetchV2Organizations } from "../../api/v2Client";
import { fetchOrganizationAuthoring } from "../authoring/crmAuthoringApi";
import { caseRefusalText, completeCaseFromSentQuote, newCaseCommandKey } from "../caseCommands";
import { Button, FormField, TextInput } from "../ui";

const NEW = "__new__";

/**
 * «Completar con la cotización del correo» (2026-10-13): confirm the institution and the API makes
 * the sent quote a case in «Enviada» with that institution and the quote, in one transaction. The
 * institution field starts from what the PDF name says (`institutionFromQuoteFile`); «Buscar»
 * offers the CRM's institutions; leaving «Crear … por confirmar» selected creates it like the
 * email rules do. Without `opportunity`, the case is opened on the email too.
 */
export function CompleteSentQuote({
  sourceRecordId,
  quoteNumber,
  suggestion,
  opportunity,
  onDone,
}: {
  sourceRecordId: string;
  quoteNumber: string;
  suggestion: string | null;
  opportunity?: { id: string; version: number };
  onDone: (caseId: string) => void;
}) {
  const [name, setName] = useState(suggestion ?? "");
  const [candidates, setCandidates] = useState<{ id: string; name: string }[]>([]);
  const [choice, setChoice] = useState<string>(NEW);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const key = useRef(newCaseCommandKey());

  async function search() {
    if (!name.trim()) return;
    setError(null);
    try {
      const page = await fetchV2Organizations({ q: name.trim(), limit: 10 });
      setCandidates(page.items.map((o) => ({ id: o.organization_id, name: o.name })));
    } catch (err) {
      setError(caseRefusalText(err));
    }
  }

  async function complete(e: FormEvent) {
    e.preventDefault();
    if (busy || (choice === NEW && name.trim().length < 3)) return;
    setBusy(true);
    setError(null);
    try {
      const institution =
        choice === NEW
          ? { new_organization_name: name.trim() }
          : await fetchOrganizationAuthoring(choice).then((r) => ({
              organization_id: r.organization.id,
              organization_version: r.organization.version,
            }));
      const receipt = await completeCaseFromSentQuote(
        {
          source_record_id: sourceRecordId,
          ...(opportunity ? { opportunity_id: opportunity.id, opportunity_version: opportunity.version } : {}),
          ...institution,
          // No number here: the API derives and records it; this note is durable.
          note: "Completado con la cotización del correo",
        },
        key.current,
      );
      onDone(String(receipt.opportunity_id));
    } catch (err) {
      setError(caseRefusalText(err));
    } finally {
      setBusy(false);
    }
  }

  const ready = choice !== NEW || name.trim().length >= 3;
  return (
    <form onSubmit={complete} className="space-y-2 rounded-md border border-brand-600/30 bg-brand-50/40 p-3">
      <p className="text-[13px] text-ink">
        Cotización <span className="font-semibold tabular-nums">{quoteNumber}</span>: confirma la institución y el caso queda
        en «Enviada» con la cotización registrada.
      </p>
      <FormField label="Institución" htmlFor={`complete-${sourceRecordId}`}>
        <div className="flex gap-2">
          <TextInput
            id={`complete-${sourceRecordId}`}
            value={name}
            placeholder="Nombre de la institución"
            onChange={(value) => {
              setName(value);
              setChoice(NEW);
            }}
          />
          <Button onClick={() => void search()} disabled={!name.trim()}>
            Buscar
          </Button>
        </div>
      </FormField>
      {candidates.length ? (
        <fieldset className="space-y-1 text-[13px]">
          <legend className="text-[11px] text-ink-muted">¿Es una de estas?</legend>
          {candidates.map((c) => (
            <label key={c.id} className="flex items-center gap-2">
              <input type="radio" name={`inst-${sourceRecordId}`} checked={choice === c.id} onChange={() => setChoice(c.id)} />
              {c.name}
            </label>
          ))}
          <label className="flex items-center gap-2">
            <input type="radio" name={`inst-${sourceRecordId}`} checked={choice === NEW} onChange={() => setChoice(NEW)} />
            Crear «{name.trim()}» por confirmar
          </label>
        </fieldset>
      ) : null}
      {error ? <p role="alert" className="text-xs text-bad">{error}</p> : null}
      <Button type="submit" variant="primary" disabled={!ready} busy={busy} busyLabel="Completando…">
        Completar
      </Button>
    </form>
  );
}
