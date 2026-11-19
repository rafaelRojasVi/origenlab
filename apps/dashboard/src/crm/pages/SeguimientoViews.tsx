/**
 * The other two ways to see «Seguimiento», and the sheet on a phone, all as cards (`SegCard`):
 *
 * - **Agenda**: grouped by the day of the next contact (Atrasados · Hoy · Mañana · Esta semana ·
 *   Más adelante · Sin fecha), Santiago days, empty groups hidden.
 * - **Por encargado**: a column per person plus «Sin confirmar». A card dragged to another column
 *   (native drag and drop) changes its encargado; the picker on each card is the keyboard and touch way.
 */
import { useRef, useState, type DragEvent } from "react";
import type { ProfileCard } from "../../api/authClient";
import { useMayAuthorCrm } from "../authoring/authoring";
import { useMayRunCaseCommands } from "../caseCommands";
import { formatMoney } from "../money";
import { useFlip, usePresence } from "../motion";
import type { SegRow } from "../seguimiento";
import { agendaGroups, boardColumns } from "../seguimientoView";
import { SegCard, type CardHandlers } from "./SegCard";

const rowKey = (r: SegRow) => r.card.opportunity_id;

interface ViewProps {
  rows: SegRow[];
  profiles: ProfileCard[];
  now: Date;
  clock?: () => Date;
  callsPaused: boolean;
  on: CardHandlers;
}

function usePermissions() {
  const mayCommand = useMayRunCaseCommands();
  return { mayCommand, mayCall: useMayAuthorCrm() && mayCommand };
}

/** Cards in a column, with leave and move motion. */
function CardStack({ rows, label, ownerPicker = false, showPhone = false, onDragStart, listClassName = "flex flex-col gap-2", ...props }: ViewProps & {
  label: string;
  listClassName?: string;
  ownerPicker?: boolean;
  showPhone?: boolean;
  onDragStart?: (row: SegRow, e: DragEvent<HTMLElement>) => void;
}) {
  const perms = usePermissions();
  const shown = usePresence(rows, rowKey);
  const ref = useRef<HTMLUListElement>(null);
  useFlip(ref, rows.map(rowKey).join("|"));
  return (
    <ul ref={ref} aria-label={label} className={listClassName}>
      {shown.map(({ item, key, leaving }) => (
        <li key={key}>
          <SegCard
            row={item}
            leaving={leaving}
            ownerPicker={ownerPicker}
            showPhone={showPhone}
            onDragStart={onDragStart ? (e) => onDragStart(item, e) : undefined}
            {...perms}
            {...props}
          />
        </li>
      ))}
    </ul>
  );
}

/** The sheet on a phone: the same rows as stacked cards. */
export function SegCardList(props: ViewProps) {
  return (
    <div data-testid="seg-cards">
      <CardStack {...props} label="Casos" showPhone />
    </div>
  );
}

export function SeguimientoAgenda(props: ViewProps) {
  const groups = agendaGroups(props.rows, props.now);
  return (
    <div className="space-y-5" data-testid="seg-agenda">
      {groups.map((g) => (
        <section key={g.key} aria-labelledby={`agenda-${g.key}`} className="space-y-2">
          <h2 id={`agenda-${g.key}`} className="flex items-baseline gap-2 text-[13px] font-bold text-ink">
            <span className={g.key === "atrasados" ? "text-bad" : ""}>{g.label}</span>
            <span className="text-[12px] font-semibold tabular-nums text-ink-faint">{g.rows.length}</span>
          </h2>
          <CardStack {...props} rows={g.rows} label={g.label} showPhone listClassName="grid gap-2 sm:grid-cols-2 2xl:grid-cols-3" />
        </section>
      ))}
    </div>
  );
}

export function SeguimientoBoard({ onMove, ...props }: ViewProps & { onMove: (row: SegRow, ownerId: string | null) => void }) {
  const columns = boardColumns(props.rows, props.profiles);
  const [over, setOver] = useState<string | null>(null);
  const dragged = useRef<SegRow | null>(null);
  const colKey = (ownerId: string | null) => ownerId ?? "sin";

  function drop(ownerId: string | null, e: DragEvent<HTMLElement>) {
    e.preventDefault();
    setOver(null);
    const id = e.dataTransfer?.getData("text/plain") || dragged.current?.card.opportunity_id;
    const row = props.rows.find((r) => r.card.opportunity_id === id) ?? dragged.current;
    dragged.current = null;
    if (row && row.ownerId !== ownerId) onMove(row, ownerId);
  }

  return (
    <div
      data-testid="seg-board"
      className="relative -mx-1 flex snap-x snap-mandatory gap-3 overflow-x-auto px-1 pb-2 md:mx-0 md:grid md:snap-none md:grid-cols-[repeat(auto-fit,minmax(15rem,1fr))] md:overflow-visible md:px-0"
    >
      {columns.map((c) => {
        const key = colKey(c.ownerId);
        const money = c.rows.reduce((s, r) => s + (r.amountClp ?? 0), 0);
        return (
          <section
            key={key}
            aria-labelledby={`board-${key}`}
            data-testid={`seg-col-${key}`}
            data-over={over === key || undefined}
            onDragOver={(e) => {
              e.preventDefault();
              if (e.dataTransfer) e.dataTransfer.dropEffect = "move";
              if (over !== key) setOver(key);
            }}
            onDragLeave={(e) => {
              if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOver((o) => (o === key ? null : o));
            }}
            onDrop={(e) => drop(c.ownerId, e)}
            className={`flex w-[17rem] shrink-0 snap-start flex-col gap-2 rounded-xl border p-2 transition-colors duration-150 md:w-auto ${
              over === key ? "border-brand-600 bg-brand-50" : "border-line bg-canvas-sunken/60"
            }`}
          >
            <h2 id={`board-${key}`} className="flex items-baseline gap-2 px-1 pt-1 text-[13px] font-bold text-ink">
              {c.label}
              <span className="text-[12px] font-semibold tabular-nums text-ink-faint">{c.rows.length}</span>
              <span className="ml-auto text-[12px] font-semibold tabular-nums text-ink-muted">{formatMoney(String(money), "CLP")}</span>
            </h2>
            {c.rows.length === 0 ? (
              <p className="rounded-lg border border-dashed border-line-strong px-3 py-6 text-center text-[12px] text-ink-faint">Arrastra un caso aquí</p>
            ) : null}
            <CardStack
              {...props}
              rows={c.rows}
              label={c.label}
              ownerPicker
              onDragStart={(row, e) => {
                dragged.current = row;
                e.dataTransfer?.setData("text/plain", row.card.opportunity_id);
                if (e.dataTransfer) e.dataTransfer.effectAllowed = "move";
              }}
            />
          </section>
        );
      })}
    </div>
  );
}
