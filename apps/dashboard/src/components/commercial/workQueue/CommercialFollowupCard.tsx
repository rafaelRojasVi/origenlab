import type { CommercialWorkQueueOpportunity } from "../../../api/commercialOperationsTypes";
import { commercialOpportunityStageLabel } from "../../../lib/commercialOpportunityFormat";

export function CommercialFollowupCard({
  item,
  onOpenOpportunity,
}: {
  item: CommercialWorkQueueOpportunity;
  onOpenOpportunity: (opportunityId: string) => void;
}) {
  return (
    <article className="rounded-lg border border-[var(--color-border)] bg-slate-50/70 p-3">
      <p className="font-medium text-slate-900">
        {item.account_display_domain ??
          item.contact_display_email ??
          "Cuenta sin identificar"}
      </p>

      {item.contact_display_email &&
      item.account_display_domain ? (
        <p className="mt-1 text-xs text-slate-600">
          {item.contact_display_email}
        </p>
      ) : null}

      <p className="mt-3 text-xs text-[var(--color-muted)]">
        Etapa: {commercialOpportunityStageLabel(item.canonical_stage)}
      </p>

      {item.owner_key ? (
        <p className="mt-1 text-xs text-[var(--color-muted)]">
          Responsable: {item.owner_key}
        </p>
      ) : null}

      <button
        type="button"
        onClick={() => onOpenOpportunity(item.opportunity_id)}
        aria-label={`Abrir oportunidad ${item.opportunity_id}`}
        className="mt-3 text-xs font-medium text-brand-700 hover:text-brand-900"
      >
        Ver seguimiento →
      </button>
    </article>
  );
}
