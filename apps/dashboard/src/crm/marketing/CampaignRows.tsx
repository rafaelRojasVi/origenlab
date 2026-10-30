import type { CampaignSummary } from "../crmTypes";
import { Badge, fmtInt } from "../ui";
import { CAMPAIGN_METRIC_LABEL, pct, type CampaignMetric, type MarketingAnalytics } from "./analytics";
import type { OpenPeople } from "./MarketingAnalytics";

const MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];

/** «20–27 oct», «2–3 sep», «17 sep», «28 sep – 3 oct». */
export function sendRange(first: string | null, last: string | null): string {
  if (!first) return "—";
  const d = (s: string) => ({ day: Number(s.slice(8, 10)), month: MONTHS[Number(s.slice(5, 7)) - 1] });
  const a = d(first);
  const b = d(last ?? first);
  if (a.month === b.month) return a.day === b.day ? `${a.day} ${a.month}` : `${a.day}–${b.day} ${a.month}`;
  return `${a.day} ${a.month} – ${b.day} ${b.month}`;
}

const COUNTS: CampaignMetric[] = ["sent", "responded", "quote_request", "cases_after"];

/**
 * One row per sent campaign, newest first: what really happened. Every figure opens the people
 * it counts; the row opens the campaign's detail. «—» is a figure the CRM cannot measure.
 */
export function CampaignRows({
  campaigns,
  analytics,
  onDetail,
  onPeople,
}: {
  campaigns: CampaignSummary[];
  analytics: MarketingAnalytics | null;
  onDetail: (campaignId: string) => void;
  onPeople: OpenPeople;
}) {
  const rows = [...campaigns].sort((a, b) => (b.first_sent_at ?? "").localeCompare(a.first_sent_at ?? ""));
  return (
    <div className="overflow-hidden rounded-lg border border-line bg-canvas-raised" data-testid="campaign-rows">
      <div className="hidden grid-cols-[minmax(0,1.5fr)_100px_minmax(0,1.6fr)_repeat(4,84px)_16px] items-center gap-3 border-b border-line px-4 py-2 text-[11px] font-medium text-ink-faint md:grid">
        <span>Campaña</span>
        <span>Fechas</span>
        <span>Entrega</span>
        {COUNTS.map((m) => (
          <span key={m} className="text-right">{CAMPAIGN_METRIC_LABEL[m]}</span>
        ))}
        <span />
      </div>
      {rows.map((c) => {
        const f = analytics?.campaigns[c.campaign_id] ?? null;
        const sent = f?.sent ?? c.totals?.sent ?? 0;
        const bounced = f?.bounced ?? null;
        const baja = f?.baja ?? null;
        const ok = Math.max(sent - (bounced ?? 0) - (baja ?? 0), 0);
        return (
          <div
            key={c.campaign_id}
            role="button"
            tabIndex={0}
            onClick={() => onDetail(c.campaign_id)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onDetail(c.campaign_id);
              }
            }}
            className="grid cursor-pointer grid-cols-2 items-center gap-x-3 gap-y-1.5 border-b border-line px-4 py-3 last:border-b-0 hover:bg-canvas-sunken/50 md:grid-cols-[minmax(0,1.5fr)_100px_minmax(0,1.6fr)_repeat(4,84px)_16px]"
            data-testid="campaign-row"
          >
            <div className="col-span-2 min-w-0 md:col-span-1">
              <span className="text-[13px] font-semibold text-ink">{c.name}</span>
              {c.hold?.held ? (
                <span className="ml-1.5"><Badge tone="bad" title={c.hold.refusals.map((r) => r.label).join(" · ")}>Bloqueada</Badge></span>
              ) : null}
              {f?.ndr_unblocked ? (
                <span className="ml-1.5"><Badge tone="warn">{`${fmtInt(f.ndr_unblocked)} rebotes sin bloquear`}</Badge></span>
              ) : null}
            </div>
            <span className="text-xs text-ink-muted">{sendRange(c.first_sent_at, c.last_sent_at)}</span>
            <div className="col-span-2 md:col-span-1">
              <div className="flex h-2 overflow-hidden rounded-full bg-canvas-sunken" aria-hidden="true">
                <span className="bg-accent" style={{ width: `${(ok / Math.max(sent, 1)) * 100}%` }} />
                <span className="bg-bad" style={{ width: `${((bounced ?? 0) / Math.max(sent, 1)) * 100}%` }} />
                <span className="bg-warn" style={{ width: `${((baja ?? 0) / Math.max(sent, 1)) * 100}%` }} />
              </div>
              <p className="mt-1 text-[11px] tabular-nums text-ink-faint">
                <Num value={bounced} onClick={() => onPeople({ kind: "campaign", metric: "bounced", campaignId: c.campaign_id }, `${c.name} · Rebotes`)} />{" "}
                {bounced === 1 ? "rebote" : "rebotes"}{pct(bounced, sent) ? ` (${pct(bounced, sent)})` : ""} ·{" "}
                <Num value={baja} onClick={() => onPeople({ kind: "campaign", metric: "baja", campaignId: c.campaign_id }, `${c.name} · Bajas`)} /> {baja === 1 ? "baja" : "bajas"}
                {f?.new_people != null ? (
                  <>
                    {" · "}
                    <Num value={f.new_people} onClick={() => onPeople({ kind: "campaign", metric: "new_people", campaignId: c.campaign_id }, `${c.name} · Personas nuevas`)} />{" "}
                    {f.new_people === 1 ? "persona nueva" : "personas nuevas"}
                  </>
                ) : null}
              </p>
            </div>
            {COUNTS.map((m) => (
              <span key={m} className="text-right text-[13px] tabular-nums text-ink">
                <span className="mr-1 text-[10px] text-ink-faint md:hidden">{CAMPAIGN_METRIC_LABEL[m]}</span>
                <Num
                  value={m === "sent" ? sent : (f?.[m] ?? null)}
                  onClick={() => onPeople({ kind: "campaign", metric: m, campaignId: c.campaign_id }, `${c.name} · ${CAMPAIGN_METRIC_LABEL[m]}`)}
                />
              </span>
            ))}
            <span aria-hidden="true" className="hidden text-ink-faint md:block">›</span>
          </div>
        );
      })}
    </div>
  );
}

function Num({ value, onClick }: { value: number | null; onClick: () => void }) {
  if (value === null) return <span className="text-ink-faint" title="Aún no medido">—</span>;
  if (value === 0) return <span>0</span>;
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      className="tabular-nums hover:underline"
    >
      {fmtInt(value)}
    </button>
  );
}
