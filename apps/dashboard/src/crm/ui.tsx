/**
 * CRM workspace primitives. Dense, calm and explicit: every status pairs a glyph with text,
 * every "nothing here" says which kind of nothing it is, and every write action is visibly
 * disabled with the reason next to it.
 */

import {
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type ButtonHTMLAttributes,
  type ChangeEvent,
  type ReactNode,
} from "react";
import type { Provenance } from "./crmTypes";
import type { ResourceState } from "./useResource";

export type Tone = "neutral" | "good" | "warn" | "bad" | "info" | "brand";

const TONE: Record<Tone, string> = {
  neutral: "bg-canvas-sunken text-ink-muted border-line-strong",
  good: "bg-good-bg text-good border-good/30",
  warn: "bg-warn-bg text-warn border-warn/30",
  bad: "bg-bad-bg text-bad border-bad/30",
  info: "bg-info-bg text-info border-info/30",
  brand: "bg-brand-50 text-brand-700 border-brand-600/30",
};

const GLYPH: Record<Tone, string> = {
  neutral: "●",
  good: "✓",
  warn: "!",
  bad: "✕",
  info: "i",
  brand: "●",
};

export function Badge({
  tone = "neutral",
  children,
  title,
  glyph = true,
}: {
  tone?: Tone;
  children: ReactNode;
  title?: string;
  glyph?: boolean;
}) {
  return (
    <span
      title={title}
      className={`inline-flex max-w-full items-center gap-1 whitespace-nowrap rounded-full border px-1.5 py-px text-[11px] font-medium leading-4 ${TONE[tone]}`}
    >
      {glyph ? <span aria-hidden="true">{GLYPH[tone]}</span> : null}
      <span className="truncate">{children}</span>
    </span>
  );
}

export const PROVENANCE_LABEL: Record<Provenance, { label: string; tone: Tone }> = {
  imported: { label: "Importado", tone: "good" },
  partial: { label: "Parcial", tone: "warn" },
  not_imported: { label: "No importado", tone: "neutral" },
  no_write_path: { label: "Sin flujo de escritura", tone: "neutral" },
};

export function ProvenanceBadge({ provenance }: { provenance: Provenance }) {
  const p = PROVENANCE_LABEL[provenance];
  return <Badge tone={p.tone}>{p.label}</Badge>;
}

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-end gap-x-4 gap-y-2">
      <div className="min-w-0">
        <h1 className="text-[17px] font-semibold tracking-tight text-ink">{title}</h1>
        {subtitle ? <p className="mt-0.5 text-xs text-ink-muted">{subtitle}</p> : null}
      </div>
      {actions ? <div className="ml-auto flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}

/** Platt-style inline stat line: `label value | label value`, never a wall of tiles. */
export function StatLine({
  items,
}: {
  items: { label: string; value: ReactNode; tone?: "bad" | "warn" | "good"; title?: string }[];
}) {
  return (
    <dl className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-muted">
      {items.map((item, i) => (
        <div key={item.label} className="flex items-center gap-3" title={item.title}>
          {i > 0 ? <span aria-hidden="true" className="h-3 w-px bg-line-strong" /> : null}
          <div className="flex items-baseline gap-1.5">
            <dt>{item.label}</dt>
            <dd
              className={`font-semibold tabular-nums ${
                item.tone === "bad" ? "text-bad" : item.tone === "warn" ? "text-warn" : item.tone === "good" ? "text-good" : "text-ink"
              }`}
            >
              {item.value}
            </dd>
          </div>
        </div>
      ))}
    </dl>
  );
}

export function Panel({
  title,
  note,
  aside,
  children,
  className = "",
  bodyClassName = "",
}: {
  title?: ReactNode;
  note?: ReactNode;
  aside?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section className={`min-w-0 rounded-lg border border-line bg-canvas-raised ${className}`}>
      {title ? (
        <header className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
          <h2 className="text-[13px] font-semibold text-ink">{title}</h2>
          {note ? <span className="text-[11px] text-ink-faint">{note}</span> : null}
          {aside ? <div className="ml-auto flex items-center gap-2">{aside}</div> : null}
        </header>
      ) : null}
      <div className={bodyClassName}>{children}</div>
    </section>
  );
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: { value: T; label: string; count?: number }[];
  onChange: (value: T) => void;
  label: string;
}) {
  return (
    <div role="group" aria-label={label} className="inline-flex flex-wrap rounded-md bg-canvas-sunken p-0.5">
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(o.value)}
            className={`h-6 rounded-[5px] px-2.5 text-xs font-medium transition-colors ${
              active
                ? "bg-canvas-raised text-ink shadow-[0_1px_2px_rgb(24_24_27/0.08)]"
                : "text-ink-muted hover:text-ink"
            }`}
          >
            {o.label}
            {o.count !== undefined ? (
              <span className={`ml-1.5 tabular-nums ${active ? "text-ink-muted" : "text-ink-faint"}`}>{o.count}</span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

export function SearchInput({
  value,
  onChange,
  placeholder,
  label,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
  label: string;
}) {
  return (
    <input
      type="search"
      aria-label={label}
      value={value}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
      className="h-7 w-full rounded-md border border-line bg-canvas-raised px-2.5 text-xs text-ink placeholder:text-ink-faint focus:border-brand-600 focus:outline-none focus:ring-1 focus:ring-brand-600 sm:w-64"
    />
  );
}

/** A write action that exists in the product but is not enabled: visible, disabled, explained. */
/* ─────────────────────────────────────────────────────────────── buttons ── */

export type ButtonVariant = "primary" | "secondary" | "danger" | "quiet";

const BUTTON_BASE =
  "inline-flex h-8 shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-md px-3 text-xs font-medium " +
  "transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 " +
  "focus-visible:outline-brand-600 disabled:cursor-not-allowed disabled:opacity-50";

const BUTTON_VARIANT: Record<ButtonVariant, string> = {
  primary: "bg-brand-700 text-white hover:bg-brand-900",
  secondary: "border border-line bg-canvas-raised text-ink hover:bg-canvas-sunken",
  danger: "border border-bad/40 bg-canvas-raised text-bad hover:bg-bad-bg",
  quiet: "text-brand-700 hover:bg-canvas-sunken",
};

/** The class string of a button, for the rare element that must stay an `<a>` but look like one. */
export function buttonClass(variant: ButtonVariant = "secondary"): string {
  return `${BUTTON_BASE} ${BUTTON_VARIANT[variant]}`;
}

export function Spinner() {
  return (
    <span
      aria-hidden="true"
      className="inline-block h-3 w-3 rounded-full border-2 border-current border-r-transparent motion-safe:animate-spin"
    />
  );
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  /** While true the button is disabled, says `busyLabel` and shows a spinner. */
  busy?: boolean;
  busyLabel?: ReactNode;
};

/**
 * Every CRM button. One look per variant, a visible "working" state, and never clickable twice
 * while its write is in flight.
 */
export function Button({
  variant = "secondary",
  busy = false,
  busyLabel,
  disabled,
  className,
  children,
  type = "button",
  ...rest
}: ButtonProps) {
  return (
    <button
      {...rest}
      type={type}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      className={`${buttonClass(variant)}${className ? ` ${className}` : ""}`}
    >
      {busy ? (
        <>
          <Spinner />
          {busyLabel ?? children}
        </>
      ) : (
        children
      )}
    </button>
  );
}

/* ─────────────────────────────────────────────────────────────── toasts ── */

export interface ToastItem {
  id: number;
  tone: "good" | "warn" | "bad";
  text: string;
}

let toastItems: ToastItem[] = [];
let toastSeq = 0;
const toastListeners = new Set<() => void>();

function emitToasts(next: ToastItem[]) {
  toastItems = next;
  toastListeners.forEach((l) => l());
}

export function dismissToast(id: number): void {
  emitToasts(toastItems.filter((t) => t.id !== id));
}

/** A short line saying what just happened. It disappears on its own after a few seconds. */
export function toast(text: string, tone: ToastItem["tone"] = "good"): void {
  const id = ++toastSeq;
  emitToasts([...toastItems.slice(-3), { id, tone, text }]);
  setTimeout(() => dismissToast(id), tone === "bad" ? 8000 : 4000);
}

function subscribeToasts(listener: () => void) {
  toastListeners.add(listener);
  return () => toastListeners.delete(listener);
}

/** Mounted once by the CRM shell. */
export function Toaster() {
  const items = useSyncExternalStore(subscribeToasts, () => toastItems, () => toastItems);
  if (items.length === 0) return null;
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-4 z-[60] flex flex-col items-center gap-2 px-4" data-testid="toaster">
      {items.map((t) => (
        <div
          key={t.id}
          role={t.tone === "bad" ? "alert" : "status"}
          className={`pointer-events-auto flex max-w-md items-start gap-2 rounded-lg border px-3 py-2 text-xs shadow-lg ${
            t.tone === "good"
              ? "border-good/40 bg-good-bg text-good"
              : t.tone === "warn"
                ? "border-warn/40 bg-warn-bg text-warn"
                : "border-bad/40 bg-bad-bg text-bad"
          }`}
        >
          <span className="min-w-0 flex-1 break-words">{t.text}</span>
          <button type="button" onClick={() => dismissToast(t.id)} aria-label="Cerrar aviso" className="shrink-0 opacity-70 hover:opacity-100">
            ✕
          </button>
        </div>
      ))}
    </div>
  );
}

export function DisabledAction({ children, reason, id }: { children: ReactNode; reason: string; id: string }) {
  return (
    <span className="inline-flex flex-col items-start gap-0.5">
      <button
        type="button"
        disabled
        aria-describedby={id}
        className="h-7 cursor-not-allowed rounded-md border border-line bg-canvas-raised px-2.5 text-xs font-medium text-ink opacity-50"
      >
        {children}
      </button>
      <span id={id} className="text-[10px] leading-3 text-ink-faint">
        {reason}
      </span>
    </span>
  );
}

export const WRITE_DISABLED_REASON = "Escritura desactivada hasta aprobación explícita";

/** Only `http:` and `https:` may open in a new tab; `javascript:`, `data:`, `blob:` and relative values never become a link. */
export function isSafeExternalHref(href: string): boolean {
  try {
    const url = new URL(href);
    return url.protocol === "https:" || url.protocol === "http:";
  } catch {
    return false;
  }
}

export function ExternalLink({ href, children, label }: { href: string; children: ReactNode; label?: string }) {
  if (!isSafeExternalHref(href)) {
    // Defence in depth: every href here is server-built, but an unsafe scheme renders as inert text.
    return (
      <span className="text-ink-faint" title="Enlace no permitido" data-testid="external-link-refused">
        {children}
      </span>
    );
  }
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      aria-label={label}
      className="inline-flex items-center gap-1 font-medium text-brand-700 underline decoration-brand-600/30 underline-offset-2 hover:decoration-brand-700"
    >
      {children}
      <span aria-hidden="true" className="text-[10px]">
        ↗
      </span>
    </a>
  );
}

/** The Drive links shown in the CRM come from local archive ledgers, not a live Drive read. */
export const LOCAL_DRIVE_TITLE =
  "Enlace tomado del registro local del archivo de Drive (ledger de la carga). No se consultó Drive en vivo: el archivo pudo moverse o cambiar de permisos.";

/**
 * A Drive link whose address comes from a local archive ledger. It is labelled as such every
 * time it appears, because nothing checked it against Drive when the page loaded.
 */
export function LocalDriveLink({ href, children, label }: { href: string; children: ReactNode; label?: string }) {
  return (
    <span className="inline-flex items-center gap-1" title={LOCAL_DRIVE_TITLE}>
      <ExternalLink href={href} label={label ? `${label} (enlace de registro local)` : undefined}>
        {children}
      </ExternalLink>
      <span className="rounded border border-line px-1 text-[9px] font-semibold uppercase tracking-wide text-ink-faint" data-testid="local-drive-tag">
        registro local
      </span>
    </span>
  );
}

/** Marks a computed next step: the CRM holds no task, this is a suggestion from case state. */
export function SuggestedTag() {
  return (
    <span
      className="rounded border border-dashed border-line-strong px-1 text-[9px] font-semibold uppercase tracking-wide text-ink-faint"
      title="Sugerencia calculada a partir del estado del caso. No es una tarea registrada en el CRM."
      data-testid="suggested-tag"
    >
      sugerencia
    </span>
  );
}

/* ─────────────────────────────── states: loading / empty / not imported / error ── */

export function Skeleton({ rows = 3, cards = false }: { rows?: number; cards?: boolean }) {
  return (
    <div
      role="status"
      aria-label="Cargando"
      className={cards ? "grid grid-cols-1 gap-3 md:grid-cols-2 2xl:grid-cols-3" : "space-y-2"}
    >
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className={`crm-skeleton rounded-md ${cards ? "h-40" : "h-9"}`} />
      ))}
      <span className="sr-only">Cargando…</span>
    </div>
  );
}

/** Imported, and genuinely empty. */
export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-line-strong bg-canvas-raised px-4 py-6 text-center">
      <p className="text-[13px] font-semibold text-ink">{title}</p>
      {children ? <p className="mx-auto mt-1 max-w-md text-xs text-ink-muted">{children}</p> : null}
    </div>
  );
}

/** Zero because the data has not been imported (or has no write path) — not because it is empty. */
export function NotImportedState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div role="status" className="rounded-lg border border-line bg-canvas-sunken/70 px-4 py-4">
      <div className="flex items-center gap-2">
        <Badge tone="neutral">No importado</Badge>
        <p className="text-[13px] font-semibold text-ink">{title}</p>
      </div>
      {children ? <div className="mt-1.5 max-w-2xl text-xs leading-5 text-ink-muted">{children}</div> : null}
    </div>
  );
}

export function ResourceGate<T>({
  state,
  reload,
  skeleton,
  children,
}: {
  state: ResourceState<T>;
  reload: () => void;
  skeleton?: ReactNode;
  children: (data: T) => ReactNode;
}) {
  if (state.kind === "loading") return <>{skeleton ?? <Skeleton />}</>;
  if (state.kind === "ready") return <>{children(state.data)}</>;
  if (state.kind === "permission") {
    return (
      <div role="alert" className="rounded-lg border border-warn/30 bg-warn-bg px-4 py-4">
        <p className="text-[13px] font-semibold text-warn">Sin permiso para ver esta sección</p>
        <p className="mt-1 text-xs text-ink-muted">
          Tu sesión no tiene un operador activo con rol de lectura. Vuelve a iniciar sesión o pide acceso al
          administrador.
        </p>
      </div>
    );
  }
  if (state.kind === "unavailable") {
    return (
      <div role="status" className="rounded-lg border border-line bg-canvas-sunken/70 px-4 py-4">
        <p className="text-[13px] font-semibold text-ink">Esta lectura no está habilitada en este entorno</p>
        <p className="mt-1 text-xs text-ink-muted">
          La ruta del API no está montada o el proxy del panel no la permite todavía. Localmente se sirve con
          el proxy de Vite y <code className="rounded bg-canvas-raised px-1">ORIGENLAB_V2_DATABASE_URL</code>.
        </p>
      </div>
    );
  }
  return (
    <div role="alert" className="rounded-lg border border-bad/30 bg-bad-bg px-4 py-4">
      <p className="text-[13px] font-semibold text-bad">No se pudo leer el CRM</p>
      <p className="mt-1 break-words text-xs text-ink-muted">{state.message}</p>
      <button
        type="button"
        onClick={reload}
        className="mt-2 h-7 rounded-md bg-ink px-3 text-xs font-medium text-white hover:bg-black"
      >
        Reintentar
      </button>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────── drawer ── */

export function Drawer({
  open,
  onClose,
  title,
  subtitle,
  busy = false,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  subtitle?: ReactNode;
  /** The data behind the drawer is being refreshed (after a save). */
  busy?: boolean;
  children: ReactNode;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);
  // Read through a ref: callers pass a new arrow on every render, and re-running the focus effect
  // on each refresh would pull the cursor back to ✕ after every save.
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCloseRef.current();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus?.();
    };
  }, [open]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40" role="presentation">
      <div className="absolute inset-0 bg-ink/30" onClick={onClose} aria-hidden="true" />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={typeof title === "string" ? title : "Detalle"}
        className="crm-drawer-in absolute inset-y-0 right-0 flex w-full flex-col border-l border-line bg-canvas-raised shadow-lg sm:w-[34rem]"
      >
        <header className="flex items-start gap-3 border-b border-line px-4 py-3">
          <div className="min-w-0 flex-1">
            <h2 className="text-base font-semibold leading-6 text-ink">{title}</h2>
            {subtitle ? <div className="mt-0.5 text-xs text-ink-muted">{subtitle}</div> : null}
          </div>
          {busy ? (
            <span role="status" className="flex shrink-0 items-center gap-1.5 pt-1 text-[11px] text-ink-muted" data-testid="drawer-refreshing">
              <Spinner />
              Actualizando…
            </span>
          ) : null}
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Cerrar"
            className="h-7 w-7 shrink-0 rounded-md text-ink-muted hover:bg-canvas-sunken hover:text-ink"
          >
            ✕
          </button>
        </header>
        <div className="flex-1 space-y-5 overflow-y-auto p-4">{children}</div>
      </aside>
    </div>
  );
}

/**
 * A small centred dialog for one decision (the Tablero's «Mover a…», «Perdida», «En pausa»).
 * Escape and the backdrop close it unless `busy`; focus goes to its first field and comes back.
 */
export function Modal({
  title,
  onClose,
  busy = false,
  children,
}: {
  title: string;
  onClose: () => void;
  busy?: boolean;
  children: ReactNode;
}) {
  const boxRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const busyRef = useRef(busy);
  busyRef.current = busy;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const first = boxRef.current?.querySelector<HTMLElement>("input, textarea, select, button:not([data-modal-close])");
    first?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busyRef.current) onCloseRef.current();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus?.();
    };
  }, []);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="presentation">
      <div className="absolute inset-0 bg-ink/30" onClick={busy ? undefined : onClose} aria-hidden="true" />
      <div
        ref={boxRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="relative w-full max-w-md min-w-0 rounded-xl border border-line bg-canvas-raised p-5 shadow-xl"
      >
        <div className="mb-3 flex items-start gap-3">
          <h2 className="min-w-0 flex-1 text-base font-semibold text-ink">{title}</h2>
          <button
            type="button"
            data-modal-close
            onClick={onClose}
            disabled={busy}
            aria-label="Cerrar"
            className="h-7 w-7 shrink-0 rounded-md text-ink-muted hover:bg-canvas-sunken hover:text-ink disabled:opacity-50"
          >
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

/** A row of one-click choices (a reason, a state). `aria-pressed` marks the chosen one. */
export function ChoiceChips({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: string[];
  value: string | null;
  onChange: (v: string) => void;
}) {
  return (
    <div role="group" aria-label={label} className="flex flex-wrap gap-1.5">
      {options.map((o) => (
        <button
          key={o}
          type="button"
          aria-pressed={value === o}
          onClick={() => onChange(o)}
          className={`rounded-full border px-2.5 py-1 text-xs transition-colors ${
            value === o
              ? "border-brand-700 bg-brand-700 text-white"
              : "border-line-strong bg-canvas-raised text-ink hover:border-brand-600 hover:text-brand-700"
          }`}
        >
          {o}
        </button>
      ))}
    </div>
  );
}

export function Section({ title, children, aside }: { title: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <section>
      <div className="mb-1.5 flex items-center gap-2">
        <h3 className="text-[11px] font-semibold uppercase tracking-wide text-ink-faint">{title}</h3>
        {aside ? <div className="ml-auto">{aside}</div> : null}
      </div>
      {children}
    </section>
  );
}

/* ─────────────────────────────────────────────────────────────── format ── */

export function fmtDate(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString("es-CL", { day: "2-digit", month: "short", year: "numeric" });
}

export function fmtInt(n: number): string {
  return n.toLocaleString("es-CL");
}

export function initials(name: string | null | undefined): string {
  if (!name) return "·";
  const parts = name.replace(/[^\p{L}\s]/gu, " ").trim().split(/\s+/).filter(Boolean);
  return (parts[0]?.[0] ?? "·").toUpperCase() + (parts[1]?.[0] ?? "").toUpperCase();
}

/* ─────────────────────────────────────────────────────── form primitives ── */

/**
 * A labelled form field wrapper. Renders a label, the field slot, and an optional hint.
 * `required` adds an asterisk to the label text (accessible: marked aria-required on the child).
 */
export function FormField({
  label,
  htmlFor,
  required,
  hint,
  error,
  children,
}: {
  label: string;
  htmlFor?: string;
  required?: boolean;
  hint?: string;
  error?: string | null;
  children: ReactNode;
}) {
  return (
    <div className="min-w-0 space-y-1">
      <label htmlFor={htmlFor} className="block text-xs font-medium text-ink">
        {label}
        {required ? <span aria-hidden="true" className="ml-0.5 text-bad"> *</span> : null}
      </label>
      {children}
      {error ? <p className="text-[11px] text-bad">{error}</p> : hint ? <p className="text-[11px] text-ink-faint">{hint}</p> : null}
    </div>
  );
}

const INPUT_CLS =
  "w-full min-w-0 rounded-md border border-line bg-canvas-raised px-2.5 py-1.5 text-xs text-ink placeholder:text-ink-faint focus:border-brand-600 focus:outline-none focus:ring-1 focus:ring-brand-600 disabled:cursor-not-allowed disabled:opacity-50";

export function TextInput({
  id,
  type = "text",
  value,
  onChange,
  placeholder,
  required,
  disabled,
  maxLength,
}: {
  id?: string;
  type?: "text" | "date";
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  required?: boolean;
  disabled?: boolean;
  maxLength?: number;
}) {
  return (
    <input
      id={id}
      type={type}
      value={value}
      onChange={(e: ChangeEvent<HTMLInputElement>) => onChange(e.target.value)}
      placeholder={placeholder}
      required={required}
      disabled={disabled}
      maxLength={maxLength}
      className={INPUT_CLS}
    />
  );
}

export function TextareaInput({
  id,
  value,
  onChange,
  placeholder,
  required,
  disabled,
  maxLength,
  rows = 3,
}: {
  id?: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  required?: boolean;
  disabled?: boolean;
  maxLength?: number;
  rows?: number;
}) {
  return (
    <textarea
      id={id}
      value={value}
      onChange={(e: ChangeEvent<HTMLTextAreaElement>) => onChange(e.target.value)}
      placeholder={placeholder}
      required={required}
      disabled={disabled}
      maxLength={maxLength}
      rows={rows}
      className={`${INPUT_CLS} resize-y`}
    />
  );
}

export function SelectInput({
  id,
  value,
  onChange,
  options,
  required,
  disabled,
  placeholder,
}: {
  id?: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
  required?: boolean;
  disabled?: boolean;
  placeholder?: string;
}) {
  return (
    <select
      id={id}
      value={value}
      onChange={(e: ChangeEvent<HTMLSelectElement>) => onChange(e.target.value)}
      required={required}
      disabled={disabled}
      className={INPUT_CLS}
    >
      {placeholder ? <option value="">{placeholder}</option> : null}
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

/* ─────────────────────────────────────────────────────── confirm dialog ── */

/**
 * A destructive-action confirmation dialog.
 *
 * The caller passes `lines` — concrete sentences about exactly what will change — and whether
 * a `reason` is required (adds a mandatory textarea). Submit is blocked until the confirm
 * checkbox is checked (and reason filled when required).
 */
export function ConfirmDialog({
  title,
  lines,
  requireReason = false,
  reasonLabel = "Motivo",
  confirmLabel = "Confirmar",
  cancelLabel = "Cancelar",
  busy = false,
  error,
  onConfirm,
  onCancel,
}: {
  title: string;
  lines: string[];
  requireReason?: boolean;
  reasonLabel?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  busy?: boolean;
  error?: string | null;
  onConfirm: (reason: string) => void;
  onCancel: () => void;
}) {
  const [checked, setChecked] = useState(false);
  const [reason, setReason] = useState("");
  const canSubmit = checked && (!requireReason || reason.trim().length > 0) && !busy;

  return (
    <div
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="confirm-dialog-title"
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
    >
      <div className="absolute inset-0 bg-ink/30" onClick={busy ? undefined : onCancel} aria-hidden="true" />
      <div className="relative w-full max-w-md min-w-0 rounded-xl border border-line bg-canvas-raised p-5 shadow-xl">
        <h2 id="confirm-dialog-title" className="text-base font-semibold text-ink">
          {title}
        </h2>
        <ul className="mt-3 space-y-1">
          {lines.map((l, i) => (
            <li key={i} className="flex gap-2 text-xs text-ink-muted">
              <span aria-hidden="true" className="mt-0.5 shrink-0 text-ink-faint">·</span>
              <span>{l}</span>
            </li>
          ))}
        </ul>
        {requireReason ? (
          <div className="mt-3 space-y-1">
            <label htmlFor="confirm-reason" className="block text-xs font-medium text-ink">
              {reasonLabel} <span aria-hidden="true" className="text-bad">*</span>
            </label>
            <textarea
              id="confirm-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
              maxLength={2000}
              placeholder="Escribe el motivo…"
              className="w-full min-w-0 resize-y rounded-md border border-line bg-canvas-raised px-2.5 py-1.5 text-xs text-ink placeholder:text-ink-faint focus:border-brand-600 focus:outline-none focus:ring-1 focus:ring-brand-600"
            />
          </div>
        ) : null}
        <label className="mt-3 flex cursor-pointer items-start gap-2 text-xs text-ink">
          <input
            type="checkbox"
            checked={checked}
            onChange={(e) => setChecked(e.target.checked)}
            className="mt-0.5 shrink-0"
          />
          <span>Entiendo las consecuencias y confirmo esta acción.</span>
        </label>
        {error ? <p className="mt-2 text-[11px] text-bad">{error}</p> : null}
        <div className="mt-4 flex flex-wrap justify-end gap-2">
          <Button onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </Button>
          <Button
            variant="danger"
            onClick={() => canSubmit && onConfirm(reason)}
            disabled={!canSubmit}
            busy={busy}
            busyLabel="Guardando…"
          >
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
