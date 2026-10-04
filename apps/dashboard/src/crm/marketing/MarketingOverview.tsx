import type { CampaignSummary, V1LaneCampaign } from "../crmTypes";
import { agoText, fmtLongDay, inText, overviewFigures, PLANNING_LABEL, todayInSantiago, v1LaneBannerState } from "./calendar";

const MONTH_SHORT: Record<string, string> = {
  "01": "ene", "02": "feb", "03": "mar", "04": "abr",
  "05": "may", "06": "jun", "07": "jul", "08": "ago",
  "09": "sep", "10": "oct", "11": "nov", "12": "dic",
};

/** «5 al 9 oct (promo hasta el 11)» */
function formatV1DateRange(campaign: V1LaneCampaign): string {
  const first = campaign.send_days[0];
  const last = campaign.send_days[campaign.send_days.length - 1];
  const firstDay = String(Number(first.slice(8)));
  const lastDay = String(Number(last.slice(8)));
  const promoDay = String(Number(campaign.promo_until.slice(8)));
  const month = MONTH_SHORT[first.slice(5, 7)] ?? first.slice(5, 7);
  return `${campaign.name}, ${firstDay} al ${lastDay} ${month} (promo hasta el ${promoDay})`;
}

/**
 * The top of the Marketing section: how long since the last real send, how long until the next
 * planned one, and four counts. Only recorded figures: no opens, clicks or replies are shown,
 * because none are imported.
 *
 * When a V1-lane campaign is active or upcoming, a banner shows its status and date range.
 * «En curso por el canal V1: …» while it runs; «Programada por el canal V1: …» before it starts;
 * nothing after promo_until.
 */
export function MarketingOverview({
  campaigns,
  today = todayInSantiago(),
  onOpen,
  v1LaneCampaigns = [],
}: {
  campaigns: CampaignSummary[];
  today?: string;
  onOpen: (campaignId: string) => void;
  v1LaneCampaigns?: V1LaneCampaign[];
}) {
  const f = overviewFigures(campaigns, today);

  // Show the first V1-lane campaign that is upcoming or active. After promo_until: hide.
  const activeCampaign = v1LaneCampaigns.find(
    (c) => v1LaneBannerState(c, today) !== "done",
  ) ?? null;

  return (
    <section aria-label="Resumen de marketing" className="grid grid-cols-1 gap-3 lg:grid-cols-[1fr_1fr_1.3fr]" data-testid="marketing-overview">
      <div className="rounded-lg border border-line bg-canvas-raised p-4">
        <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-faint">Último envío</p>
        {f.lastSend ? (
          <>
            <p className="mt-1 text-2xl font-semibold tracking-tight text-ink" data-testid="last-send">
              Último envío: {agoText(f.lastSend.daysAgo)}
            </p>
            <p className="mt-0.5 truncate text-xs text-ink-muted" title={f.lastSend.campaignName}>
              {fmtLongDay(f.lastSend.day)} · {f.lastSend.campaignName}
            </p>
          </>
        ) : (
          <p className="mt-1 text-sm text-ink-muted" data-testid="last-send">Sin envíos registrados</p>
        )}
        {activeCampaign ? (
          <p
            className="mt-2 rounded-md border border-good/40 bg-good-bg px-2 py-1 text-[11px] font-medium text-good"
            data-testid="v1-lane-status"
          >
            {v1LaneBannerState(activeCampaign, today) === "active"
              ? `En curso por el canal V1: ${formatV1DateRange(activeCampaign)}`
              : `Programada por el canal V1: ${formatV1DateRange(activeCampaign)}`}
          </p>
        ) : null}
      </div>
      <div className="rounded-lg border border-dashed border-warn/60 bg-canvas-raised p-4">
        <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-faint">Próxima campaña</p>
        {f.nextPlanned ? (
          <>
            <p className="mt-1 text-2xl font-semibold tracking-tight text-ink" data-testid="next-planned">
              Próxima campaña: {inText(f.nextPlanned.inDays)}
            </p>
            <p className="mt-0.5 truncate text-xs text-ink-muted">
              {fmtLongDay(f.nextPlanned.day)}
              {f.nextPlanned.time ? ` · ${f.nextPlanned.time}` : ""} ·{" "}
              <button type="button" className="font-medium text-brand-700 hover:underline" onClick={() => onOpen(f.nextPlanned!.campaignId)}>
                {f.nextPlanned.campaignName}
              </button>
            </p>
          </>
        ) : (
          <p className="mt-1 text-sm text-ink-muted" data-testid="next-planned">Ninguna campaña planificada</p>
        )}
        <p className="mt-2 text-[11px] font-medium text-warn">{PLANNING_LABEL}</p>
      </div>
      <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-2" data-testid="overview-counts">
        {(
          [
            ["Enviadas", f.sent],
            ["Borradores", f.drafts],
            ["Audiencia congelada", f.frozen],
            ["Planificadas", f.planned],
          ] as const
        ).map(([label, n]) => (
          <div key={label} className="rounded-lg border border-line bg-canvas-raised px-3 py-2">
            <dt className="text-[11px] text-ink-faint">{label}</dt>
            <dd className="text-lg font-semibold tabular-nums text-ink">{n}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
