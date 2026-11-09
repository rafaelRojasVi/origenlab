/**
 * «Línea de tiempo»: what happened on the case, newest first — its domain events and each
 * quotation sent (`GET /v2/cockpit/opportunities/{id}/timeline`, which answers oldest first). The
 * newest `SHOWN` lines, the rest behind «Ver todo»; `timelineLines` drops the wordless ones.
 */
import { useCallback, useState } from "react";
import { fetchCaseTimeline } from "../crmApi";
import { ResourceGate, fmtDate } from "../ui";
import { useResource } from "../useResource";
import { timelineLines } from "./timelineLabels";

const SHOWN = 5;

export function CaseTimeline({ opportunityId }: { opportunityId: string }) {
  const load = useCallback(() => fetchCaseTimeline(opportunityId), [opportunityId]);
  const [state, reload] = useResource(load, [opportunityId]);
  const [all, setAll] = useState(false);
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
          {(data) => {
            const lines = timelineLines(data.entries);
            if (lines.length === 0) return <p className="text-[12px] text-ink-muted">Sin cambios registrados todavía.</p>;
            const shown = all ? lines : lines.slice(0, SHOWN);
            return (
              <>
                <ul className="space-y-1.5 border-l border-line pl-3" data-testid="case-timeline">
                  {shown.map((l, i) => (
                    <li key={`${l.at}:${i}`} className="relative text-[12.5px] leading-5 text-ink">
                      <span aria-hidden="true" className="absolute -left-[17px] top-[7px] h-2 w-2 rounded-full border border-line-strong bg-canvas-raised" />
                      <span className="mr-1.5 text-[11px] text-ink-faint tabular-nums">{fmtDate(l.at)}</span>
                      {l.label}
                    </li>
                  ))}
                </ul>
                {lines.length > SHOWN ? (
                  <button
                    type="button"
                    onClick={() => setAll((v) => !v)}
                    className="mt-1.5 rounded-sm pl-3 text-[12px] font-medium text-accent hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
                  >
                    {all ? "Ver menos" : `Ver todo (${lines.length})`}
                  </button>
                ) : null}
              </>
            );
          }}
        </ResourceGate>
      )}
    </section>
  );
}
