import type { CampaignSummary } from "../crmTypes";
import { agoText, fmtLongDay, inText, overviewFigures, PLANNING_LABEL, todayInSantiago } from "./calendar";

/**
 * The top of the Marketing section: how long since the last real send, how long until the next
 * planned one, and four counts. Only recorded figures: no opens, clicks or replies are shown,
 * because none are imported.
 */
export function MarketingOverview({
  campaigns,
  today = todayInSantiago(),
  onOpen,
}: {
  campaigns: CampaignSummary[];
  today?: string;
  onOpen: (campaignId: string) => void;
}) {
  const f = overviewFigures(campaigns, today);
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
