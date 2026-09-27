import { useMemo, useState } from "react";
import type { CampaignSummary } from "../crmTypes";
import {
  EVENT_KINDS,
  EVENT_LABEL,
  EVENT_STYLE,
  PLANNING_LABEL,
  WEEKDAYS,
  addMonths,
  buildEvents,
  fmtLongDay,
  fmtMonth,
  monthGrid,
  monthOf,
  relativeDay,
  todayInSantiago,
  type CalendarEvent,
  type EventKind,
} from "./calendar";
import type { EquipmentTaxonomy } from "./marketingTypes";

const NO_LINE = "__none__";

/**
 * A full-size monthly calendar of campaign history and planning. Each event sits on a date the
 * CRM recorded; `planned` events are internal planning and schedule nothing. On a phone the grid
 * becomes an agenda list of the same month.
 */
export function CampaignCalendar({
  campaigns,
  taxonomy,
  today = todayInSantiago(),
  onOpen,
}: {
  campaigns: CampaignSummary[];
  taxonomy: EquipmentTaxonomy | null;
  today?: string;
  onOpen: (campaignId: string) => void;
}) {
  const events = useMemo(() => buildEvents(campaigns), [campaigns]);
  const [month, setMonth] = useState(() => monthOf(today));
  const [kinds, setKinds] = useState<Set<EventKind>>(() => new Set(EVENT_KINDS));
  const [line, setLine] = useState<string>("");

  const familyName = (id: string) => taxonomy?.families.find((f) => f.id === id)?.name ?? id;
  const lines = useMemo(() => {
    const ids = new Set(events.flatMap((e) => e.familyIds));
    return [...ids].sort();
  }, [events]);
  const visible = events.filter(
    (e) => kinds.has(e.kind) && (!line || (line === NO_LINE ? e.familyIds.length === 0 : e.familyIds.includes(line))),
  );
  const byDay = new Map<string, CalendarEvent[]>();
  for (const e of visible) byDay.set(e.day, [...(byDay.get(e.day) ?? []), e]);
  const inMonth = visible.filter((e) => monthOf(e.day) === month);
  const weeks = monthGrid(month);

  const toggleKind = (k: EventKind) =>
    setKinds((prev) => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });

  return (
    <section className="rounded-lg border border-line bg-canvas-raised" data-testid="campaign-calendar" aria-label="Calendario de campañas">
      <header className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2.5">
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => setMonth((m) => addMonths(m, -1))}
            aria-label="Mes anterior"
            className="h-7 w-7 rounded-md border border-line text-ink-muted hover:bg-canvas-sunken"
          >
            ‹
          </button>
          <button
            type="button"
            onClick={() => setMonth((m) => addMonths(m, 1))}
            aria-label="Mes siguiente"
            className="h-7 w-7 rounded-md border border-line text-ink-muted hover:bg-canvas-sunken"
          >
            ›
          </button>
          <button
            type="button"
            onClick={() => setMonth(monthOf(today))}
            className="h-7 rounded-md border border-line px-2.5 text-xs font-medium text-ink hover:bg-canvas-sunken"
          >
            Hoy
          </button>
        </div>
        <h2 className="min-w-[10rem] text-base font-semibold tracking-tight text-ink" aria-live="polite" data-testid="calendar-month">
          {fmtMonth(month)}
        </h2>
        <div className="ml-auto flex flex-wrap items-center gap-1.5" role="group" aria-label="Filtrar por estado">
          {EVENT_KINDS.map((k) => (
            <button
              key={k}
              type="button"
              aria-pressed={kinds.has(k)}
              onClick={() => toggleKind(k)}
              className={`inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-xs font-medium transition-opacity ${
                kinds.has(k) ? "border-line-strong text-ink" : "border-line text-ink-faint opacity-60"
              }`}
            >
              <span aria-hidden="true" className={`h-2 w-2 rounded-full ${EVENT_STYLE[k].dot}`} />
              {EVENT_LABEL[k]}
            </button>
          ))}
          <label className="sr-only" htmlFor="calendar-line">
            Línea de equipo
          </label>
          <select
            id="calendar-line"
            value={line}
            onChange={(e) => setLine(e.target.value)}
            className="h-7 rounded-md border border-line bg-canvas-raised px-2 text-xs text-ink"
          >
            <option value="">Todas las líneas</option>
            {lines.map((id) => (
              <option key={id} value={id}>
                {familyName(id)}
              </option>
            ))}
            <option value={NO_LINE}>Sin línea</option>
          </select>
        </div>
      </header>

      {/* Desktop and tablet: the month grid. */}
      <div className="hidden md:block" data-testid="calendar-grid">
        <div className="grid grid-cols-7 border-b border-line text-[11px] font-semibold uppercase tracking-wide text-ink-faint">
          {WEEKDAYS.map((d) => (
            <div key={d} className="px-2 py-1.5">
              {d}
            </div>
          ))}
        </div>
        <div className="grid grid-cols-7">
          {weeks.flat().map(({ day, inMonth: current }) => {
            const list = byDay.get(day) ?? [];
            const isToday = day === today;
            return (
              <div
                key={day}
                data-day={day}
                className={`min-h-[7.5rem] border-b border-r border-line p-1.5 last:border-r-0 [&:nth-child(7n)]:border-r-0 ${
                  current ? "bg-canvas-raised" : "bg-canvas-sunken/60"
                }`}
              >
                <div className="mb-1 flex items-center justify-between">
                  <span
                    className={`inline-flex h-6 min-w-6 items-center justify-center rounded-full px-1 text-xs tabular-nums ${
                      isToday ? "bg-ink font-semibold text-white" : current ? "text-ink" : "text-ink-faint"
                    }`}
                    aria-label={isToday ? `Hoy, ${fmtLongDay(day)}` : fmtLongDay(day)}
                  >
                    {Number(day.slice(8))}
                  </span>
                </div>
                <ul className="space-y-1">
                  {list.map((e) => (
                    <li key={e.id}>
                      <EventChip e={e} today={today} onOpen={onOpen} />
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
        </div>
      </div>

      {/* Phone: the same month as an agenda. */}
      <div className="md:hidden" data-testid="calendar-agenda">
        {inMonth.length === 0 ? (
          <p className="px-3 py-6 text-center text-xs text-ink-muted">Sin eventos este mes.</p>
        ) : (
          <ol className="divide-y divide-line">
            {groupByDay(inMonth).map(([day, list]) => (
              <li key={day} className="px-3 py-2">
                <p className="text-[11px] font-semibold text-ink-muted">
                  {fmtLongDay(day)} · <span className="font-normal">{relativeDay(day, today)}</span>
                </p>
                <ul className="mt-1 space-y-1">
                  {list.map((e) => (
                    <li key={e.id}>
                      <EventChip e={e} today={today} onOpen={onOpen} wide />
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ol>
        )}
      </div>

      <footer className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-line px-3 py-2 text-[11px] text-ink-faint">
        <span>Fechas en hora de Santiago. Los envíos muestran cada lote real registrado.</span>
        <span className="font-medium text-warn">{PLANNING_LABEL}</span>
        <span>
          <span className="rounded border border-ink-faint px-1 text-[10px]">V1</span> campaña histórica importada
        </span>
      </footer>
    </section>
  );
}

function EventChip({ e, today, onOpen, wide = false }: { e: CalendarEvent; today: string; onOpen: (id: string) => void; wide?: boolean }) {
  const rel = relativeDay(e.day, today);
  return (
    <button
      type="button"
      onClick={() => onOpen(e.campaignId)}
      data-testid="calendar-event"
      data-kind={e.kind}
      data-origin={e.origin}
      title={`${EVENT_LABEL[e.kind]} · ${e.campaignName} · ${e.detail}${e.time ? ` · ${e.time}` : ""} · ${rel}`}
      className={`block w-full rounded-md border px-1.5 py-1 text-left text-[11px] leading-tight hover:brightness-95 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-brand-600 ${EVENT_STYLE[e.kind].chip}`}
    >
      <span className="flex items-center gap-1">
        {e.origin === "imported_v1" ? (
          <span className="shrink-0 rounded border border-current px-0.5 text-[9px] font-semibold opacity-80" aria-label="Histórica importada">
            V1
          </span>
        ) : null}
        <span className={`${wide ? "" : "truncate"} font-semibold`}>{e.campaignName}</span>
      </span>
      <span className={`block ${wide ? "" : "truncate"} opacity-90`}>
        {EVENT_LABEL[e.kind]} · {e.detail}
        {e.time ? ` · ${e.time}` : ""}
      </span>
      <span className="block opacity-80">{rel}</span>
    </button>
  );
}

function groupByDay(events: CalendarEvent[]): [string, CalendarEvent[]][] {
  const map = new Map<string, CalendarEvent[]>();
  for (const e of events) map.set(e.day, [...(map.get(e.day) ?? []), e]);
  return [...map.entries()];
}
