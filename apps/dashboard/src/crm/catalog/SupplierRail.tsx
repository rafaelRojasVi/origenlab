/**
 * The supplier rail: «Todos» and one entry per supplier, each with how many products match the
 * current search and kind there. A column on wide screens, a row of chips that scrolls sideways on
 * narrow ones. A supplier with no match stays listed, dimmed, so the list never jumps around.
 */
import { fmtInt } from "../ui";
import type { SupplierSummary } from "./catalogApi";

export function SupplierRail({ suppliers, counts, allCount, value, onChange }: {
  suppliers: SupplierSummary[];
  counts: Record<string, number>;
  allCount: number;
  value: string;
  onChange: (supplierId: string) => void;
}) {
  const entries = [
    { id: "", name: "Todos", count: allCount },
    ...[...suppliers]
      .sort((a, b) => a.display_name.localeCompare(b.display_name, "es"))
      .map((s) => ({ id: s.id, name: s.display_name, count: counts[s.id] ?? 0 })),
  ];
  return (
    <nav aria-label="Proveedores" className="-mx-1 overflow-x-auto lg:mx-0 lg:overflow-visible">
      <ul className="flex gap-1 px-1 lg:flex-col lg:px-0">
        {entries.map((e) => {
          const active = e.id === value;
          return (
            <li key={e.id || "all"} className="shrink-0">
              <button
                type="button"
                aria-pressed={active}
                onClick={() => onChange(e.id)}
                className={`flex w-full items-center justify-between gap-3 rounded-md px-2.5 py-1.5 text-left text-xs transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-brand-600 ${
                  active
                    ? "bg-brand-700 font-semibold text-white"
                    : e.count === 0
                      ? "text-ink-faint hover:bg-canvas-sunken"
                      : "text-ink hover:bg-canvas-sunken"
                }`}
              >
                <span className="truncate">{e.name}</span>
                <span className={`tabular-nums ${active ? "text-white/80" : "text-ink-faint"}`}>{fmtInt(e.count)}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
