/**
 * «Ventas › Seguimiento» as a sheet: one row per open case with a sent quote, one fact per column, a
 * fixed header, a frozen first column (Encargado), Monto and Sin respuesta as orders, and a totals
 * row. The case name opens the case; «Llamé» opens the inline call form under its row; «⋯» holds
 * «Pausar…» and «Ya no sigue». Rows that leave fade out, rows that move glide (`motion.ts`).
 */
import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import type { ProfileCard } from "../../api/authClient";
import { useMayAuthorCrm } from "../authoring/authoring";
import { useMayRunCaseCommands, type CaseCommandReceipt } from "../caseCommands";
import { contactLine, displayName, quoteProduct } from "../caseDisplay";
import { formatMoney } from "../money";
import { useFlip, usePresence } from "../motion";
import { quoteNumberOf, type SegRow } from "../seguimiento";
import type { SegOrder } from "../seguimientoView";
import { Button, initials } from "../ui";
import { caseGmailUrl } from "./CaseQuotes";
import { FollowUpPicker } from "./FollowUpPicker";
import { cardMoney, shortDate } from "./PipelineBoard";
import { SeguimientoCallRow } from "./SeguimientoCallRow";
import {
  DaysPill,
  ICON_BUTTON,
  Icon,
  LastTouch,
  MAIL_ICON,
  NextDate,
  PhoneLink,
  RowMenu,
  SCRIPT_ICON,
  SORT_ICON,
  nextReminder,
  type RowAction,
} from "./SegParts";

export { daysTone } from "./SegParts";

const COLUMNS = 11;

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

export function SeguimientoSheet({
  rows,
  profiles,
  now,
  selectedId,
  scrollToId = null,
  order = "prioridad",
  onOrder,
  callsPaused = false,
  clock,
  ...on
}: {
  rows: SegRow[];
  profiles: ProfileCard[];
  now: Date;
  selectedId: string | null;
  /** Bring this row into view once it is on screen (`?caso=`). */
  scrollToId?: string | null;
  order?: SegOrder;
  /** Monto and Sin respuesta in the header choose the page's order. */
  onOrder?: (order: SegOrder) => void;
  /** The cases are being read again: a call now would replace a reminder that is already gone. */
  callsPaused?: boolean;
  /** The real time when a call is saved; the sheet's `now` is only for what it shows. */
  clock?: () => Date;
} & Partial<SheetHandlers> & Pick<SheetHandlers, "onSelect" | "onGuion" | "onCallSaved" | "onReceipt" | "onConflict">) {
  const [callOpen, setCallOpen] = useState<string | null>(null);
  const mayCommand = useMayRunCaseCommands();
  // A call is a note (CRM authoring) plus its reminder (a case command).
  const mayCall = useMayAuthorCrm() && mayCommand;
  const shown = usePresence(rows, rowKey);
  const total = rows.reduce((sum, r) => sum + (r.amountClp ?? 0), 0);
  const callsToday = rows.reduce((sum, r) => sum + r.callsToday, 0);
  const bodyRef = useRef<HTMLTableSectionElement>(null);
  useFlip(bodyRef, rows.map(rowKey).join("|"));

  const rowRefs = useRef(new Map<string, HTMLTableRowElement>());
  const scrolled = useRef<string | null>(null);
  useEffect(() => {
    if (!scrollToId || scrolled.current === scrollToId) return;
    const el = rowRefs.current.get(scrollToId);
    if (!el) return;
    scrolled.current = scrollToId;
    el.scrollIntoView?.({ block: "center" });
  }, [scrollToId, rows]);

  const nameOf = (id: string | null) => profiles.find((p) => p.id === id)?.displayName ?? null;

  const sortButton = (key: SegOrder, label: string, right = false) => {
    const active = order === key;
    return (
      <th
        scope="col"
        aria-sort={active ? "descending" : "none"}
        className={`sticky top-0 z-10 border-b border-line bg-canvas-sunken p-0 ${right ? "text-right" : "text-left"}`}
      >
        <button
          type="button"
          onClick={() => onOrder?.(active ? "prioridad" : key)}
          className={`inline-flex h-9 w-full items-center gap-1 whitespace-nowrap px-3 text-[11px] font-bold hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-600 ${right ? "justify-end" : ""} ${active ? "text-ink" : "text-ink-muted"}`}
        >
          {label}
          <span className={active ? "text-ink" : "text-ink-faint"}>
            <Icon d={SORT_ICON} />
          </span>
        </button>
      </th>
    );
  };
  const th = (label: ReactNode, cls = "") => (
    <th scope="col" className={`sticky top-0 z-10 h-9 whitespace-nowrap border-b border-line bg-canvas-sunken px-3 text-left text-[11px] font-bold text-ink-muted ${cls}`}>
      {label}
    </th>
  );
  const td = "border-b border-line px-3 py-2.5 align-middle";

  return (
    <div className="overflow-hidden rounded-xl border border-line bg-canvas-raised" data-testid="seg-sheet">
      {/* `relative`: the screen-reader-only labels inside (absolute) are clipped here, not by the page. */}
      <div className="relative max-h-[calc(100vh-17rem)] min-h-64 overflow-auto">
        <table className="w-full min-w-[76rem] border-separate border-spacing-0 text-[13px] tabular-nums">
          <thead>
            <tr>
              {th("Encargado", "left-0 !z-20 w-44 min-w-44 shadow-[1px_0_0] shadow-line")}
              {th("Cliente y contacto")}
              {th("Teléfono")}
              {th("Cotización")}
              {sortButton("monto", "Monto", true)}
              {th("Enviada")}
              {sortButton("dias", "Sin respuesta")}
              {th("Último toque")}
              {th("Llamadas", "text-right")}
              {th("Próximo", "w-36")}
              {th(<span className="sr-only">Acciones</span>)}
            </tr>
          </thead>
          <tbody ref={bodyRef}>
            {shown.map(({ item: r, key: id, leaving }) => {
              const selected = id === selectedId;
              return (
                <Fragment key={id}>
                  <tr
                    ref={(el) => {
                      if (el) rowRefs.current.set(id, el);
                      else rowRefs.current.delete(id);
                    }}
                    data-flip={leaving ? undefined : id}
                    data-leaving={leaving || undefined}
                    aria-current={selected ? "true" : undefined}
                    onClick={() => on.onSelect(id)}
                    className={`transition-colors ${leaving ? "crm-row-out" : ""} ${selected ? "bg-brand-50" : "bg-canvas-raised hover:bg-canvas-sunken"}`}
                    data-testid={`seg-row-${id}`}
                  >
                    <td className={`${td} sticky left-0 z-[1] bg-inherit shadow-[1px_0_0] shadow-line`}>
                      <div className="flex items-start gap-2">
                        <span
                          aria-hidden="true"
                          className={`mt-1 grid h-6 w-6 shrink-0 place-items-center rounded-full text-[10px] font-bold ${r.ownerId || r.suggestedOwnerId ? "bg-canvas-sunken text-ink-muted" : "border border-dashed border-line-strong text-ink-faint"}`}
                        >
                          {r.ownerId || r.suggestedOwnerId ? initials(nameOf(r.ownerId ?? r.suggestedOwnerId)) : "?"}
                        </span>
                        <div className="min-w-0 flex-1">
                          {mayCommand ? (
                            <FollowUpPicker card={r.card} profiles={profiles} onChanged={on.onReceipt} onConflict={on.onConflict} compact only="owner" />
                          ) : (
                            <span className="text-[12.5px] text-ink">{nameOf(r.ownerId) ?? "Sin confirmar"}</span>
                          )}
                        </div>
                      </div>
                    </td>
                    <td className={td}>
                      <CaseName row={r} onOpen={on.onOpen} />
                      <div className="max-w-[15rem] truncate text-[12px] text-ink-muted">{contactLine(r.card) ?? "Sin contacto"}</div>
                    </td>
                    <td className={`${td} whitespace-nowrap`}>
                      <PhoneLink row={r} />
                    </td>
                    <td className={td}>
                      <span className="whitespace-nowrap font-mono text-[12px]">
                        {quoteNumberOf(r.card) ?? "Sin número"}
                        {r.card.quote_numbers.length > 1 ? ` +${r.card.quote_numbers.length - 1}` : ""}
                      </span>
                      <div className="max-w-[13rem] truncate text-[12px] text-ink-muted" title={quoteProduct(r.card) ?? undefined}>
                        {quoteProduct(r.card) ?? ""}
                      </div>
                    </td>
                    <td className={`${td} whitespace-nowrap text-right font-bold text-ink`}>{cardMoney(r.card).text || <span className="text-ink-faint">Sin monto</span>}</td>
                    <td className={`${td} whitespace-nowrap text-[12px] text-ink-muted`}>{r.sentAt ? shortDate(r.sentAt, now) : "Sin fecha"}</td>
                    <td className={td}>
                      <DaysPill row={r} now={now} />
                    </td>
                    <td className={`${td} text-[12px] leading-snug text-ink-muted`}>
                      <LastTouch row={r} now={now} />
                    </td>
                    <td className={`${td} whitespace-nowrap text-right`}>
                      <span data-testid="seg-calls" className="font-bold">
                        <span className={r.callsToday > 0 ? "text-accent" : "text-ink"}>{r.callsToday}</span>
                        <small className="text-[11px] font-semibold text-ink-faint"> hoy</small> · {r.callsTotal}
                      </span>
                    </td>
                    <td className={`${td} min-w-[9.5rem]`}>
                      {mayCommand ? (
                        <FollowUpPicker card={r.card} profiles={profiles} onChanged={on.onReceipt} onConflict={on.onConflict} compact only="channel" />
                      ) : null}
                      <NextDate due={nextReminder(r)} now={now} />
                    </td>
                    <td className={`${td} text-right`}>
                      <Actions
                        row={r}
                        open={callOpen === id}
                        onGuion={() => on.onGuion(id)}
                        onToggle={() => setCallOpen(callOpen === id ? null : id)}
                        onAction={mayCommand && on.onAction ? (a) => on.onAction!(id, a) : undefined}
                        mayCall={mayCall}
                        paused={callsPaused}
                      />
                    </td>
                  </tr>
                  {callOpen === id && !leaving ? (
                    <SeguimientoCallRow
                      row={r}
                      colSpan={COLUMNS}
                      clock={clock}
                      onCancel={() => setCallOpen(null)}
                      onSaved={(said) => {
                        setCallOpen(null);
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
              <td className="sticky bottom-0 left-0 z-20 border-t border-line bg-canvas-sunken px-3 py-2.5 shadow-[1px_0_0] shadow-line">
                {rows.length} casos
              </td>
              <td colSpan={3} className="sticky bottom-0 z-10 border-t border-line bg-canvas-sunken" />
              <td className="sticky bottom-0 z-10 whitespace-nowrap border-t border-line bg-canvas-sunken px-3 py-2.5 text-right" data-testid="seg-total-money">
                {formatMoney(String(total), "CLP")}
              </td>
              <td colSpan={3} className="sticky bottom-0 z-10 border-t border-line bg-canvas-sunken" />
              <td className="sticky bottom-0 z-10 whitespace-nowrap border-t border-line bg-canvas-sunken px-3 py-2.5 text-right">{callsToday} hoy</td>
              <td colSpan={2} className="sticky bottom-0 z-10 border-t border-line bg-canvas-sunken" />
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
  );
}

/** The case's name: a button that opens it, or plain text where no drawer is offered. */
export function CaseName({ row, onOpen }: { row: SegRow; onOpen?: (id: string) => void }) {
  const name = displayName(row.card).name;
  const cls = "block max-w-[15rem] truncate text-left font-bold leading-tight text-ink";
  if (!onOpen) return <div className={cls} title={name}>{name}</div>;
  return (
    <button
      type="button"
      title={`Abrir el caso de ${name}`}
      onClick={(e) => {
        e.stopPropagation();
        onOpen(row.card.opportunity_id);
      }}
      className={`${cls} rounded-sm hover:text-accent hover:underline hover:underline-offset-2 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600`}
    >
      {name}
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
}: {
  row: SegRow;
  open: boolean;
  mayCall: boolean;
  paused: boolean;
  onGuion?: () => void;
  onToggle: () => void;
  onAction?: (action: RowAction) => void;
}) {
  const client = displayName(row.card).name;
  const gmail = caseGmailUrl(row.card);
  return (
    <div className="flex items-center justify-end gap-1.5">
      {gmail ? (
        <a
          href={gmail}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={`Abrir el correo de ${client} en Gmail`}
          title="Abrir el hilo en Gmail"
          onClick={(e) => e.stopPropagation()}
          className={ICON_BUTTON}
        >
          <Icon d={MAIL_ICON} />
        </a>
      ) : (
        <span className="w-9" aria-hidden="true" />
      )}
      {onGuion ? (
        <button type="button" aria-label={`Ver guion para ${client}`} title="Guion de llamada" onClick={onGuion} className={ICON_BUTTON}>
          <Icon d={SCRIPT_ICON} />
        </button>
      ) : null}
      {mayCall ? (
        <Button variant="primary" aria-expanded={open} disabled={paused && !open} onClick={onToggle} className="h-9">
          Llamé
        </Button>
      ) : null}
      {onAction ? <RowMenu client={client} onAction={onAction} /> : null}
    </div>
  );
}
