/**
 * «Ventas › Seguimiento» as a sheet (≥ 1280 px; narrower screens get the cards): one row per open
 * case with a sent quote, a fixed header, Monto and Sin respuesta as orders, and a totals row. It
 * fits the screen with no sideways scroll: the contact, quote and latest note sit under the client,
 * the last touch and the next contact share a cell, and Gmail and the guion live in «⋯».
 *
 * The case name opens the case; «Llamé» opens the inline call form under its row (one at a time,
 * kept in `segStore` so a refetch never closes it); in selection mode a checkbox per row.
 */
import { Fragment, useEffect, useId, useRef, type ReactNode } from "react";
import type { ProfileCard } from "../../api/authClient";
import { useMayAuthorCrm } from "../authoring/authoring";
import { useMayRunCaseCommands, type CaseCommandReceipt } from "../caseCommands";
import { contactLine, displayName, quoteProduct } from "../caseDisplay";
import { formatMoney } from "../money";
import { useFlip, usePresence } from "../motion";
import { quoteNumberOf, type SegRow } from "../seguimiento";
import type { SegOrder } from "../seguimientoView";
import { useSegUi } from "../segStore";
import { Button } from "../ui";
import { caseGmailUrl } from "./CaseQuotes";
import { FollowUpPicker } from "./FollowUpPicker";
import { cardMoney, shortDate } from "./PipelineBoard";
import { SeguimientoCallRow } from "./SeguimientoCallRow";
import {
  ContactButtons,
  DaysPill,
  Icon,
  LastNote,
  LastTouch,
  NextDate,
  RowMenu,
  SORT_ICON,
  nextReminder,
  type RowAction,
} from "./SegParts";

export { daysTone } from "./SegParts";

const rowKey = (r: SegRow) => r.card.opportunity_id;

export interface SheetHandlers {
  onSelect: (id: string) => void;
  /** Open the case (the same drawer as Pipeline and Hoy). */
  onOpen: (id: string) => void;
  onAction: (id: string, action: RowAction) => void;
  onGuion: (id: string) => void;
  /** A call was saved on the row: gets the next contact as said in the toast. */
  onCallSaved: (id: string, nextSaid: string) => void;
  onReceipt: (receipt: CaseCommandReceipt) => void;
  onConflict: () => void;
}

/** Selection mode: a checkbox per row and «Seleccionar todos los visibles». */
export interface SheetSelection {
  selected: ReadonlySet<string>;
  onToggle: (id: string) => void;
  onToggleAll: () => void;
}

export function SeguimientoSheet({
  rows,
  profiles,
  now,
  selectedId,
  flashId = null,
  order = "prioridad",
  onOrder,
  callsPaused = false,
  clock,
  selection,
  ...on
}: {
  rows: SegRow[];
  profiles: ProfileCard[];
  now: Date;
  selectedId: string | null;
  /** Highlight this row for a moment (`?caso=`, «Modo llamadas»). */
  flashId?: string | null;
  order?: SegOrder;
  /** Monto and Sin respuesta in the header choose the page's order. */
  onOrder?: (order: SegOrder) => void;
  /** The cases are being read again: a call now would replace a reminder that is already gone. */
  callsPaused?: boolean;
  /** The real time when a call is saved; the sheet's `now` is only for what it shows. */
  clock?: () => Date;
  selection?: SheetSelection | null;
} & Partial<SheetHandlers> & Pick<SheetHandlers, "onSelect" | "onGuion" | "onCallSaved" | "onReceipt" | "onConflict">) {
  const [ui, act] = useSegUi();
  const mayCommand = useMayRunCaseCommands();
  // A call is a note (CRM authoring) plus its reminder (a case command).
  const mayCall = useMayAuthorCrm() && mayCommand;
  const shown = usePresence(rows, rowKey);
  const total = rows.reduce((sum, r) => sum + (r.amountClp ?? 0), 0);
  const callsToday = rows.reduce((sum, r) => sum + r.callsToday, 0);
  const bodyRef = useRef<HTMLTableSectionElement>(null);
  useFlip(bodyRef, rows.map(rowKey).join("|"));
  const selecting = !!selection;
  const columns = selecting ? 8 : 7;
  const allSelected = selecting && rows.length > 0 && rows.every((r) => selection.selected.has(rowKey(r)));

  const rowRefs = useRef(new Map<string, HTMLTableRowElement>());
  const scrolled = useRef<string | null>(null);
  useEffect(() => {
    if (!flashId || scrolled.current === flashId) return;
    const el = rowRefs.current.get(flashId);
    if (!el) return;
    scrolled.current = flashId;
    el.scrollIntoView?.({ block: "center", behavior: "smooth" });
  }, [flashId, rows]);

  const sortButton = (key: SegOrder, label: string, right = false) => {
    const active = order === key;
    return (
      <th scope="col" aria-sort={active ? "descending" : "none"} className={`${TH} p-0 ${right ? "text-right" : "text-left"}`}>
        <button
          type="button"
          onClick={() => onOrder?.(active ? "prioridad" : key)}
          className={`inline-flex h-9 w-full items-center gap-1 whitespace-nowrap px-2.5 text-[11px] font-bold hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-600 ${right ? "justify-end" : ""} ${active ? "text-ink" : "text-ink-muted"}`}
        >
          {label}
          <span className={active ? "text-ink" : "text-ink-faint"}>
            <Icon d={SORT_ICON} />
          </span>
        </button>
      </th>
    );
  };
  const th = (label: ReactNode, cls = "") => <th scope="col" className={`${TH} h-9 whitespace-nowrap px-2.5 text-left text-[11px] font-bold text-ink-muted ${cls}`}>{label}</th>;
  const td = "border-b border-line px-2.5 py-2.5 align-top";

  return (
    // `overflow-clip`, not `overflow-hidden`: the rounded corners clip, and the header still sticks under the top bar.
    <div className="overflow-clip rounded-xl border border-line bg-canvas-raised" data-testid="seg-sheet">
      <table className="w-full table-fixed border-separate border-spacing-0 text-[13px] tabular-nums">
        <colgroup>
          {selecting ? <col className="w-11" /> : null}
          <col className="w-[9rem]" />
          <col />
          <col className="w-[6.75rem]" />
          <col className="w-[6.5rem]" />
          <col className="w-[11rem]" />
          <col className="w-[8.5rem]" />
          <col className="w-[8.5rem]" />
        </colgroup>
        <thead>
          <tr>
            {selecting ? (
              <th scope="col" className={`${TH} h-9 px-2.5`}>
                <input
                  type="checkbox"
                  aria-label="Seleccionar todos los visibles"
                  checked={allSelected}
                  onChange={selection.onToggleAll}
                  className="h-5 w-5 accent-[var(--color-brand-700)]"
                />
              </th>
            ) : null}
            {th("Encargado")}
            {th("Cliente, contacto y cotización")}
            {sortButton("monto", "Monto", true)}
            {sortButton("dias", "Sin respuesta")}
            {th("Último toque · Próximo")}
            {th("Teléfono · Llamadas")}
            {th(<span className="sr-only">Acciones</span>)}
          </tr>
        </thead>
        <tbody ref={bodyRef}>
          {shown.map(({ item: r, key: id, leaving }) => {
            const selected = id === selectedId;
            const flash = id === flashId;
            const checked = selection?.selected.has(id) ?? false;
            const client = displayName(r.card).name;
            const callOpen = ui.callOpen === id && !leaving;
            return (
              <Fragment key={id}>
                <tr
                  ref={(el) => {
                    if (el) rowRefs.current.set(id, el);
                    else rowRefs.current.delete(id);
                  }}
                  data-flip={leaving ? undefined : id}
                  data-leaving={leaving || undefined}
                  data-flash={flash || undefined}
                  aria-current={selected ? "true" : undefined}
                  onClick={() => on.onSelect(id)}
                  className={`transition-colors duration-300 ${leaving ? "crm-row-out" : ""} ${flash ? "seg-flash" : ""} ${selected || checked ? "bg-brand-50" : "bg-canvas-raised hover:bg-canvas-sunken"}`}
                  data-testid={`seg-row-${id}`}
                >
                  {selecting ? (
                    <td className={td} onClick={(e) => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        aria-label={`Seleccionar ${client}`}
                        checked={checked}
                        onChange={() => selection.onToggle(id)}
                        className="mt-0.5 h-5 w-5 accent-[var(--color-brand-700)]"
                      />
                    </td>
                  ) : null}
                  <td className={td}>
                    {mayCommand ? (
                      <FollowUpPicker card={r.card} profiles={profiles} onChanged={on.onReceipt} onConflict={on.onConflict} compact only="owner" announce />
                    ) : (
                      <span className="text-[12.5px] text-ink">{profiles.find((p) => p.id === r.ownerId)?.displayName ?? "Sin confirmar"}</span>
                    )}
                  </td>
                  <td className={`${td} min-w-0`}>
                    <CaseName row={r} onOpen={on.onOpen} />
                    <div className="truncate text-[12px] text-ink-muted">{contactLine(r.card) ?? "Sin contacto"}</div>
                    <div className="truncate text-[12px] text-ink-muted" title={quoteProduct(r.card) ?? undefined}>
                      <span className="font-mono text-[11.5px] text-ink">
                        {quoteNumberOf(r.card) ?? "Sin número"}
                        {r.card.quote_numbers.length > 1 ? ` +${r.card.quote_numbers.length - 1}` : ""}
                      </span>
                      {quoteProduct(r.card) ? ` · ${quoteProduct(r.card)}` : ""}
                    </div>
                    <LastNote row={r} now={now} />
                  </td>
                  <td className={`${td} whitespace-nowrap text-right font-bold text-ink`}>{cardMoney(r.card).text || <span className="font-normal text-ink-faint">Sin monto</span>}</td>
                  <td className={td}>
                    <DaysPill row={r} now={now} />
                    <div className="mt-1 whitespace-nowrap text-[11.5px] text-ink-faint">{r.sentAt ? `env. ${shortDate(r.sentAt, now)}` : "Sin fecha"}</div>
                  </td>
                  <td className={`${td} text-[12px] leading-snug text-ink-muted`}>
                    <div className="line-clamp-2">
                      <LastTouch row={r} now={now} />
                    </div>
                    <div className="mt-1 flex items-center gap-1.5">
                      <span className="text-ink-faint">Próximo:</span>
                      <NextDate due={nextReminder(r)} now={now} />
                    </div>
                    {mayCommand ? (
                      <div className="mt-1">
                        <FollowUpPicker card={r.card} profiles={profiles} onChanged={on.onReceipt} onConflict={on.onConflict} compact only="channel" />
                      </div>
                    ) : null}
                  </td>
                  <td className={`${td} min-w-0`}>
                    <ContactButtons row={r} profiles={profiles} compact />
                    <div data-testid="seg-calls" className="mt-1 whitespace-nowrap text-[12px] font-bold">
                      <span className={r.callsToday > 0 ? "text-accent" : "text-ink"}>{r.callsToday}</span>
                      <small className="text-[11px] font-semibold text-ink-faint"> hoy</small> · {r.callsTotal}
                      <small className="text-[11px] font-semibold text-ink-faint"> {r.callsTotal === 1 ? "llamada" : "llamadas"}</small>
                    </div>
                  </td>
                  <td className={`${td} text-right`}>
                    <Actions
                      row={r}
                      open={callOpen}
                      onGuion={() => on.onGuion(id)}
                      onToggle={() => act.openCall(callOpen ? null : id)}
                      onAction={mayCommand && on.onAction ? (a) => on.onAction!(id, a) : undefined}
                      mayCall={mayCall}
                      paused={callsPaused}
                    />
                  </td>
                </tr>
                {callOpen ? (
                  <SeguimientoCallRow
                    row={r}
                    colSpan={columns}
                    clock={clock}
                    onCancel={() => act.openCall(null)}
                    onSaved={(said) => {
                      act.openCall(null);
                      on.onCallSaved(id, said);
                    }}
                  />
                ) : null}
              </Fragment>
            );
          })}
        </tbody>
        <tfoot>
          <tr className="text-[12.5px] font-bold text-ink" data-testid="seg-total">
            <td colSpan={selecting ? 3 : 2} className={TF}>
              {rows.length} casos
            </td>
            <td className={`${TF} whitespace-nowrap text-right`} data-testid="seg-total-money">
              {formatMoney(String(total), "CLP")}
            </td>
            <td colSpan={2} className={TF} />
            <td className={`${TF} whitespace-nowrap`}>{callsToday} hoy</td>
            <td className={TF} />
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

/** The header sticks under the 48 px top bar while the page scrolls. */
const TH = "sticky top-12 z-10 border-b border-line bg-canvas-sunken";
const TF = "sticky bottom-0 z-10 border-t border-line bg-canvas-sunken px-2.5 py-2.5";

/** The case's name: a button that opens it, or plain text where no drawer is offered. */
export function CaseName({
  row,
  onOpen,
  wrap = false,
  sub,
}: {
  row: SegRow;
  onOpen?: (id: string) => void;
  wrap?: boolean;
  /** A line under the name (the card's contact) that belongs to the same tap target. */
  sub?: string;
}) {
  const name = displayName(row.card).name;
  const subId = useId();
  // On a card the name wraps (two lines at most) instead of running under the amount.
  const nameCls = `block max-w-full text-left font-bold leading-tight text-ink ${wrap ? "line-clamp-2 break-words" : "truncate"}`;
  const subLine = sub !== undefined ? <span id={subId} className="block truncate text-[12px] font-normal text-ink-muted">{sub}</span> : null;
  if (!onOpen) {
    return (
      <div className="min-w-0">
        <div className={`${nameCls} min-h-6`} title={name}>{name}</div>
        {subLine}
      </div>
    );
  }
  const id = row.card.opportunity_id;
  return (
    <button
      type="button"
      title={`Abrir el caso de ${name}`}
      aria-label={sub !== undefined ? name : undefined}
      aria-describedby={sub !== undefined ? subId : undefined}
      data-case={id}
      onClick={(e) => {
        e.stopPropagation();
        onOpen(id);
      }}
      // With a line under it (a card) the name and that line are one target, 44 px tall on a phone.
      className={`group/name block min-h-6 max-w-full rounded-sm text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 ${sub !== undefined ? "w-full max-sm:min-h-11" : ""}`}
    >
      <span className={`${nameCls} group-hover/name:text-accent group-hover/name:underline group-hover/name:underline-offset-2`}>{name}</span>
      {subLine}
    </button>
  );
}

export function Actions({
  row,
  open,
  mayCall,
  paused,
  onGuion,
  onToggle,
  onAction,
  guionButton = false,
}: {
  row: SegRow;
  open: boolean;
  mayCall: boolean;
  paused: boolean;
  onGuion?: () => void;
  onToggle: () => void;
  onAction?: (action: RowAction) => void;
  /** Show the guion as a button of its own (cards), not only in «⋯». */
  guionButton?: boolean;
}) {
  const client = displayName(row.card).name;
  const gmail = caseGmailUrl(row.card);
  return (
    <div className="flex flex-wrap items-center justify-end gap-1.5 xl:flex-nowrap">
      {guionButton && onGuion ? (
        <Button aria-label={`Ver guion para ${client}`} onClick={onGuion} className="max-sm:h-11">
          Guion
        </Button>
      ) : null}
      {mayCall ? (
        <Button variant="primary" aria-expanded={open} disabled={paused && !open} onClick={onToggle} className="h-11 px-4 text-[13px] sm:h-9">
          Llamé
        </Button>
      ) : null}
      <RowMenu id={row.card.opportunity_id} client={client} onAction={onAction} gmail={gmail} onGuion={guionButton ? undefined : onGuion} />
    </div>
  );
}
