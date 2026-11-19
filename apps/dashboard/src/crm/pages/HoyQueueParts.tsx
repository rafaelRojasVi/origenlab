/**
 * «Hoy» as a work queue (owner 2026-11-11): the list on the left, the chosen case inline on the
 * right (`CaseInline`), the page one screen tall. These are the list's pieces; the row actions of
 * `TodayBody.tsx` stay behind «Atajos de Hoy» in the detail pane.
 */
import type { NextStep } from "../nextSteps";
import { displayName, quoteProduct } from "../caseDisplay";
import { followUpStep } from "../today";

export type HoyTab = "ahora" | "manana" | "semana";

export const HOY_TABS: { key: HoyTab; label: string; groups: NextStep["group"][] }[] = [
  { key: "ahora", label: "Ahora", groups: ["atrasado", "hoy"] },
  { key: "manana", label: "Mañana", groups: ["manana"] },
  { key: "semana", label: "Semana", groups: ["semana"] },
];

export type IconName = "reply" | "doc" | "phone" | "chat" | "mail" | "clock" | "check" | "inbox" | "pause" | "trophy" | "x" | "bell";

const PATHS: Record<IconName, string> = {
  reply: "M6 4L2.5 7.5 6 11M2.5 7.5H10a3.5 3.5 0 013.5 3.5v1",
  doc: "M4 2h5l3 3v9H4zM9 2v3h3M6 8h4M6 10.5h4",
  phone: "M3 3h3l1.5 3.5L6 8a7 7 0 002 2l1.5-1.5L13 10v3a1 1 0 01-1 1A10 10 0 012 4a1 1 0 011-1z",
  chat: "M3 13l.8-2.6A5.5 5.5 0 1113 8a5.5 5.5 0 01-7.6 5.1z",
  mail: "M2 4h12v9H2zM2 5l6 4 6-4",
  clock: "M8 14A6 6 0 108 2a6 6 0 000 12zM8 5v3.5l2.5 1.5",
  check: "M3.5 8.5l3 3 6-7",
  inbox: "M2 9l2-6h8l2 6v4H2zM2 9h3.5l1 1.5h3L10.5 9H14",
  pause: "M8 14A6 6 0 108 2a6 6 0 000 12zM6.5 5.5v5M9.5 5.5v5",
  trophy: "M5 2.5h6v3a3 3 0 01-6 0zM5 3.5H3a2 2 0 002 3M11 3.5h2a2 2 0 01-2 3M8 8.5v3M5.5 13.5h5",
  x: "M8 14A6 6 0 108 2a6 6 0 000 12zM6 6l4 4M10 6l-4 4",
  bell: "M4 11V7.5a4 4 0 018 0V11l1 1.5H3zM6.5 14h3",
};

const TONE: Record<IconName, string> = {
  reply: "bg-warn-bg text-warn",
  doc: "bg-accent/10 text-accent",
  phone: "bg-bad-bg text-bad",
  chat: "bg-good-bg text-good",
  mail: "bg-info-bg text-info",
  clock: "bg-canvas-sunken text-ink-muted",
  check: "bg-canvas-sunken text-ink-muted",
  inbox: "bg-accent/10 text-accent",
  pause: "bg-canvas-sunken text-ink-muted",
  trophy: "bg-good-bg text-good",
  x: "bg-bad-bg text-bad",
  bell: "bg-warn-bg text-warn",
};

const CHANNEL_ICON: Record<"llamada" | "correo" | "whatsapp", IconName> = { llamada: "phone", correo: "mail", whatsapp: "chat" };

/** The channel the step asks for: a reply is an email, a follow-up's step picks call/WhatsApp/email. */
export function stepIcon(step: NextStep): IconName {
  if (step.kind === "responder") return "reply";
  if (step.kind === "cotizar") return "doc";
  if (step.kind === "pausa") return "clock";
  if (step.kind === "tarea") return "check";
  // The channel chosen in Seguimiento («Próximo contacto») wins over the rhythm's suggestion.
  const chosen = step.card.follow_up?.channel;
  if (chosen) return CHANNEL_ICON[chosen];
  if (step.item?.kind === "followup") {
    const n = followUpStep(step.item.followUp);
    return n === 2 ? "phone" : n === 3 ? "chat" : "mail";
  }
  return "mail";
}

export function QueueIcon({ name, size = "md" }: { name: IconName; size?: "md" | "lg" }) {
  const box = size === "lg" ? "h-9 w-9" : "h-7 w-7";
  return (
    <span className={`inline-grid shrink-0 place-items-center rounded-lg ${box} ${TONE[name]}`} aria-hidden="true">
      <svg viewBox="0 0 16 16" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <path d={PATHS[name]} />
      </svg>
    </span>
  );
}

const ACTION: Record<NextStep["kind"], string> = {
  responder: "Responder", cotizar: "Cotizar", seguimiento: "Seguimiento", pausa: "Vuelve de pausa", tarea: "Tarea",
};
const CHANNEL_WORD: Partial<Record<IconName, string>> = { phone: "llamar", chat: "WhatsApp", mail: "correo" };

/** One short line: what to do, with the channel for a follow-up. */
export function stepAction(step: NextStep): string {
  if (step.label) return step.label;
  const ch = step.kind === "seguimiento" ? CHANNEL_WORD[stepIcon(step)] : null;
  const base = ACTION[step.kind];
  return ch ? `${base} · ${ch}` : base;
}

/** What a wait today counts, per kind of step: the badge says it, so «3 d» is never ambiguous. */
const WAIT_WORD: Record<NextStep["kind"], { short: string; long: string }> = {
  responder: { short: "esperando", long: "desde que el cliente escribió" },
  cotizar: { short: "esperando", long: "desde que llegó la solicitud" },
  seguimiento: { short: "sin toque", long: "desde nuestro último toque (correo o llamada)" },
  pausa: { short: "atrasado", long: "desde el día en que volvía de pausa" },
  tarea: { short: "atrasado", long: "desde la fecha de la tarea" },
};

/**
 * The age badge, labelled for what it measures (owner QA 2026-11-19): «3 d atrasado» counts from
 * the próximo contacto's day, «5 d sin toque» from our last touch, «2 d esperando» from the
 * client's email; or the day ahead. `title` says it in full.
 */
export function stepAge(step: NextStep): { text: string; tone: "bad" | "warn" | "neutral"; title: string } {
  const day = (n: number) => `${n} ${n === 1 ? "día" : "días"}`;
  if (step.dueDays < 0) {
    return { text: `${-step.dueDays} d atrasado`, tone: "bad", title: `${day(-step.dueDays)} desde la fecha del próximo contacto` };
  }
  if (step.dueDays === 0) {
    const ms = step.item && "waitMs" in step.item ? (step.item as { waitMs: number }).waitMs : null;
    if (ms === null || ms === undefined) return { text: "hoy", tone: "neutral", title: "Toca hoy" };
    const days = Math.floor(ms / 86_400_000);
    const word = WAIT_WORD[step.kind];
    if (days < 1) {
      const hours = Math.max(0, Math.floor(ms / 3_600_000));
      return { text: `${hours} h`, tone: "neutral", title: `${hours} h ${word.long}` };
    }
    return { text: `${days} d ${word.short}`, tone: days >= 3 ? "warn" : "neutral", title: `${day(days)} ${word.long}` };
  }
  if (step.dueDays === 1) return { text: "mañana", tone: "neutral", title: "Próximo contacto mañana" };
  const d = new Intl.DateTimeFormat("es-CL", { weekday: "short", day: "numeric" }).format(step.due).replace(".", "");
  return { text: d, tone: "neutral", title: `Próximo contacto el ${d}` };
}

const AGE_TONE = { bad: "bg-bad-bg text-bad", warn: "bg-warn-bg text-warn", neutral: "bg-canvas-sunken text-ink-muted" };

export function QueueListRow({
  step, selected, onSelect,
}: { step: NextStep; selected: boolean; onSelect: () => void }) {
  const { name } = displayName(step.card);
  const age = stepAge(step);
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        aria-current={selected || undefined}
        data-testid={`hoy-queue-row-${step.id}`}
        className={`grid w-full grid-cols-[1.75rem_minmax(0,1fr)_auto] items-center gap-2.5 border-b border-line px-3 py-2 text-left transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-600 ${
          selected ? "bg-accent/10 shadow-[inset_3px_0_0] shadow-accent" : "hover:bg-canvas-sunken/60"
        }`}
      >
        <QueueIcon name={stepIcon(step)} />
        <span className="min-w-0">
          <span className="block truncate text-[13px] font-semibold text-ink">{name}</span>
          <span className="block truncate text-[12px] text-ink-muted">{stepAction(step)}{quoteProduct(step.card) ? ` · ${quoteProduct(step.card)}` : ""}</span>
        </span>
        <span title={age.title} className={`whitespace-nowrap rounded px-1.5 py-0.5 font-mono text-[11px] font-semibold tabular-nums ${AGE_TONE[age.tone]}`}>{age.text}</span>
      </button>
    </li>
  );
}
