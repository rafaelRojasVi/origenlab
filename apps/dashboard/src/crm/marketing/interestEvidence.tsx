/**
 * How an equipment interest is shown, wherever it is shown: the Marketing audience and the CRM
 * cards (people, institutions, equipment lines) render the same evidence the same way.
 */

import { useMemo } from "react";
import { crmHash } from "../crmRoute";
import { Badge, fmtDate, type Tone } from "../ui";
import type { AudienceInterest, EquipmentTaxonomy, InterestBasis } from "./marketingTypes";

export const BASIS_TONE: Record<InterestBasis, Tone> = {
  purchased: "good",
  requested_quotation: "brand",
  requested_information: "info",
  inferred_relevance: "neutral",
};

/** "IKA T 25 digital", or "IKA (marca)" when the evidence names only the brand. */
export function useInterestLabel(t: EquipmentTaxonomy) {
  return useMemo(() => {
    const brands = new Map(t.brands.map((b) => [b.id, b.name]));
    const models = new Map(t.models.map((m) => [m.id, m.name]));
    return (i: AudienceInterest) => (i.model_id ? `${brands.get(i.brand_id)} ${models.get(i.model_id)}` : `${brands.get(i.brand_id)} (marca)`);
  }, [t]);
}

export function EvidenceList({ interests, label }: { interests: AudienceInterest[]; label: (i: AudienceInterest) => string }) {
  return (
    <ul className="divide-y divide-line rounded-md border border-line">
      {interests.map((i, n) => (
        <li key={n} className="space-y-0.5 px-3 py-2 text-xs">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="font-semibold text-ink">{label(i)}</span>
            <Badge tone={BASIS_TONE[i.basis]} glyph={false}>{i.basis_label}</Badge>
            <span className="ml-auto text-[11px] tabular-nums text-ink-muted">{i.date ? fmtDate(i.date) : "sin fecha"}</span>
          </div>
          <p className="text-[11px] text-ink-muted">
            {i.source.label}
            {i.recorded_in_crm ? (i.confirmation === "confirmed" ? " · confirmado por operador" : i.confirmation === "machine_proposed" ? " · propuesto por la máquina" : "") : " · no registrado como interés en el CRM"}
          </p>
          <p className="text-[11px] text-ink-faint">
            Coincidencia «{i.matched_term}» en: {i.source.detail ?? i.source.case_title ?? "—"}
          </p>
          {i.source.opportunity_id ? (
            <a className="text-[11px] font-medium text-brand-700 underline" href={crmHash("oportunidades", i.source.opportunity_id)}>
              Ver caso{i.source.quote_numbers.length ? ` · ${i.source.quote_numbers.join(", ")}` : ""}
            </a>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
