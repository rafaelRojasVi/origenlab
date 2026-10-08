/** A human-reviewed bridge between unrelated Gmail threads: propose, never auto-link. */
import { useCallback, useRef, useState } from "react";
import { fetchCaseQuoteCandidates } from "../crmApi";
import { caseRefusalText, isStaleRefusal, linkCaseQuoteEvidence, newCaseCommandKey } from "../caseCommands";
import type { OpportunityCardData, CrossThreadQuoteCandidate } from "../crmTypes";
import { useResource } from "../useResource";
import { crmHash } from "../crmRoute";
import { Button, FormField, TextareaInput } from "../ui";

export function CrossThreadQuoteReview({
  card,
  onDone,
}: {
  card: OpportunityCardData;
  onDone: (outcome: { tone: "good" | "bad"; lines: string[] }, refetch: boolean) => void;
}) {
  const load = useCallback(() => fetchCaseQuoteCandidates(card.opportunity_id), [card.opportunity_id]);
  const [state] = useResource(load, [card.opportunity_id]);
  const [candidate, setCandidate] = useState<CrossThreadQuoteCandidate | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const key = useRef(newCaseCommandKey());

  async function link() {
    if (!candidate || candidate.recorded_elsewhere || candidate.other_cases_on_quote_thread?.length ||
      !note.trim() || busy || card.version == null) return;
    setBusy(true);
    setError(null);
    try {
      await linkCaseQuoteEvidence(
        {
          opportunity_id: card.opportunity_id,
          opportunity_version: card.version,
          relation: "mentions",
          source_record_id: candidate.source_record_id,
          note: note.trim(),
        },
        key.current,
      );
      setCandidate(null);
      onDone({
        tone: "good",
        lines: [
          "Correo de cotización vinculado como evidencia, sin crear ni marcar ganada una venta.",
          "Continúa con la confirmación del solicitante y después registra el PDF ya enviado.",
        ],
      }, true);
    } catch (err) {
      key.current = newCaseCommandKey();
      setError(caseRefusalText(err));
      if (isStaleRefusal(err)) onDone({ tone: "bad", lines: [caseRefusalText(err)] }, true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="space-y-3 rounded-md border border-line p-3" aria-label="Cotización en otro hilo">
      <strong>Cotización enviada en otro hilo de Gmail</strong>
      <p className="text-xs text-ink-muted">
        Coincidencias basadas en un número CN exacto, un destinatario externo compartido
        y un PDF enviado antes de esta consulta. Ninguna coincidencia se vincula automáticamente.
      </p>
      {state.kind === "loading" ? <p>Buscando evidencia…</p> : null}
      {state.kind === "error" || state.kind === "permission" || state.kind === "unavailable" ? (
        <p role="alert">No se pudieron consultar los otros hilos: {state.message}</p>
      ) : null}
      {state.kind === "ready" && state.data.candidates.length === 0 ? (
        <p>No se encontraron cotizaciones de otro hilo con evidencia suficiente.</p>
      ) : null}
      {state.kind === "ready" ? state.data.candidates.map((item) => (
        <div key={item.source_record_id} className="space-y-2 rounded-md border border-line p-3">
          <p><strong>{item.quote_token}</strong> · {item.filename}</p>
          <p className="text-xs text-ink-muted">{item.reason}</p>
          <p className="text-xs text-ink-muted">{item.subject ?? "Asunto no disponible"}</p>
          <a href={item.gmail_url} target="_blank" rel="noopener noreferrer"
            className="text-brand-700 underline">
            Abrir mensaje original en Gmail ↗
          </a>
          {item.other_cases_on_quote_thread?.length ? (
            <div className="space-y-1 rounded-md border border-warn/40 p-2" role="status">
              <strong>Este hilo ya corresponde a otro caso del CRM.</strong>
              <p>No lo vincules automáticamente con el caso actual; revisa primero
                cuál debe representar la operación comercial.</p>
              {item.other_cases_on_quote_thread.map((other) => (
                <a key={other.opportunity_id} href={crmHash("oportunidades", other.opportunity_id)}
                  className="block text-brand-700 underline">
                  Abrir caso original: {other.title}
                </a>
              ))}
            </div>
          ) : item.recorded_elsewhere ? (
            <p role="alert">
              El PDF ya figura en una revisión del CRM. Revisa la atribución;
              no está permitido registrar otra cotización con el mismo archivo.
            </p>
          ) : (
            <Button onClick={() => { setCandidate(item); setError(null); setNote(""); }}>
              Revisar vínculo con este caso
            </Button>
          )}
        </div>
      )) : null}
      {candidate ? (
        <div className="space-y-2 rounded-md border border-warn/40 p-3">
          <p>Confirma que revisaste ambos correos y que este PDF corresponde al caso.</p>
          <FormField label="Motivo y evidencia del vínculo" htmlFor="cross-thread-note">
            <TextareaInput id="cross-thread-note" value={note} onChange={setNote}
              maxLength={2000} rows={3} />
          </FormField>
          <div className="flex gap-2">
            <Button variant="primary" busy={busy} disabled={busy || !note.trim()}
              onClick={() => void link()}>
              Vincular correo revisado
            </Button>
            <Button disabled={busy} onClick={() => setCandidate(null)}>Cancelar</Button>
          </div>
        </div>
      ) : null}
      {error ? <p role="alert">{error}</p> : null}
    </section>
  );
}
