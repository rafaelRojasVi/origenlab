/**
 * The small pieces every «Seguimiento» view shares: the days pill, the phone and WhatsApp buttons,
 * the last touch, the next date, the latest notes, the icon buttons and the «⋯» menu of a case.
 */
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type RefObject } from "react";
import { createPortal } from "react-dom";
import type { ProfileCard } from "../../api/authClient";
import { callOutcomeLabel, defaultCallBody, type CallOutcome } from "../callOutcome";
import { chileanMobile } from "../company";
import { dateSantiago } from "../goals";
import { isMaskedPhone } from "../redaction";
import { channelsFor, touchedToday, type SegRow } from "../seguimiento";
import { shownOwner } from "../seguimientoView";
import { useSegUi } from "../segStore";
import { shortDate } from "./PipelineBoard";

export type DaysTone = "ok" | "warn" | "bad";

export const DAYS_PILL: Record<DaysTone, string> = {
  ok: "bg-good-bg text-good",
  warn: "bg-warn-bg text-warn",
  bad: "bg-bad-bg text-bad",
};

/** «Sin respuesta»: under 3 days fine, 3 to 6 a warning, a week or more bad; fine once touched today, and fine
 *  when the client wrote last and awaits our answer (that is Hoy's «Responder», not a silence). */
export function daysTone(row: SegRow, now: Date): DaysTone {
  if (touchedToday(row, now) || row.answer.kind === "replied") return "ok";
  return row.answer.days >= 7 ? "bad" : row.answer.days >= 3 ? "warn" : "ok";
}

const plural = (n: number) => `${n} ${n === 1 ? "día" : "días"}`;
const SINCE: Record<"quote" | "email" | "call", string> = {
  quote: "que se envió la cotización",
  email: "nuestro último correo",
  call: "la última llamada en que contestó",
};

const santiagoTime = (iso: string) =>
  new Date(iso).toLocaleTimeString("es-CL", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "America/Santiago" });

/** «hoy 10:12» on today's Santiago day, else «13 nov». */
export function whenText(iso: string, now: Date): string {
  if (Number.isNaN(Date.parse(iso))) return "—"; // `dateSantiago` throws on it; `shortDate` says «—»
  return dateSantiago(new Date(iso)) === dateSantiago(now) ? `hoy ${santiagoTime(iso)}` : shortDate(iso, now);
}

/** The case's próximo contacto (`SegRow.next`: `nextContact`, the one rule Hoy, the Pipeline and the case panel read). */
export function nextReminder(row: SegRow): string | null {
  return row.next?.at ?? null;
}

export const telHref = (phone: string) => `tel:${phone.replace(/[^\d+]/g, "")}`;

/** `https://wa.me/569XXXXXXXX` for a Chilean mobile; null for a landline or anything else. */
export function waHref(phone: string): string | null {
  const mobile = chileanMobile(phone);
  return mobile ? `https://wa.me/${mobile}` : null;
}

/** The case's encargado (or suggestion) may write on WhatsApp. */
export function whatsappAllowed(row: SegRow, profiles: ProfileCard[]): boolean {
  return channelsFor(shownOwner(row), profiles).includes("whatsapp");
}

export const ICON_BUTTON =
  "inline-grid h-11 w-11 shrink-0 place-items-center rounded-md border border-line-strong bg-canvas-raised text-ink-muted transition-colors hover:bg-canvas-sunken hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 sm:h-9 sm:w-9";

export function Icon({ d }: { d: string }) {
  return (
    <svg viewBox="0 0 16 16" className="h-4 w-4 shrink-0" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}

export const PHONE_ICON = "M3 2.5h2.5l1.2 3-1.6 1a8 8 0 0 0 4.4 4.4l1-1.6 3 1.2V13a1 1 0 0 1-1 1A11 11 0 0 1 2 3.5a1 1 0 0 1 1-1z";
export const CHAT_ICON = "M3 13l.8-2.6A5.5 5.5 0 1 1 13 8a5.5 5.5 0 0 1-7.6 5.1z";
export const MAIL_ICON = "M2 3.5h12v9H2zM2.5 4.5l5.5 4 5.5-4";
export const SCRIPT_ICON = "M3 2.5h7l3 3v8H3zM10 2.5v3h3M5.5 8h5M5.5 10.5h5";
export const SORT_ICON = "m4 6 4-4 4 4M4 10l4 4 4-4";
const MORE_ICON = "M3.5 8h.01M8 8h.01M12.5 8h.01";

/** «Sin respuesta» (`SegRow.answer`): «Respondió 17 nov» when the client wrote last and awaits our answer,
 *  «Escribió 16 nov» when they wrote and needed none, else the days since our last touch; `long` adds the words
 *  (cards, where no header says it). */
export function DaysPill({ row, now, long = false }: { row: SegRow; now: Date; long?: boolean }) {
  const tone = daysTone(row, now);
  const { kind, at, days, from } = row.answer;
  const text = kind === "replied" && at ? `Respondió ${shortDate(at, now)}`
    : kind === "settled" && at ? `Escribió ${shortDate(at, now)}`
    : `${days} d${long ? " sin respuesta" : ""}`;
  const title = kind === "replied" ? "El cliente escribió y espera nuestra respuesta"
    : kind === "settled" ? `El cliente escribió hace ${plural(days)}; no pedía respuesta`
    : `${plural(days)} sin respuesta desde ${SINCE[from === "client" ? "quote" : from]}`;
  return (
    <span
      data-testid="seg-days"
      data-tone={tone}
      title={title}
      className={`inline-flex h-6 min-w-11 items-center justify-center whitespace-nowrap rounded-full px-2 text-[12px] font-bold tabular-nums ${DAYS_PILL[tone]}`}
    >
      {text}
    </span>
  );
}

/** The phone to tap, «por confirmar» when the CRM is not sure, or a muted «Sin teléfono». */
export function PhoneLink({ row }: { row: SegRow }) {
  if (!row.phone) return <span className="text-[12px] text-ink-faint">Sin teléfono</span>;
  return (
    <span className="inline-flex min-w-0 max-w-full flex-wrap items-center gap-x-1">
      {isMaskedPhone(row.phone) ? (
        <span className="inline-flex min-h-9 min-w-0 items-center gap-1.5 truncate text-ink-muted" data-testid="phone-masked">
          <Icon d={PHONE_ICON} />
          {row.phone}
        </span>
      ) : (
        <a
          href={telHref(row.phone)}
          onClick={(e) => e.stopPropagation()}
          className="inline-flex min-h-9 min-w-0 items-center gap-1.5 truncate font-semibold text-accent hover:underline hover:underline-offset-2 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
        >
          <Icon d={PHONE_ICON} />
          {row.phone}
        </a>
      )}
      {!row.phoneConfirmed ? <span className="rounded-full bg-warn-bg px-1.5 py-px text-[10px] font-bold text-warn">por confirmar</span> : null}
    </span>
  );
}

/**
 * Tap-to-call and WhatsApp as buttons: WhatsApp only when the case's encargado may use it; a
 * masked phone (a viewer) has no links; no phone, a muted «Sin teléfono». `compact` (the sheet)
 * shows the number on the call button and WhatsApp as an icon.
 */
export function ContactButtons({ row, profiles, compact = false }: { row: SegRow; profiles: ProfileCard[]; compact?: boolean }) {
  const client = row.card.organization?.name ?? "el cliente";
  if (!row.phone) return <span className="text-[12px] text-ink-faint" data-testid="seg-no-phone">Sin teléfono</span>;
  if (isMaskedPhone(row.phone)) return <PhoneLink row={row} />;
  const btn = `inline-flex h-11 min-w-0 items-center gap-1.5 rounded-md border px-3 text-[13px] font-semibold transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 ${compact ? "sm:h-8 sm:px-2 sm:text-[12px]" : "sm:h-9"}`;
  return (
    <span className="flex min-w-0 flex-wrap items-center gap-1.5" onClick={(e) => e.stopPropagation()}>
      <a href={telHref(row.phone)} aria-label={`Llamar a ${client} al ${row.phone}`} title={row.phone} className={`${btn} border-brand-100 bg-brand-50 text-accent hover:bg-brand-100`}>
        <Icon d={PHONE_ICON} />
        <span className="truncate">{compact ? row.phone : "Llamar"}</span>
      </a>
      {whatsappAllowed(row, profiles) && waHref(row.phone) ? (
        <a
          href={waHref(row.phone) as string}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={`Escribir por WhatsApp a ${client}`}
          title="WhatsApp"
          className={`${btn} border-line-strong bg-canvas-raised text-good hover:bg-good-bg`}
        >
          <Icon d={CHAT_ICON} />
          {compact ? null : "WhatsApp"}
        </a>
      ) : null}
      {!row.phoneConfirmed ? <span className="rounded-full bg-warn-bg px-1.5 py-px text-[10px] font-bold text-warn">por confirmar</span> : null}
    </span>
  );
}

/** «Último toque»: our last call or email, when, and by whom. */
export function LastTouch({ row, now }: { row: SegRow; now: Date }) {
  if (!row.lastTouch) return <span className="text-ink-faint">Sin toque</span>;
  return (
    <span title={`Último toque hace ${row.daysSinceTouch} ${row.daysSinceTouch === 1 ? "día" : "días"}`}>
      <b className="font-semibold text-ink">{row.lastTouch.label}</b>{" "}
      <span className="whitespace-nowrap">
        · {whenText(row.lastTouch.at, now)}
        {/* A person sends every email (owner 2026-11-24: «automático» was false); a call says who made it. */}
        {row.lastTouch.kind === "call" ? ` · ${row.card.last_call_any?.author_name ?? "equipo"}` : null}
      </span>
    </span>
  );
}

/** The next contact's day; red once it is past. */
export function NextDate({ due, now }: { due: string | null; now: Date }) {
  if (!due) return <span data-testid="seg-next" className="whitespace-nowrap text-[12px] text-ink-faint">Sin fecha</span>;
  const late = Date.parse(due) < now.getTime() && dateSantiago(new Date(due)) !== dateSantiago(now);
  return (
    <span data-testid="seg-next" className={`whitespace-nowrap text-[12px] ${late ? "font-semibold text-bad" : "text-ink-muted"}`}>
      {late ? `Vencido · ${shortDate(due, now)}` : whenText(due, now)}
    </span>
  );
}

/* ── the latest notes on the row ─────────────────────────────────────── */

export interface RowNote {
  at: string;
  /** «Contestó», «Nota»… */
  head: string;
  /** The note's own words, or null when a call has only its outcome. */
  text: string | null;
  author: string | null;
}

/** The case's three newest notes and calls, from the pipeline read (an older API: the newest of each). */
export function rowNotes(row: SegRow): RowNote[] {
  const card = row.card;
  const list = card.recent_notes;
  if (list) {
    return list.slice(0, 3).map((n) => {
      const call = n.kind === "llamada";
      const body = n.body?.trim() ?? "";
      // «Llamada: No contestó» is the default body of a call with no words: the outcome says it.
      const auto = call && !!n.outcome && body === defaultCallBody(n.outcome as CallOutcome);
      return { at: n.at, head: call ? callOutcomeLabel(n.outcome ?? "") : "Nota", text: auto || !body ? null : body, author: n.author };
    });
  }
  const out: RowNote[] = [];
  if (card.last_note) out.push({ at: card.last_note.created_at, head: "Nota", text: card.last_note.body, author: card.last_note.author });
  if (card.last_call_any) out.push({ at: card.last_call_any.at, head: callOutcomeLabel(card.last_call_any.outcome), text: null, author: card.last_call_any.author_name });
  return out.sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
}

const clip = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1).trimEnd()}…` : s);

/** «Contestó: «la orden sale el viernes» · Karla · hoy 13:04». */
export function noteLine(n: RowNote, now: Date, max = 80): string {
  return [n.text ? `${n.head}: «${clip(n.text, max)}»` : n.head, n.author, whenText(n.at, now)].filter(Boolean).join(" · ");
}

/** Close a menu or popover on a click outside it, or on Escape. */
function useAway(open: boolean, ref: RefObject<HTMLElement | null>, close: () => void, also?: RefObject<HTMLElement | null>) {
  const closeRef = useRef(close);
  closeRef.current = close;
  useEffect(() => {
    if (!open) return;
    const away = (e: Event) => {
      const t = e.target as Node;
      if (also?.current?.contains(t)) return;
      if (ref.current && !ref.current.contains(t)) closeRef.current();
    };
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeRef.current();
    };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [open, ref, also]);
}

/**
 * The newest call or note under the client (outcome, the first ~80 characters, author, when).
 * Hover, focus or a tap shows the last three.
 */
export function LastNote({ row, now }: { row: SegRow; now: Date }) {
  const notes = rowNotes(row);
  const [ui, act] = useSegUi();
  const key = `notes:${row.card.opportunity_id}`;
  const open = ui.menuOpen === key;
  const ref = useRef<HTMLSpanElement>(null);
  const popId = useId();
  useAway(open, ref, () => act.openMenu(null));
  if (notes.length === 0) return null;
  return (
    <span ref={ref} className="group/notes relative block min-w-0" onClick={(e) => e.stopPropagation()}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={popId}
        onClick={() => act.openMenu(open ? null : key)}
        className="block min-h-6 w-full min-w-0 truncate rounded-sm text-left text-[12px] text-ink-muted hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
        data-testid="seg-last-note"
      >
        {noteLine(notes[0], now)}
      </button>
      <span
        id={popId}
        role="tooltip"
        data-testid="seg-notes-popover"
        data-open={open || undefined}
        className={`absolute left-0 top-full z-30 mt-1 w-[min(22rem,80vw)] flex-col gap-2 rounded-lg border border-line bg-canvas-raised p-3 text-[12px] text-ink shadow-lg ${open ? "flex" : "hidden group-hover/notes:flex group-focus-within/notes:flex"}`}
      >
        <span className="text-[11px] font-bold text-ink-muted">Últimas notas</span>
        {notes.map((n, i) => (
          <span key={i} className="block leading-snug">
            <b className="font-semibold">{n.head}</b>
            {n.text ? <span className="block whitespace-pre-line break-words text-ink-muted">{clip(n.text, 160)}</span> : null}
            <span className="block text-[11px] text-ink-faint">{[n.author, whenText(n.at, now)].filter(Boolean).join(" · ")}</span>
          </span>
        ))}
      </span>
    </span>
  );
}

export type RowAction = "pausa" | "perdida";

/**
 * «⋯»: the quick decisions on a case («Pausar…», «Ya no sigue») and, where the row has no room for
 * them, the Gmail thread and the guion. Its open state lives in `segStore`, so a refetch that
 * re-mounts the row keeps it open.
 */
export function RowMenu({
  id,
  client,
  onAction,
  gmail,
  onGuion,
}: {
  id: string;
  client: string;
  onAction?: (action: RowAction) => void;
  gmail?: string | null;
  onGuion?: () => void;
}) {
  const [ui, act] = useSegUi();
  const key = `menu:${id}`;
  const open = ui.menuOpen === key;
  const ref = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuId = useId();
  useAway(open, ref, () => act.openMenu(null), menuRef);
  // The menu lives in <body> (a portal), placed by the «⋯» button, so no scroll box or short table
  // clips it (QA 2026-11-19: with one row, «Ya no sigue» sat under the table and could not be
  // clicked). It opens upward when there is no room below, and follows the button on scroll and
  // resize.
  const [place, setPlace] = useState<{ top: number; right: number } | null>(null);
  const reposition = useCallback(() => {
    const btn = buttonRef.current?.getBoundingClientRect();
    if (!btn) return;
    const height = menuRef.current?.offsetHeight ?? 0;
    const below = window.innerHeight - btn.bottom;
    const up = below < height + 8 && btn.top > below;
    setPlace({ top: up ? btn.top - height - 4 : btn.bottom + 4, right: Math.max(8, window.innerWidth - btn.right) });
  }, []);
  useLayoutEffect(() => {
    if (!open) {
      setPlace(null);
      return;
    }
    reposition();
    window.addEventListener("scroll", reposition, true);
    window.addEventListener("resize", reposition);
    return () => {
      window.removeEventListener("scroll", reposition, true);
      window.removeEventListener("resize", reposition);
    };
  }, [open, reposition]);
  // Focus the first item once the menu is placed (a hidden element cannot take the focus).
  const placed = place !== null;
  useEffect(() => {
    if (open && placed) menuRef.current?.querySelector<HTMLElement>("[role=menuitem]")?.focus();
  }, [open, placed]);
  if (!onAction && !gmail && !onGuion) return null;
  const item =
    "flex min-h-11 w-full items-center rounded-md px-3 text-left text-[13px] font-medium text-ink hover:bg-canvas-sunken focus:bg-canvas-sunken focus:outline-none sm:min-h-9";
  const close = () => act.openMenu(null);
  /** Arrows move between the items; Escape and Tab close it and give the focus back to «⋯». */
  const onMenuKey = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    const items = [...(menuRef.current?.querySelectorAll<HTMLElement>("[role=menuitem]") ?? [])];
    const at = items.indexOf(document.activeElement as HTMLElement);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const next = e.key === "ArrowDown" ? (at + 1) % items.length : (at - 1 + items.length) % items.length;
      items[next]?.focus();
    } else if (e.key === "Home" || e.key === "End") {
      e.preventDefault();
      items[e.key === "Home" ? 0 : items.length - 1]?.focus();
    } else if (e.key === "Escape" || e.key === "Tab") {
      e.preventDefault();
      close();
      buttonRef.current?.focus();
    }
  };
  const pick = (a: RowAction) => {
    close();
    onAction?.(a);
  };
  return (
    <div ref={ref} className="relative" onClick={(e) => e.stopPropagation()}>
      <button
        ref={buttonRef}
        type="button"
        aria-label={`Más acciones para ${client}`}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => act.openMenu(open ? null : key)}
        className={ICON_BUTTON}
      >
        <Icon d={MORE_ICON} />
      </button>
      {open ? createPortal(
        <div
          ref={menuRef}
          id={menuId}
          role="menu"
          aria-label={`Acciones para ${client}`}
          onKeyDown={onMenuKey}
          onClick={(e) => e.stopPropagation()}
          data-testid="seg-row-menu"
          style={place ? { top: place.top, right: place.right } : { visibility: "hidden", top: 0, right: 0 }}
          className="fixed z-50 w-56 rounded-lg border border-line bg-canvas-raised p-1 text-left shadow-lg motion-safe:animate-fade-in-up"
        >
          {gmail ? (
            <a role="menuitem" href={gmail} target="_blank" rel="noopener noreferrer" className={item} onClick={close}>
              Abrir el correo en Gmail
            </a>
          ) : null}
          {onGuion ? (
            <button type="button" role="menuitem" className={item} onClick={() => { close(); onGuion(); }}>
              Ver guion
            </button>
          ) : null}
          {onAction ? (
            <>
              <button type="button" role="menuitem" className={item} onClick={() => pick("pausa")}>
                Pausar…
              </button>
              <button type="button" role="menuitem" className={`${item} text-bad`} onClick={() => pick("perdida")}>
                Ya no sigue
              </button>
            </>
          ) : null}
        </div>,
        document.body,
      ) : null}
    </div>
  );
}
