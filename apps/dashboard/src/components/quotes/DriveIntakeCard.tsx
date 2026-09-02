import type { DrivePendingQuoteItem } from "../../api/customerQuoteTypes";
import { DriveLink } from "./driveWorkspaceUi";

/**
 * A Drive-only folder with no durable CRM identity yet. Deliberately never
 * draggable (see resolveBoardMove: any drag out of drive_intake is
 * refused) -- "Incorporar al CRM" is the only path into a CRM lane.
 */
export function DriveIntakeCard({
  item,
  onAdopt,
}: {
  item: DrivePendingQuoteItem;
  onAdopt: () => void;
}) {
  return (
    <article className="space-y-2 rounded-xl border border-dashed border-sky-300 bg-sky-50/40 p-3">
      {item.document_identifier ? (
        <p className="truncate text-sm font-semibold text-slate-900">{item.document_identifier}</p>
      ) : null}
      <p
        className={
          item.document_identifier
            ? "truncate text-xs text-slate-500"
            : "truncate text-sm font-semibold text-slate-900"
        }
      >
        {item.folder_name}
      </p>

      <span className="inline-flex rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-xs font-medium text-slate-700">
        Sin registro CRM
      </span>

      <div className="flex items-center justify-between gap-2 pt-1">
        {item.folder_web_url ? <DriveLink href={item.folder_web_url} label="Abrir carpeta" /> : <span />}
        <button
          type="button"
          onClick={onAdopt}
          className="shrink-0 rounded-md bg-brand-600 px-2 py-1 text-xs font-semibold text-white hover:bg-brand-700"
        >
          Incorporar al CRM
        </button>
      </div>
    </article>
  );
}
