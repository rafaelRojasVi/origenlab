import type { QuoteQueueRow, CustomerQuoteGlobalItem } from "../../api/customerQuoteTypes";
import { salesOpportunityStageLabel } from "../../lib/salesOpportunityFormat";
import { formatCommercialOpportunityDate } from "../../lib/commercialOpportunityFormat";
import { quoteQueueStateLabel } from "./customerQuoteQueueFilters";
import { DriveLink } from "./driveWorkspaceUi";

const DRIVE_BADGE_CLASS: Record<string, string> = {
  "Drive listo": "bg-emerald-50 text-emerald-800 border-emerald-200",
  "Aprovisionando": "bg-slate-100 text-slate-700 border-slate-200",
  "Error de Drive": "bg-amber-50 text-amber-900 border-amber-200",
};

function CrmQueueRow({
  item,
  onOpenQuote,
}: {
  item: CustomerQuoteGlobalItem;
  onOpenQuote: (item: CustomerQuoteGlobalItem) => void;
}) {
  const state = quoteQueueStateLabel(item);
  return (
    <tr>
      <td className="px-3 py-2 align-top">
        <button
          type="button"
          onClick={() => onOpenQuote(item)}
          className="text-left font-semibold text-brand-700 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-600"
        >
          {item.quote.quote_number}
        </button>
        <div className="text-xs text-slate-500">{item.quote.document_number}</div>
      </td>
      <td className="px-3 py-2 align-top">
        <div className="text-slate-900">{item.organization_display_name ?? "—"}</div>
        <div className="text-xs text-slate-500">
          {item.contact_display_name ?? item.contact_primary_email ?? "—"}
        </div>
      </td>
      <td className="px-3 py-2 align-top text-slate-700">{item.quote.sales_opportunity_title}</td>
      <td className="px-3 py-2 align-top text-slate-700">
        {salesOpportunityStageLabel(item.sales_opportunity_stage)}
      </td>
      <td className="px-3 py-2 align-top">
        <div className="flex flex-wrap gap-1">
          <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-xs font-medium text-slate-700">
            {state.status}
          </span>
          <span
            className={`rounded-full border px-2 py-0.5 text-xs font-medium ${DRIVE_BADGE_CLASS[state.drive]}`}
          >
            {state.drive}
          </span>
        </div>
      </td>
      <td className="px-3 py-2 align-top text-slate-600">
        {formatCommercialOpportunityDate(item.quote.updated_at)}
      </td>
      <td className="px-3 py-2 align-top" />
    </tr>
  );
}

function DrivePendingQueueRow({ item }: { item: QuoteQueueRow & { kind: "drive_pending" } }) {
  const folder = item.item;
  return (
    <tr>
      <td className="px-3 py-2 align-top">
        {folder.document_identifier ? (
          <div className="font-semibold text-slate-900">{folder.document_identifier}</div>
        ) : null}
        <div className={folder.document_identifier ? "text-xs text-slate-500" : "font-semibold text-slate-900"}>
          {folder.folder_name}
        </div>
      </td>
      <td className="px-3 py-2 align-top text-slate-500">—</td>
      <td className="px-3 py-2 align-top text-slate-500">—</td>
      <td className="px-3 py-2 align-top text-slate-500">—</td>
      <td className="px-3 py-2 align-top">
        <div className="flex flex-wrap gap-1">
          <span className="rounded-full border border-sky-200 bg-sky-50 px-2 py-0.5 text-xs font-medium text-sky-800">
            Pendiente en Drive
          </span>
          <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-xs font-medium text-slate-700">
            Sin registro CRM
          </span>
        </div>
      </td>
      <td className="px-3 py-2 align-top text-slate-600">
        {formatCommercialOpportunityDate(folder.modified_time ?? folder.created_time)}
      </td>
      <td className="px-3 py-2 align-top">
        {folder.folder_web_url ? (
          <DriveLink href={folder.folder_web_url} label="Abrir carpeta" />
        ) : null}
      </td>
    </tr>
  );
}

export function CustomerQuoteQueueTable({
  rows,
  onOpenQuote,
}: {
  rows: readonly QuoteQueueRow[];
  onOpenQuote: (item: CustomerQuoteGlobalItem) => void;
}) {
  return (
    <div className="overflow-x-auto rounded-xl border border-[var(--color-border)] bg-[var(--color-card)]">
      <table className="w-full min-w-[960px] text-left text-sm">
        <thead className="border-b border-[var(--color-border)] text-xs uppercase tracking-wide text-slate-500">
          <tr>
            <th className="px-3 py-2 font-medium">Cotización</th>
            <th className="px-3 py-2 font-medium">Cliente</th>
            <th className="px-3 py-2 font-medium">Oportunidad</th>
            <th className="px-3 py-2 font-medium">Etapa</th>
            <th className="px-3 py-2 font-medium">Estado</th>
            <th className="px-3 py-2 font-medium">Actualizada</th>
            <th className="px-3 py-2 font-medium">Acción</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[var(--color-border)]">
          {rows.map((row) =>
            row.kind === "crm" ? (
              <CrmQueueRow key={row.item.quote.quote_id} item={row.item} onOpenQuote={onOpenQuote} />
            ) : (
              <DrivePendingQueueRow key={row.item.folder_id} item={row} />
            ),
          )}
        </tbody>
      </table>
    </div>
  );
}
