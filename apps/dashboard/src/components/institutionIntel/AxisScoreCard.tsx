import type { AxisBand, InstitutionAxes } from "../../api/institutionIntel/types";
import { AXIS_BAND_LABEL, formatAxisReasonCode } from "../../lib/institutionIntelLabels";

const BAND_TONE: Record<AxisBand, string> = {
  high: "text-emerald-700",
  medium: "text-sky-700",
  low: "text-amber-700",
  none: "text-slate-500",
};

const BAND_BAR: Record<AxisBand, string> = {
  high: "bg-emerald-600 w-[88%]",
  medium: "bg-sky-600 w-[55%]",
  low: "bg-amber-600 w-[25%]",
  none: "bg-slate-300 w-[6%]",
};

function SingleAxisCard({
  label,
  band,
  reasonCodes,
}: {
  label: string;
  band: AxisBand;
  reasonCodes: readonly string[];
}) {
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-3.5 py-3">
      <p className="text-xs font-medium text-[var(--color-muted)]">{label}</p>
      <p className={`mt-0.5 text-base font-bold ${BAND_TONE[band]}`}>{AXIS_BAND_LABEL[band]}</p>
      <div className="mt-2 h-1.5 rounded-full bg-slate-100">
        <div className={`h-full rounded-full ${BAND_BAR[band]}`} />
      </div>
      {reasonCodes.length > 0 ? (
        <ul className="mt-2 space-y-1 text-[11px] text-[var(--color-muted)]">
          {reasonCodes.map((code) => (
            <li key={code} className="pl-2.5 relative before:absolute before:left-0 before:content-['–']">
              {formatAxisReasonCode(code)}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/**
 * Three independent axes — prospect strength, opportunity urgency, contact
 * readiness. This is a validated product decision: they are never combined
 * into one score. A strong historical buyer can have zero urgency; an urgent
 * open tender can have unreachable contacts — collapsing that away would
 * hide exactly the distinction the design exists to surface.
 */
export function AxisScoreCard({ axes }: { axes: InstitutionAxes }) {
  return (
    <div>
      <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-3" data-testid="axis-score-card">
        <SingleAxisCard
          label="Fuerza de prospecto"
          band={axes.prospectStrength.band}
          reasonCodes={axes.prospectStrength.reasonCodes}
        />
        <SingleAxisCard
          label="Urgencia de oportunidad"
          band={axes.opportunityUrgency.band}
          reasonCodes={axes.opportunityUrgency.reasonCodes}
        />
        <SingleAxisCard
          label="Contacto listo"
          band={axes.contactReadiness.band}
          reasonCodes={axes.contactReadiness.reasonCodes}
        />
      </div>
    </div>
  );
}
