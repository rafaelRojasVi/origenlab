import { useState } from "react";
import { Panel, fmtInt } from "../ui";
import { useResource } from "../useResource";
import { CAMPAIGN_METRIC_LABEL, fetchMarketingAnalytics, pct, type CampaignMetric } from "./analytics";
import { PeopleList } from "./PeopleList";

const SHOWN: CampaignMetric[] = ["sent", "new_people", "bounced", "baja", "responded", "quote_request", "cases_after"];

/** What the campaign produced, as the CRM measures it; each figure opens the people it counts. */
export function CampaignResults({ campaignId, campaignName }: { campaignId: string; campaignName: string }) {
  const [state] = useResource(fetchMarketingAnalytics);
  const [open, setOpen] = useState<CampaignMetric | null>(null);
  if (state.kind !== "ready") return null;
  const f = state.data.campaigns[campaignId];
  if (!f) return null;
  return (
    <div className="space-y-3">
      <Panel title="Resultados" note={`respuestas hasta ${state.data.reply_window_days} días después del envío`} bodyClassName="p-3">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4" data-testid="campaign-results">
          {SHOWN.map((m) => {
            const v = f[m];
            const share = m === "bounced" || m === "responded" ? pct(v, f.sent) : null;
            return (
              <button
                key={m}
                type="button"
                disabled={!v}
                onClick={() => setOpen(m)}
                className="rounded-md border border-line bg-canvas-raised px-3 py-2 text-left enabled:hover:border-line-strong"
                data-testid={`result-${m}`}
              >
                <span className="block text-[11px] text-ink-faint">{CAMPAIGN_METRIC_LABEL[m]}</span>
                <span className="block text-lg font-semibold tabular-nums text-ink">
                  {v === null ? <span className="text-ink-faint" title="Aún no medido">—</span> : fmtInt(v)}
                  {share ? <span className="ml-1.5 text-[11px] font-normal text-ink-faint">{share}</span> : null}
                </span>
              </button>
            );
          })}
        </div>
      </Panel>
      {open ? (
        <PeopleList
          key={open}
          title={`${campaignName} · ${CAMPAIGN_METRIC_LABEL[open]}`}
          query={{ kind: "campaign", metric: open, campaignId }}
          onClose={() => setOpen(null)}
        />
      ) : null}
    </div>
  );
}
