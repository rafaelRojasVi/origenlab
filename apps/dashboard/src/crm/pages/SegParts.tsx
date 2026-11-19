/**
 * The small pieces every «Seguimiento» view shares: the days pill, the phone, the last touch, the
 * next date, the icon buttons and the «⋯» menu of a case.
 */
import { useEffect, useId, useRef, useState } from "react";
import { dateSantiago } from "../goals";
import { isMaskedPhone } from "../redaction";
import { touchedToday, type SegRow } from "../seguimiento";
import { nextContactAt } from "../seguimientoView";
import { shortDate } from "./PipelineBoard";

export type DaysTone = "ok" | "warn" | "bad";

export const DAYS_PILL: Record<DaysTone, string> = {
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
export function whenText(iso: string, now: Date): string {
  return dateSantiago(new Date(iso)) === dateSantiago(now) ? `hoy ${santiagoTime(iso)}` : shortDate(iso, now);
}

/** The case's próximo contacto (`nextContact`, the one rule Hoy, the Pipeline and the case panel read). */
export function nextReminder(row: SegRow, now: Date = new Date()): string | null {
  return nextContactAt(row.card, now);
}

export const telHref = (phone: string) => `tel:${phone.replace(/[^\d+]/g, "")}`;

export const ICON_BUTTON =
  "inline-grid h-9 w-9 shrink-0 place-items-center rounded-md border border-line-strong bg-canvas-raised text-ink-muted transition-colors hover:bg-canvas-sunken hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600";

export function Icon({ d }: { d: string }) {
  return (
    <svg viewBox="0 0 16 16" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}

export const PHONE_ICON = "M3 2.5h2.5l1.2 3-1.6 1a8 8 0 0 0 4.4 4.4l1-1.6 3 1.2V13a1 1 0 0 1-1 1A11 11 0 0 1 2 3.5a1 1 0 0 1 1-1z";
export const MAIL_ICON = "M2 3.5h12v9H2zM2.5 4.5l5.5 4 5.5-4";
export const SCRIPT_ICON = "M3 2.5h7l3 3v8H3zM10 2.5v3h3M5.5 8h5M5.5 10.5h5";
export const SORT_ICON = "m4 6 4-4 4 4M4 10l4 4 4-4";
const MORE_ICON = "M3.5 8h.01M8 8h.01M12.5 8h.01";

export function DaysPill({ row, now }: { row: SegRow; now: Date }) {
  const tone = daysTone(row, now);
  return (
    <span
      data-testid="seg-days"
      data-tone={tone}
      title="Días desde que se envió la cotización"
      className={`inline-flex h-6 min-w-11 items-center justify-center rounded-full px-2 text-[12px] font-bold tabular-nums ${DAYS_PILL[tone]}`}
    >
      {row.daysSinceSent} d
    </span>
  );
}

/** The phone to tap, «por confirmar» when the CRM is not sure, or how to get one. */
export function PhoneLink({ row }: { row: SegRow }) {
  if (!row.phone) return <span className="text-[12px] text-ink-faint">Sin fono · pedir por correo</span>;
  return (
    <>
      {isMaskedPhone(row.phone) ? (
        <span className="inline-flex min-h-9 items-center gap-1.5 text-ink-muted" data-testid="phone-masked">
          <Icon d={PHONE_ICON} />
          {row.phone}
        </span>
      ) : (
        <a
          href={telHref(row.phone)}
          onClick={(e) => e.stopPropagation()}
          className="inline-flex min-h-9 items-center gap-1.5 font-semibold text-accent hover:underline hover:underline-offset-2 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
        >
          <Icon d={PHONE_ICON} />
          {row.phone}
        </a>
      )}
      {!row.phoneConfirmed ? <span className="ml-1 rounded-full bg-warn-bg px-1.5 py-px text-[10px] font-bold text-warn">por confirmar</span> : null}
    </>
  );
}

export function LastTouch({ row, now }: { row: SegRow; now: Date }) {
  if (!row.lastTouch) return <span className="text-ink-faint">Sin toque</span>;
  return (
    <>
      <b className="block whitespace-nowrap font-semibold text-ink">{row.lastTouch.label}</b>
      <span className="whitespace-nowrap">
        {whenText(row.lastTouch.at, now)} · {row.lastTouch.kind === "email" ? "automático" : row.card.last_call_any?.author_name ?? "equipo"}
      </span>
    </>
  );
}

/** The next follow-up reminder's day; red once it is past. */
export function NextDate({ due, now }: { due: string | null; now: Date }) {
  if (!due) return <div className="mt-1 text-[12px] text-ink-faint">Sin fecha</div>;
  const late = Date.parse(due) < now.getTime() && dateSantiago(new Date(due)) !== dateSantiago(now);
  return (
    <div className={`mt-1 whitespace-nowrap text-[12px] ${late ? "font-semibold text-bad" : "text-ink-muted"}`}>
      {late ? `Vencido · ${shortDate(due, now)}` : whenText(due, now)}
    </div>
  );
}

export type RowAction = "pausa" | "perdida";

/** «⋯»: the quick decisions on a case, «Pausar…» and «Ya no sigue». */
export function RowMenu({ client, onAction }: { client: string; onAction: (action: RowAction) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const menuId = useId();
  useEffect(() => {
    if (!open) return;
    const away = (e: Event) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    ref.current?.querySelector<HTMLElement>("[role=menuitem]")?.focus();
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);
  const item =
    "flex w-full items-center rounded-md px-3 py-2 text-left text-[13px] font-medium text-ink hover:bg-canvas-sunken focus:bg-canvas-sunken focus:outline-none";
  const pick = (a: RowAction) => {
    setOpen(false);
    onAction(a);
  };
  return (
    <div ref={ref} className="relative" onClick={(e) => e.stopPropagation()}>
      <button
        type="button"
        aria-label={`Más acciones para ${client}`}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => setOpen((v) => !v)}
        className={ICON_BUTTON}
      >
        <Icon d={MORE_ICON} />
      </button>
      {open ? (
        <div
          id={menuId}
          role="menu"
          aria-label={`Acciones para ${client}`}
          className="absolute right-0 top-10 z-30 w-44 rounded-lg border border-line bg-canvas-raised p-1 text-left shadow-lg motion-safe:animate-fade-in-up"
        >
          <button type="button" role="menuitem" className={item} onClick={() => pick("pausa")}>
            Pausar…
          </button>
          <button type="button" role="menuitem" className={`${item} text-bad`} onClick={() => pick("perdida")}>
            Ya no sigue
          </button>
        </div>
      ) : null}
    </div>
  );
}
