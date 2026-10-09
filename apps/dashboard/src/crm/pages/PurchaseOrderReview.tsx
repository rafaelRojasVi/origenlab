/** A reviewed Gmail PO received in a different thread, never an automatic sales win. */
import { useCallback, useRef, useState, type FormEvent } from "react";
import { fetchCasePurchaseOrderCandidates } from "../crmApi";
import {
  caseRefusalText, isStaleRefusal, linkCaseQuoteEvidence, markCaseWon,
  newCaseCommandKey, winnableRevisions,
} from "../caseCommands";
import type { CasePurchaseOrderCandidate, OpportunityCardData } from "../crmTypes";
import { useResource } from "../useResource";
import { Button, FormField, SelectInput, TextInput } from "../ui";

/** The printed CN token, not the database's older normalized quote number. */
export function printedQuoteCode(filename: string | null | undefined): string | null {
  const match = filename?.match(/(?:^|[\s_-])CN\s*0*(\d+[A-Za-z]?)(?=[\s_.-]|$)/i);
  return match ? match[1].toUpperCase() : null;
}

function enteredQuoteCode(input: string): string | null {
  const match = input.trim().match(/^(?:CN)?0*(\d+[A-Za-z]?)(?:[-/]\d{2})?$/i);
  return match ? match[1].toUpperCase() : null;
}

export function PurchaseOrderReview({
  card, onDone,
}: {
  card: OpportunityCardData;
  onDone: (result: { tone: "good" | "warn" | "bad"; lines: string[] }, refetch: boolean) => void;
}) {
  const load = useCallback(() => fetchCasePurchaseOrderCandidates(card.opportunity_id), [card.opportunity_id]);
  const [state] = useResource(load, [card.opportunity_id]);
  const [candidate, setCandidate] = useState<CasePurchaseOrderCandidate | null>(null);
  const revisions = winnableRevisions(card);
  const [revisionId, setRevisionId] = useState(revisions[0]?.revision_id ?? "");
  const [printed, setPrinted] = useState("");
  const [enteredPO, setEnteredPO] = useState("");
  const [verified, setVerified] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const keys = useRef({ link: newCaseCommandKey(), won: newCaseCommandKey() });
  const selected = revisions.find((r) => r.revision_id === revisionId);
  const actualCode = printedQuoteCode(selected?.document?.filename);
  const matchedPrinted = Boolean(actualCode && enteredQuoteCode(printed) === actualCode);
  const matchedOC = Boolean(candidate?.purchase_order_number &&
    enteredPO.trim() === candidate.purchase_order_number);
  const ready = Boolean(selected && candidate && matchedPrinted && matchedOC && verified &&
    card.stage === "negotiating" && card.version != null && !busy);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!ready || !candidate || !selected) return;
    setBusy(true);
    setError(null);
    const note = `OC ${enteredPO.trim()} recibida en hilo Gmail independiente; operador verificó el PDF de OC, producto, cantidad, valor neto y referencia impresa ${printed.trim()}. La revisión vigente del CRM figura como ${selected.quote_number} r${selected.revision_no}, PDF ${selected.document?.filename ?? "sin nombre"}. La diferencia entre el número base y el sufijo de la revisión se conserva; no se renumeró ninguna cotización. Sin confirmación de pago.`;
    let linked = false;
    try {
      await linkCaseQuoteEvidence({
        opportunity_id: card.opportunity_id,
        opportunity_version: card.version as number,
        relation: "mentions",
        source_record_id: candidate.source_record_id,
        note,
      }, keys.current.link);
      linked = true;
      await markCaseWon({
        opportunity_id: card.opportunity_id, stage: card.stage,
        version: card.version as number,
      }, { quote_id: selected.quote_id, revision_no: selected.revision_no },
      note, { advance: newCaseCommandKey(), won: keys.current.won });
      onDone({ tone: "good", lines: [
        `OC ${enteredPO.trim()} vinculada al caso mediante evidencia de Gmail.`,
        `Caso marcado Ganada contra ${selected.quote_number} r${selected.revision_no}. No se ha registrado un pago.`,
      ] }, true);
    } catch (err) {
      // Never attempt another win or re-link on a blind retry. An audited first
      // step may have succeeded; refresh the case before any further action.
      const explanation = caseRefusalText(err);
      if (linked) {
        onDone({ tone: "warn", lines: [
          "OC vinculada correctamente, pero la venta NO se marcó ganada.",
          explanation, "Actualiza el caso antes de decidir el siguiente paso.",
        ] }, true);
      } else {
        keys.current.link = newCaseCommandKey();
        setError(explanation);
        if (isStaleRefusal(err)) onDone({ tone: "bad", lines: [explanation] }, true);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="space-y-3 rounded-md border border-line p-3" aria-label="Revisar OC de otro hilo">
      <strong>Orden de compra recibida en otro hilo de Gmail</strong>
      <p className="text-xs text-ink-muted">
        Los candidatos comparten un destinatario externo con una cotización enviada y
        tienen un PDF verificado. El sistema NO puede leer aquí la referencia impresa
        dentro de la OC; debes abrirla y comprobar sus ítems antes de ganar el caso.
      </p>
      {state.kind === "loading" ? <p>Buscando órdenes de compra…</p> : null}
      {state.kind === "error" || state.kind === "permission" || state.kind === "unavailable"
        ? <p role="alert">No se pudo revisar la OC: {state.message}</p> : null}
      {state.kind === "ready" && state.data.candidates.length === 0
        ? <p>No se encontraron OC de otros hilos con una coincidencia verificable.</p> : null}
      {state.kind === "ready" && state.data.candidates.map((item) => (
        <div key={item.source_record_id} className="space-y-1 border-b border-line pb-2">
          <p><strong>{item.filename}</strong> · {item.subject}</p>
          <p className="text-xs text-ink-muted">{item.reason}</p>
          <a href={item.gmail_url} target="_blank" rel="noopener noreferrer"
            className="text-brand-700 underline">Abrir correo y PDF de la OC en Gmail ↗</a>
          <div><Button onClick={() => {
            setCandidate(item); setEnteredPO(""); setPrinted(""); setVerified(false); setError(null);
          }}>{candidate?.source_record_id === item.source_record_id
            ? "OC seleccionada" : "Revisar esta OC"}</Button></div>
        </div>
      ))}
      {candidate && (
        <form onSubmit={(e) => void submit(e)} className="space-y-3" aria-label="Confirmar OC y venta">
          <FormField label="Cotización y revisión aceptadas" htmlFor="po-review-revision" required>
            <SelectInput id="po-review-revision" value={revisionId} onChange={setRevisionId}
              options={revisions.map((r) => ({ value: r.revision_id,
                label: `${r.quote_number} r${r.revision_no} · ${r.document?.filename ?? "PDF sin nombre"}` }))} />
          </FormField>
          <FormField label="Número impreso en la OC (ej. 01253A-26)" htmlFor="po-review-printed"
            required hint="Debe coincidir con el código CN del PDF revisado, no sólo con el cliente.">
            <TextInput id="po-review-printed" value={printed} onChange={setPrinted} maxLength={32}/>
          </FormField>
          <FormField label="Número de OC" htmlFor="po-review-number" required
            hint="Cópialo desde el PDF de la orden de compra.">
            <TextInput id="po-review-number" value={enteredPO} onChange={setEnteredPO} maxLength={64} />
          </FormField>
          {printed && !matchedPrinted && <p role="alert">La referencia no coincide con el CN del PDF enviado.</p>}
          <label className="flex items-start gap-2 text-sm">
            <input type="checkbox" checked={verified} onChange={(e) => setVerified(e.target.checked)} />
            <span>Comprobé en el PDF de la OC la referencia de cotización, el artículo,
              la cantidad y el valor neto. Es esta venta, no otra cotización del mismo cliente.</span>
          </label>
          {error ? <p role="alert">{error}</p> : null}
          <div className="flex justify-end">
            <Button type="submit" variant="primary" disabled={!ready} busy={busy} busyLabel="Registrando…">
              Vincular OC y marcar ganada
            </Button>
          </div>
        </form>
      )}
    </section>
  );
}
