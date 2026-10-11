/**
 * «Cotizaciones no leídas» in Datos → «Acciones automáticas»: the quote PDFs the quote reader
 * (apps/worker quote_reader.py) refused, so a person can see why a case shows «PDF aún no leído».
 * Read-only; a refusal is retried by the reader after a week, and a PDF read later drops off.
 */
import { fetchQuoteReadings } from "../crmApi";
import { crmHash } from "../crmRoute";
import type { QuoteReading } from "../crmTypes";
import { Panel, ResourceGate, Skeleton, fmtDate } from "../ui";
import { useResource } from "../useResource";

const REASON: Record<QuoteReading["code"], string> = {
  not_origenlab_quote: "No parece una cotización de OrigenLab",
  too_many_disputed: "Muchas líneas no cuadran con el PDF",
  net_mismatch: "El neto no cuadra con las líneas",
  model_refused: "El lector no pudo leerla",
  unreadable: "La respuesta del lector no se pudo usar",
  no_text_layer: "El PDF no tiene texto (¿escaneado?)",
  request_failed: "El lector rechazó el PDF (¿protegido o muy grande?)",
  write_failed: "Un valor del PDF no cabe en el registro",
  hash_mismatch: "El correo guardado no coincide con el original",
  attachment_not_in_eml: "El PDF no está en el correo guardado",
  other: "Motivo no reconocido",
};

export function QuoteReadingsPanel() {
  const [state, reload] = useResource(fetchQuoteReadings);
  return (
    <Panel title="Cotizaciones no leídas">
      <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={2} />}>
        {(data) =>
          data.items.length === 0 ? (
            <p className="text-xs text-ink-muted">El lector leyó todas las cotizaciones registradas.</p>
          ) : (
            <ul className="divide-y divide-line text-[13px]">
              {data.items.map((r, i) => (
                <li key={`${r.quote_number}-${r.revision_no}-${i}`} className="flex flex-wrap items-baseline gap-x-3 py-2" data-testid="quote-reading-row">
                  <span className="font-mono font-semibold text-ink">
                    {r.quote_number ?? "Sin número"}
                    {r.revision_no ? ` r${r.revision_no}` : ""}
                  </span>
                  <span className="text-ink">{REASON[r.code] ?? REASON.other}</span>
                  {r.opportunity_id ? (
                    <a href={crmHash("oportunidades", r.opportunity_id)} className="text-brand-700 hover:underline">
                      {r.case_title ?? "Abrir caso"}
                    </a>
                  ) : null}
                  <span className="ml-auto text-[11px] text-ink-faint tabular-nums">{fmtDate(r.recorded_at)}</span>
                </li>
              ))}
            </ul>
          )
        }
      </ResourceGate>
    </Panel>
  );
}
