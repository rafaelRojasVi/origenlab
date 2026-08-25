import type { CommercialWorkQueueOpportunity } from "../../../api/commercialOperationsTypes";
import {
  commercialOpportunityStageLabel,
  commercialOpportunityTokenLabel,
} from "../../../lib/commercialOpportunityFormat";

export function CommercialReviewCard({
  item,
  onOpenOpportunity,
}: {
  item: CommercialWorkQueueOpportunity;
  onOpenOpportunity: (opportunityId: string) => void;
}) {
  return (
    <article className="rounded-lg border border-amber-200 bg-amber-50/60 p-3">
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

      <dl className="mt-3 grid gap-1 text-xs text-[var(--color-muted)]">
        <div>
          <dt className="inline font-medium">Etapa máquina: </dt>
          <dd className="inline">
            {commercialOpportunityStageLabel(item.canonical_stage)}
          </dd>
        </div>

        <div>
          <dt className="inline font-medium">Confirmación humana: </dt>
          <dd className="inline">
            {item.confirmation_status
              ? commercialOpportunityTokenLabel(
                  item.confirmation_status,
                )
              : "Pendiente"}
          </dd>
        </div>

        {item.owner_key ? (
          <div>
            <dt className="inline font-medium">Responsable: </dt>
            <dd className="inline">{item.owner_key}</dd>
          </div>
        ) : null}
      </dl>

      <button
        type="button"
        onClick={() => onOpenOpportunity(item.opportunity_id)}
        aria-label={`Abrir oportunidad ${item.opportunity_id}`}
        className="mt-3 text-xs font-medium text-brand-700 hover:text-brand-900"
      >
        Revisar ciclo →
      </button>
    </article>
  );
}
