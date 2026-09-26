/**
 * CRM workspace primitives. Dense, calm and explicit: every status pairs a glyph with text,
 * every "nothing here" says which kind of nothing it is, and every write action is visibly
 * disabled with the reason next to it.
 */

import { useEffect, useRef, type ReactNode } from "react";
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

export function ExternalLink({ href, children, label }: { href: string; children: ReactNode; label?: string }) {
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
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  subtitle?: ReactNode;
  children: ReactNode;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus?.();
    };
  }, [open, onClose]);
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
