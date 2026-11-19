/**
 * «Seguimiento»'s controls: the view switch (a segmented control with a sliding indicator), the
 * filter chips with their counts, the order, the search, «Limpiar» and «Mostrando X de Y».
 */
import { memo } from "react";
import {
  SEG_ORDERS,
  SEG_VIEWS,
  activeFilterCount,
  type FilterGroup,
  type GroupKey,
  type SegFilters,
  type SegOrder,
  type SegView,
} from "../seguimientoView";
import { SearchInput, SelectInput } from "../ui";

export function ViewSwitch({ view, onChange }: { view: SegView; onChange: (v: SegView) => void }) {
  const index = SEG_VIEWS.findIndex((v) => v.key === view);
  return (
    <div role="group" aria-label="Vista" className="relative inline-grid grid-cols-3 rounded-lg border border-line bg-canvas-sunken p-[3px]" data-testid="seg-views">
      {/* The indicator slides under the chosen view (transform only). */}
      <span
        aria-hidden="true"
        className="absolute inset-y-[3px] left-[3px] w-[calc((100%-6px)/3)] rounded-md bg-canvas-raised shadow-[0_0_0_1px_var(--color-line)] motion-safe:transition-transform motion-safe:duration-200 motion-safe:ease-out"
        style={{ transform: `translateX(${index * 100}%)` }}
      />
      {SEG_VIEWS.map((v) => (
        <button
          key={v.key}
          type="button"
          aria-pressed={v.key === view}
          onClick={() => onChange(v.key)}
          className={`relative z-[1] inline-flex h-11 items-center sm:h-8 justify-center whitespace-nowrap rounded-md px-3 text-[13px] font-semibold transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 ${v.key === view ? "text-ink" : "text-ink-muted hover:text-ink"}`}
        >
          {v.label}
        </button>
      ))}
    </div>
  );
}

const CHIP_BASE =
  "inline-flex h-11 items-center gap-1.5 whitespace-nowrap rounded-full border px-3.5 sm:h-8 sm:px-3 text-[12.5px] font-semibold transition-colors duration-150 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600";

/** One group of chips; a chip with nothing to show is dimmed but still pressable (to unpress it). */
const ChipGroup = memo(function ChipGroup({
  group,
  chosen,
  counts,
  onToggle,
}: {
  group: FilterGroup;
  chosen: string[];
  counts: Map<string, number>;
  onToggle: (group: GroupKey, value: string) => void;
}) {
  if (group.options.length === 0) return null;
  return (
    <div role="group" aria-label={group.label} className="flex flex-wrap items-center gap-1.5">
      <span className="mr-0.5 text-[11px] font-bold uppercase tracking-wide text-ink-faint">{group.label}</span>
      {group.options.map((o) => {
        const on = chosen.includes(o.value);
        const n = counts.get(`${group.key}:${o.value}`) ?? 0;
        return (
          <button
            key={o.value}
            type="button"
            aria-pressed={on}
            onClick={() => onToggle(group.key, o.value)}
            className={`${CHIP_BASE} ${
              on
                ? "border-brand-700 bg-brand-700 text-white"
                : `border-line-strong bg-canvas-raised text-ink hover:border-brand-600 hover:text-accent ${n === 0 ? "opacity-60" : ""}`
            }`}
          >
            {o.label}
            <span className={`text-[11px] font-bold tabular-nums ${on ? "text-white/80" : "text-ink-faint"}`}>{n}</span>
          </button>
        );
      })}
    </div>
  );
});

export function FilterBar({
  groups,
  filters,
  counts,
  order,
  q,
  shown,
  total,
  onToggle,
  onOrder,
  onSearch,
  onClear,
}: {
  groups: FilterGroup[];
  filters: SegFilters;
  counts: Map<string, number>;
  order: SegOrder;
  q: string;
  shown: number;
  total: number;
  onToggle: (group: GroupKey, value: string) => void;
  onOrder: (order: SegOrder) => void;
  onSearch: (q: string) => void;
  onClear: () => void;
}) {
  const active = activeFilterCount(filters) > 0 || q.trim() !== "";
  return (
    <section aria-label="Filtros" className="space-y-2.5 rounded-xl border border-line bg-canvas-raised p-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="w-full sm:w-auto">
          <SearchInput value={q} onChange={onSearch} placeholder="Buscar cliente, contacto, N° o producto…" label="Buscar" className="max-sm:h-11 max-sm:text-[14px] sm:w-72" />
        </div>
        <label className="flex items-center gap-2 text-[12px] font-semibold text-ink-muted">
          Orden
          <span className="w-44">
            <SelectInput value={order} onChange={(v) => onOrder(v as SegOrder)} options={SEG_ORDERS.map((o) => ({ value: o.key, label: o.label }))} className="max-sm:min-h-11 max-sm:text-[13px]" />
          </span>
        </label>
        <p className="ml-auto text-[12px] tabular-nums text-ink-muted" aria-live="polite" data-testid="seg-showing">
          Mostrando {shown} de {total}
        </p>
        {active ? (
          <button
            type="button"
            onClick={onClear}
            className="inline-flex h-11 items-center rounded-md px-3 text-[12.5px] font-semibold text-accent sm:h-8 sm:px-2.5 hover:bg-canvas-sunken focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
          >
            Limpiar
          </button>
        ) : null}
      </div>
      <div className="flex flex-wrap gap-x-5 gap-y-2">
        {groups.map((g) => (
          <ChipGroup key={g.key} group={g} chosen={filters[g.key]} counts={counts} onToggle={onToggle} />
        ))}
      </div>
    </section>
  );
}
