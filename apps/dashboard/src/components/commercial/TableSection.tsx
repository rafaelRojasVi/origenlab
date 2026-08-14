import type { ReactNode } from "react";
import { TechnicalDetailDisclosure } from "../operator/TechnicalDetailDisclosure";

export function TableSection({
  title,
  subtitle,
  dataSourceLabel,
  loading,
  error,
  errorDetail,
  onRetry,
  empty,
  emptyMessage,
  filterEmpty,
  filterEmptyMessage,
  reducedNote,
  toolbar,
  children,
}: {
  title: string;
  subtitle?: string;
  dataSourceLabel?: string;
  loading: boolean;
  error: string | null;
  errorDetail?: string | null;
  onRetry: () => void;
  empty: boolean;
  emptyMessage: string;
  filterEmpty?: boolean;
  filterEmptyMessage?: string;
  reducedNote?: string;
  toolbar?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="space-y-3" aria-labelledby={title.replace(/\s+/g, "-").toLowerCase()}>
      <div>
        <h2 id={title.replace(/\s+/g, "-").toLowerCase()} className="text-lg font-semibold text-slate-900">
          {title}
        </h2>
        {subtitle ? <p className="mt-1 text-sm text-[var(--color-muted)]">{subtitle}</p> : null}
        {dataSourceLabel ? (
          <p className="mt-2 text-xs text-[var(--color-muted)]">
            <span className="font-medium text-slate-700">Fuente de datos:</span> {dataSourceLabel}
          </p>
        ) : null}
      </div>

      {loading ? (
        <div className="h-40 animate-pulse rounded-lg bg-slate-100" role="status" aria-live="polite" />
      ) : null}

      {error ? (
        <div
          className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-900"
          role="alert"
        >
          <p className="font-medium">{error}</p>
          {errorDetail ? <TechnicalDetailDisclosure detail={errorDetail} /> : null}
          <button
            type="button"
            onClick={onRetry}
            className="mt-2 rounded-md border border-red-300 bg-white px-3 py-1 text-sm font-medium text-red-800 hover:bg-red-50"
          >
            Reintentar
          </button>
        </div>
      ) : null}

      {!loading && !error && reducedNote ? (
        <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-950">
          {reducedNote}
        </p>
      ) : null}

      {!loading && !error && empty ? (
        <p className="text-sm text-[var(--color-muted)]" role="status">
          {emptyMessage}
        </p>
      ) : null}

      {!loading && !error && !empty && toolbar ? <div className="space-y-3">{toolbar}</div> : null}

      {!loading && !error && !empty && filterEmpty ? (
        <p className="text-sm text-[var(--color-muted)]" role="status">
          {filterEmptyMessage ?? "Ningún registro coincide con los filtros actuales."}
        </p>
      ) : null}

      {!loading && !error && !empty && !filterEmpty ? children : null}
    </section>
  );
}
