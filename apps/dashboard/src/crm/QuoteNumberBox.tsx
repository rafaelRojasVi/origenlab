import { useId, useMemo, useState } from "react";
import { fetchDriveArchive, fetchMailQuoteNumbers } from "./crmApi";
import type { PipelineResponse } from "./crmTypes";
import { todayInSantiago } from "./marketing/calendar";
import { findUses, knownQuoteNumbers, lastAndNext, parseQuoteNumber, type KnownNumber } from "./quoteNumbers";
import { useResource, type ResourceState } from "./useResource";

function where(k: KnownNumber): string {
  return [k.label, k.sources.join(" y ")].filter(Boolean).join(" · ");
}

/**
 * The last quote number the system knows and the next one, and a check for a typed number.
 * Read-only and honest about its reach: a quotation that is in none of the CRM, the Drive archive
 * or the captured Gmail is unknown here, so a number not found is «not in the CRM, Drive or
 * Gmail», never «free». When the Gmail numbers cannot be read the box still answers from the CRM
 * and Drive and says that Gmail is missing; the next number waits until every read has answered.
 */
export function QuoteNumberBox({ pipeline, compact = false }: {
  pipeline: ResourceState<PipelineResponse>;
  /** Stacked, for a side column. */
  compact?: boolean;
}) {
  const [drive] = useResource(fetchDriveArchive);
  const [mail] = useResource(fetchMailQuoteNumbers);
  const inputId = useId();
  const [typed, setTyped] = useState("");
  const [copied, setCopied] = useState(false);
  const year = Number(todayInSantiago().slice(2, 4));
  const known = useMemo(
    () =>
      knownQuoteNumbers(
        pipeline.kind === "ready" ? pipeline.data.items : [],
        drive.kind === "ready" ? drive.data.folders : [],
        mail.kind === "ready" ? mail.data.items : [],
      ),
    [pipeline, drive, mail],
  );
  const loading = pipeline.kind === "loading" || drive.kind === "loading" || mail.kind === "loading";
  const mailMissing = mail.kind !== "ready" && mail.kind !== "loading";
  const { last, next } = lastAndNext(known, year);
  const parsed = typed.trim() ? parseQuoteNumber(typed, year) : null;
  const uses = parsed ? findUses(known, parsed) : [];

  const copy = () => {
    void navigator.clipboard?.writeText(next).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    });
  };

  return (
    <section
      data-testid="quote-number-box"
      aria-label="Número de cotización"
      className={`crm-rise grid grid-cols-1 gap-3 rounded-xl border border-brand-600/30 bg-canvas-raised p-4 shadow-[0_1px_2px_rgb(28_25_23/0.04)] ${
        compact ? "grid-cols-2 gap-x-4" : "md:grid-cols-[auto_auto_minmax(0,1fr)] md:items-start md:gap-6"
      }`}
    >
      <div data-testid="quote-last" className="min-w-0">
        <p className="text-[12px] font-medium text-ink-muted">Último número</p>
        {loading && !last ? (
          <div className="crm-skeleton mt-1 h-6 w-28 rounded" />
        ) : (
          <>
            <p className={`mt-0.5 whitespace-nowrap font-semibold leading-none tracking-tight text-ink tabular-nums ${compact ? "text-[18px]" : "text-[20px]"}`}>{last?.number ?? "—"}</p>
            {last ? <p className="mt-1 max-w-[16rem] truncate text-[11px] text-ink-faint">{where(last)}</p> : null}
          </>
        )}
      </div>
      <div data-testid="quote-next" className="min-w-0">
        <p className="text-[12px] font-medium text-ink-muted">Siguiente</p>
        <div className="mt-0.5 flex items-center gap-2">
          <p className={`whitespace-nowrap font-semibold leading-none tracking-tight text-brand-700 tabular-nums ${compact ? "text-[18px]" : "text-[20px]"}`}>{loading ? "…" : next}</p>
          <button
            type="button"
            onClick={copy}
            disabled={loading}
            className="h-6 rounded-md border border-line px-2 text-[11px] font-medium text-ink-muted transition-colors hover:border-line-strong hover:text-ink disabled:opacity-50"
          >
            {copied ? "Copiado" : "Copiar"}
          </button>
        </div>
      </div>
      <div className={`min-w-0 ${compact ? "col-span-2" : ""}`}>
        <label htmlFor={inputId} className="text-[12px] font-medium text-ink-muted">
          ¿Ya existe este número?
        </label>
        <input
          id={inputId}
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          placeholder="Ej. 1247 o 01247-26"
          autoComplete="off"
          className="mt-1 h-8 w-full rounded-md border border-line bg-canvas px-2 text-[13px] tabular-nums text-ink placeholder:text-ink-faint focus:border-brand-600 focus:outline-none focus:ring-2 focus:ring-brand-600/20"
        />
        <p data-testid="quote-check" aria-live="polite" className="mt-1 min-h-[1rem] text-[11px]">
          {!typed.trim() ? null : !parsed ? (
            <span className="text-ink-muted">Escribe un número, por ejemplo 1247 o 01247-26.</span>
          ) : uses.length ? (
            <span className="font-medium text-bad">Ya usado: {uses.map((u) => `${u.number} · ${where(u)}`).join(" — ")}</span>
          ) : (
            <span className={mailMissing ? "text-ink-muted" : "text-good"}>
              {mailMissing
                ? "No está en el CRM ni en el archivo de Drive; los números de Gmail no se pudieron leer."
                : "No está en el CRM, en Drive ni en Gmail."}
            </span>
          )}
        </p>
      </div>
      <p className={`text-[11px] text-ink-faint ${compact ? "col-span-2" : "md:col-span-3"}`}>
        Según el CRM, el archivo de Drive y los correos de Gmail
        {drive.kind !== "ready" && drive.kind !== "loading" ? " (sin el archivo de Drive: no se pudo leer)" : ""}
        {mailMissing ? " (los números de Gmail no se pudieron leer)" : ""}. Una cotización que aún no está en
        ninguno de los tres no aparece aquí.
      </p>
    </section>
  );
}
