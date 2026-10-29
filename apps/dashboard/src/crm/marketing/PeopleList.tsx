import { useState } from "react";
import { crmHash } from "../crmRoute";
import { Panel, ResourceGate, Skeleton, fmtInt } from "../ui";
import { useResource } from "../useResource";
import { CASE_STAGE_LABEL, fetchMarketingPeople, peopleSearch, type PeopleQuery } from "./analytics";

/**
 * The people one figure counts — the same rows the API counted, fifty at a time. A viewer gets
 * the addresses masked by the API; nothing is masked or filtered here.
 */
export function PeopleList({ title, query, onClose, closable = true }: { title: string; query: PeopleQuery; onClose: () => void; closable?: boolean }) {
  const [offset, setOffset] = useState(0);
  const key = peopleSearch(query, offset);
  const [state, reload] = useResource(() => fetchMarketingPeople(query, offset), [key]);
  return (
    <Panel
      title={title}
      note={state.kind === "ready" ? `${fmtInt(state.data.total)} en total` : undefined}
      aside={
        closable ? (
          <button type="button" onClick={onClose} className="text-xs font-medium text-accent hover:underline">
            Cerrar
          </button>
        ) : undefined
      }
      bodyClassName="divide-y divide-line"
    >
      <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={4} />}>
        {(page) =>
          page.total === 0 ? (
            <p className="px-3 py-3 text-xs text-ink-muted" data-testid="people-empty">Nadie en esta cifra.</p>
          ) : (
            <>
              <ul data-testid="people-list">
                {page.rows.map((r) => (
                  <li key={`${r.address_norm}-${r.opportunity_id ?? ""}`} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 px-3 py-2 text-xs">
                    <span className="min-w-0 font-medium text-ink">
                      {r.person_id ? (
                        <a href={crmHash("personas", r.person_id)} className="hover:underline">{r.name ?? r.address_norm}</a>
                      ) : (
                        r.name ?? r.address_norm
                      )}
                    </span>
                    <span className="font-mono text-[11px] text-ink-muted">{r.address_norm}</span>
                    {r.organization ? <span className="text-ink-muted">{r.organization}</span> : null}
                    {r.opportunity_id ? (
                      <a href={crmHash("oportunidades", r.opportunity_id)} className="ml-auto font-medium text-accent hover:underline">
                        {r.case_title ?? "Caso"}
                        {r.case_stage ? ` · ${CASE_STAGE_LABEL[r.case_stage] ?? r.case_stage}` : ""}
                      </a>
                    ) : null}
                  </li>
                ))}
              </ul>
              {page.total > page.limit ? (
                <div className="flex items-center gap-3 px-3 py-2 text-[11px] text-ink-faint">
                  <span>
                    {fmtInt(page.offset + 1)}–{fmtInt(Math.min(page.offset + page.limit, page.total))} de {fmtInt(page.total)}
                  </span>
                  <button type="button" disabled={page.offset === 0} onClick={() => setOffset(Math.max(0, page.offset - page.limit))}
                          className="font-medium text-accent disabled:text-ink-faint">Anteriores</button>
                  <button type="button" disabled={page.offset + page.limit >= page.total} onClick={() => setOffset(page.offset + page.limit)}
                          className="font-medium text-accent disabled:text-ink-faint">Siguientes</button>
                </div>
              ) : null}
            </>
          )
        }
      </ResourceGate>
    </Panel>
  );
}
