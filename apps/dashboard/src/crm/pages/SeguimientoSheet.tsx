/**
 * «Ventas › Seguimiento» as a sheet: one row per open case with a sent quote, one fact per column, a
 * fixed header, a frozen first column (Encargado), sortable by Monto and Sin respuesta, and a totals
 * row. «Llamé» opens the inline call form under its row.
 */
import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { ProfileCard } from "../../api/authClient";
import { useMayAuthorCrm } from "../authoring/authoring";
import { useMayRunCaseCommands, type CaseCommandReceipt } from "../caseCommands";
import { contactLine, displayName, quoteProduct } from "../caseDisplay";
import { dateSantiago } from "../goals";
import { formatMoney } from "../money";
import { isMaskedPhone } from "../redaction";
import { quoteNumberOf, touchedToday, type SegRow } from "../seguimiento";
import { isFollowUpTask } from "../today";
import { Button, initials } from "../ui";
import { caseGmailUrl } from "./CaseQuotes";
import { FollowUpPicker } from "./FollowUpPicker";
import { cardMoney, shortDate } from "./PipelineBoard";
import { SeguimientoCallRow } from "./SeguimientoCallRow";

type SortKey = "monto" | "dias";
type DaysTone = "ok" | "warn" | "bad";

const COLUMNS = 11;

const DAYS_PILL: Record<DaysTone, string> = {
  ok: "bg-good-bg text-good",
  warn: "bg-warn-bg text-warn",
  bad: "bg-bad-bg text-bad",
};

/** «Sin respuesta»: under 3 days fine, 3 to 6 a warning, a week or more bad; fine once touched today. */
export function daysTone(row: SegRow, now: Date): DaysTone {
  if (touchedToday(row, now)) return "ok";
  return row.daysSinceSent >= 7 ? "bad" : row.daysSinceSent >= 3 ? "warn" : "ok";
}

const santiagoTime = (iso: string) =>
  new Date(iso).toLocaleTimeString("es-CL", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "America/Santiago" });

/** «hoy 10:12» on today's Santiago day, else «13 nov». */
function whenText(iso: string, now: Date): string {
  return dateSantiago(new Date(iso)) === dateSantiago(now) ? `hoy ${santiagoTime(iso)}` : shortDate(iso, now);
}

/** The next follow-up reminder (the open «Seguimiento …» task due first), or null. */
function nextReminder(row: SegRow): string | null {
  const due = (row.card.open_tasks ?? [])
    .filter(isFollowUpTask)
    .map((t) => t.due_at)
    .sort((a, b) => Date.parse(a) - Date.parse(b))[0];
  return due ?? null;
}

const telHref = (phone: string) => `tel:${phone.replace(/[^\d+]/g, "")}`;

const ICON_BUTTON =
  "inline-grid h-9 w-9 shrink-0 place-items-center rounded-md border border-line-strong bg-canvas-raised text-ink-muted transition-colors hover:bg-canvas-sunken hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600";

function Icon({ d }: { d: string }) {
  return (
    <svg viewBox="0 0 16 16" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}

const PHONE_ICON = "M3 2.5h2.5l1.2 3-1.6 1a8 8 0 0 0 4.4 4.4l1-1.6 3 1.2V13a1 1 0 0 1-1 1A11 11 0 0 1 2 3.5a1 1 0 0 1 1-1z";
const MAIL_ICON = "M2 3.5h12v9H2zM2.5 4.5l5.5 4 5.5-4";
const SCRIPT_ICON = "M3 2.5h7l3 3v8H3zM10 2.5v3h3M5.5 8h5M5.5 10.5h5";
const SORT_ICON = "m4 6 4-4 4 4M4 10l4 4 4-4";

export function SeguimientoSheet({
  rows,
  profiles,
  now,
  selectedId,
  scrollToId = null,
  onSelect,
  onGuion,
  onCallSaved,
  onReceipt,
  onConflict,
  callsPaused = false,
  clock,
}: {
  rows: SegRow[];
  profiles: ProfileCard[];
  now: Date;
  selectedId: string | null;
  /** Bring this row into view once it is on screen (`?caso=`). */
  scrollToId?: string | null;
  onSelect: (id: string) => void;
  onGuion: (id: string) => void;
  /** A call was saved on the row: gets the next contact as said in the toast. */
  onCallSaved: (id: string, nextSaid: string) => void;
  onReceipt: (receipt: CaseCommandReceipt) => void;
  onConflict: () => void;
  /** The cases are being read again: a call now would replace a reminder that is already gone. */
  callsPaused?: boolean;
  /** The real time when a call is saved; the sheet's `now` is only for what it shows. */
  clock?: () => Date;
}) {
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 } | null>(null);
  const [callOpen, setCallOpen] = useState<string | null>(null);
  const mayCommand = useMayRunCaseCommands();
  // A call is a note (CRM authoring) plus its reminder (a case command).
  const mayCall = useMayAuthorCrm() && mayCommand;
  const sorted = useMemo(() => {
    if (!sort) return rows;
    const value = (r: SegRow) => (sort.key === "monto" ? r.amountClp ?? -1 : r.daysSinceSent);
    return [...rows].sort((a, b) => (value(a) - value(b)) * sort.dir);
  }, [rows, sort]);
  const total = rows.reduce((sum, r) => sum + (r.amountClp ?? 0), 0);
  const callsToday = rows.reduce((sum, r) => sum + r.callsToday, 0);

  const rowRefs = useRef(new Map<string, HTMLTableRowElement>());
  const scrolled = useRef<string | null>(null);
  useEffect(() => {
    if (!scrollToId || scrolled.current === scrollToId) return;
    const el = rowRefs.current.get(scrollToId);
    if (!el) return;
    scrolled.current = scrollToId;
    el.scrollIntoView?.({ block: "center" });
  }, [scrollToId, sorted]);

  const nameOf = (id: string | null) => profiles.find((p) => p.id === id)?.displayName ?? null;

  const sortButton = (key: SortKey, label: string, right = false) => {
    const on = sort?.key === key;
    return (
      <th
        scope="col"
        aria-sort={on ? (sort!.dir === 1 ? "ascending" : "descending") : "none"}
        className={`sticky top-0 z-10 border-b border-line bg-canvas-sunken p-0 ${right ? "text-right" : "text-left"}`}
      >
        <button
          type="button"
          onClick={() => setSort(on ? { key, dir: sort!.dir === 1 ? -1 : 1 } : { key, dir: -1 })}
          className={`inline-flex h-9 w-full items-center gap-1 whitespace-nowrap px-3 text-[11px] font-bold hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-600 ${right ? "justify-end" : ""} ${on ? "text-ink" : "text-ink-muted"}`}
        >
          {label}
          <span className={on ? "text-ink" : "text-ink-faint"}>
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
      <div className="max-h-[calc(100vh-17rem)] min-h-64 overflow-auto">
        <table className="w-full min-w-[74rem] border-separate border-spacing-0 text-[13px] tabular-nums">
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
          <tbody>
            {sorted.map((r) => {
              const id = r.card.opportunity_id;
              const selected = id === selectedId;
              return (
                <Fragment key={id}>
                  <tr
                    ref={(el) => {
                      if (el) rowRefs.current.set(id, el);
                      else rowRefs.current.delete(id);
                    }}
                    aria-current={selected ? "true" : undefined}
                    onClick={() => onSelect(id)}
                    className={`transition-colors ${selected ? "bg-brand-50" : "bg-canvas-raised hover:bg-canvas-sunken"}`}
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
                            <FollowUpPicker card={r.card} profiles={profiles} onChanged={onReceipt} onConflict={onConflict} compact only="owner" />
                          ) : (
                            <span className="text-[12.5px] text-ink">{nameOf(r.ownerId) ?? "Sin asignar"}</span>
                          )}
                        </div>
                      </div>
                    </td>
                    <td className={td}>
                      <div className="max-w-[15rem] truncate font-bold leading-tight text-ink" title={displayName(r.card).name}>
                        {displayName(r.card).name}
                      </div>
                      <div className="max-w-[15rem] truncate text-[12px] text-ink-muted">{contactLine(r.card) ?? "Sin contacto"}</div>
                    </td>
                    <td className={`${td} whitespace-nowrap`}>
                      {r.phone ? (
                        <>
                          {isMaskedPhone(r.phone) ? (
                            <span className="inline-flex min-h-9 items-center gap-1.5 text-ink-muted" data-testid="phone-masked">
                              <Icon d={PHONE_ICON} />
                              {r.phone}
                            </span>
                          ) : (
                          <a
                            href={telHref(r.phone)}
                            onClick={(e) => e.stopPropagation()}
                            className="inline-flex min-h-9 items-center gap-1.5 font-semibold text-accent hover:underline hover:underline-offset-2 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
                          >
                            <Icon d={PHONE_ICON} />
                            {r.phone}
                          </a>
                          )}
                          {!r.phoneConfirmed ? (
                            <span className="ml-1 rounded-full bg-warn-bg px-1.5 py-px text-[10px] font-bold text-warn">por confirmar</span>
                          ) : null}
                        </>
                      ) : (
                        <span className="text-[12px] text-ink-faint">Sin fono · pedir por correo</span>
                      )}
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
                      <span
                        data-testid="seg-days"
                        data-tone={daysTone(r, now)}
                        className={`inline-flex h-6 min-w-11 items-center justify-center rounded-full px-2 text-[12px] font-bold ${DAYS_PILL[daysTone(r, now)]}`}
                      >
                        {r.daysSinceSent} d
                      </span>
                    </td>
                    <td className={`${td} text-[12px] leading-snug text-ink-muted`}>
                      {r.lastTouch ? (
                        <>
                          <b className="block whitespace-nowrap font-semibold text-ink">{r.lastTouch.label}</b>
                          <span className="whitespace-nowrap">
                            {whenText(r.lastTouch.at, now)} · {r.lastTouch.kind === "email" ? "automático" : r.card.last_call_any?.author_name ?? "equipo"}
                          </span>
                        </>
                      ) : (
                        <span className="text-ink-faint">Sin toque</span>
                      )}
                    </td>
                    <td className={`${td} whitespace-nowrap text-right`}>
                      <span data-testid="seg-calls" className="font-bold">
                        <span className={r.callsToday > 0 ? "text-accent" : "text-ink"}>{r.callsToday}</span>
                        <small className="text-[11px] font-semibold text-ink-faint"> hoy</small> · {r.callsTotal}
                      </span>
                    </td>
                    <td className={`${td} min-w-[9.5rem]`}>
                      {mayCommand ? (
                        <FollowUpPicker card={r.card} profiles={profiles} onChanged={onReceipt} onConflict={onConflict} compact only="channel" />
                      ) : null}
                      <NextDate due={nextReminder(r)} now={now} />
                    </td>
                    <td className={`${td} text-right`}>
                      <Actions row={r} open={callOpen === id} onGuion={() => onGuion(id)} onToggle={() => setCallOpen(callOpen === id ? null : id)} mayCall={mayCall} paused={callsPaused} />
                    </td>
                  </tr>
                  {callOpen === id ? (
                    <SeguimientoCallRow
                      row={r}
                      colSpan={COLUMNS}
                      clock={clock}
                      onCancel={() => setCallOpen(null)}
                      onSaved={(said) => {
                        setCallOpen(null);
                        onCallSaved(id, said);
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

/** The next follow-up reminder's day, under the channel; red once it is past. */
function NextDate({ due, now }: { due: string | null; now: Date }) {
  if (!due) return <div className="mt-1 text-[12px] text-ink-faint">Sin fecha</div>;
  const late = Date.parse(due) < now.getTime() && dateSantiago(new Date(due)) !== dateSantiago(now);
  return (
    <div className={`mt-1 whitespace-nowrap text-[12px] ${late ? "font-semibold text-bad" : "text-ink-muted"}`}>
      {late ? `Vencido · ${shortDate(due, now)}` : whenText(due, now)}
    </div>
  );
}

function Actions({
  row,
  open,
  mayCall,
  paused,
  onGuion,
  onToggle,
}: {
  row: SegRow;
  open: boolean;
  mayCall: boolean;
  paused: boolean;
  onGuion: () => void;
  onToggle: () => void;
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
      <button type="button" aria-label={`Ver guion para ${client}`} title="Guion de llamada" onClick={onGuion} className={ICON_BUTTON}>
        <Icon d={SCRIPT_ICON} />
      </button>
      {mayCall ? (
        <Button variant="primary" aria-expanded={open} disabled={paused && !open} onClick={onToggle} className="h-9">
          Llamé
        </Button>
      ) : null}
    </div>
  );
}
