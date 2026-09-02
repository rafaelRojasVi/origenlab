import type { CustomerQuoteGlobalItem, QuoteProvisioningStatus } from "../../api/customerQuoteTypes";
import { formatCommercialOpportunityDate } from "../../lib/commercialOpportunityFormat";

const DRIVE_STATE_LABELS: Record<QuoteProvisioningStatus, "Drive listo" | "Aprovisionando" | "Error de Drive"> = {
  ready: "Drive listo",
  pending: "Aprovisionando",
  failed: "Error de Drive",
};

const DRIVE_BADGE_CLASS: Record<string, string> = {
  "Drive listo": "bg-emerald-50 text-emerald-800 border-emerald-200",
  "Aprovisionando": "bg-slate-100 text-slate-700 border-slate-200",
  "Error de Drive": "bg-amber-50 text-amber-900 border-amber-200",
};

/** A durable CRM quote's Kanban card: draggable (dragged out only while no
 * command is already pending), and a click opens the full drawer. */
export function CotizacionesCard({
  item,
  onOpen,
  dragDisabled,
}: {
  item: CustomerQuoteGlobalItem;
  onOpen: () => void;
  dragDisabled: boolean;
}) {
  const driveLabel = DRIVE_STATE_LABELS[item.quote.drive_workspace.provisioning_status];

  return (
    <article
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen();
        }
      }}
      draggable={!dragDisabled}
      onDragStart={(event) => {
        if (dragDisabled) {
          event.preventDefault();
          return;
        }
        event.dataTransfer.setData("text/plain", item.quote.quote_id);
        event.dataTransfer.effectAllowed = "move";
      }}
      className="motion-safe:transition-shadow motion-safe:duration-150 cursor-pointer space-y-2 rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-3 shadow-sm hover:shadow-md focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600"
    >
      <div className="flex items-start justify-between gap-2">
        <p className="min-w-0 truncate text-sm font-semibold text-slate-900">{item.quote.quote_number}</p>
        <span className="shrink-0 text-xs text-slate-500">{item.quote.document_number}</span>
      </div>

      <p className="truncate text-sm text-slate-700">
        {item.organization_display_name ?? item.quote.sales_opportunity_title}
      </p>
      <p className="truncate text-xs text-[var(--color-muted)]">
        {item.contact_display_name ?? item.contact_primary_email ?? "Sin contacto"}
      </p>

      <div className="flex items-center justify-between gap-2 pt-1">
        <span className={`rounded-full border px-2 py-0.5 text-xs font-medium ${DRIVE_BADGE_CLASS[driveLabel]}`}>
          {driveLabel}
        </span>
        <span className="shrink-0 text-xs text-[var(--color-muted)]">
          {formatCommercialOpportunityDate(item.quote.revision_updated_at)}
        </span>
      </div>
    </article>
  );
}
