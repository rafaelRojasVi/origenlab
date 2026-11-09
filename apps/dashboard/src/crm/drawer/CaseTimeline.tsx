/**
 * «Línea de tiempo»: what happened on the case, newest first — its domain events and each
 * quotation sent (`GET /v2/cockpit/opportunities/{id}/timeline`, which answers oldest first).
 */
import { useCallback } from "react";
import { fetchCaseTimeline } from "../crmApi";
import { ResourceGate, fmtDate } from "../ui";
import { useResource } from "../useResource";
import { timelineLabel } from "./timelineLabels";

export function CaseTimeline({ opportunityId }: { opportunityId: string }) {
  const load = useCallback(() => fetchCaseTimeline(opportunityId), [opportunityId]);
  const [state, reload] = useResource(load, [opportunityId]);
  return (
    <section aria-label="Línea de tiempo">
      <div className="mb-1.5 flex flex-wrap items-baseline gap-x-2">
        <h3 className="text-[11px] font-semibold uppercase tracking-wide text-ink-faint">Línea de tiempo</h3>
        <span className="text-[11px] text-ink-faint">estados, cotizaciones y tareas</span>
      </div>
      {state.kind === "unavailable" ? (
        <p className="text-[12px] text-ink-faint">La línea de tiempo no está habilitada en este entorno.</p>
      ) : (
        <ResourceGate
          state={state}
          reload={reload}
          errorTitle="No se pudo cargar la línea de tiempo"
          skeleton={<div className="crm-skeleton h-16 rounded-md" aria-label="Cargando línea de tiempo" />}
        >
          {(data) =>
            data.entries.length === 0 ? (
              <p className="text-[12px] text-ink-muted">Sin cambios registrados todavía.</p>
            ) : (
              <ul className="space-y-1.5 border-l border-line pl-3" data-testid="case-timeline">
                {[...data.entries].reverse().map((e, i) => (
                  <li key={`${e.occurred_at}:${i}`} className="relative text-[12.5px] leading-5 text-ink">
                    <span aria-hidden="true" className="absolute -left-[17px] top-[7px] h-2 w-2 rounded-full border border-line-strong bg-canvas-raised" />
                    <span className="mr-1.5 text-[11px] text-ink-faint tabular-nums">{fmtDate(e.occurred_at)}</span>
                    {timelineLabel(e)}
                  </li>
                ))}
              </ul>
            )
          }
        </ResourceGate>
      )}
    </section>
  );
}
