/**
 * «¿Ya lo tenemos?» on Prospectos (owner 2026-10-25): one quiet search line — a correo, a name or a
 * domain — answered from the whole system (`LookupResults`). Nothing shows until there is something
 * to search; ✕ or Escape clears it.
 */
import { useState } from "react";
import { LookupResults } from "../LookupResults";
import { useLookup } from "../lookup";

export function SystemSearch({ onOpenProspect }: { onOpenProspect?: (id: string) => void }) {
  const [q, setQ] = useState("");
  const state = useLookup(q);
  return (
    <section aria-label="¿Ya lo tenemos?" className="space-y-2">
      <div className="relative">
        <svg aria-hidden="true" viewBox="0 0 16 16" className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-faint" fill="none" stroke="currentColor" strokeWidth="1.5">
          <circle cx="7" cy="7" r="4.5" />
          <path d="m10.5 10.5 3 3" />
        </svg>
        <input
          type="text"
          role="searchbox"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Escape") setQ(""); }}
          placeholder="¿Ya lo tenemos? Busca un correo, nombre o dominio en todo el sistema"
          aria-label="Buscar un correo, nombre o dominio en todo el sistema"
          data-testid="system-search"
          className="w-full rounded-lg border border-line bg-canvas-raised py-2 pl-8 pr-8 text-[13px] text-ink placeholder:text-ink-faint focus:border-brand-600 focus:outline-none focus:ring-1 focus:ring-brand-600"
        />
        {q ? (
          <button type="button" onClick={() => setQ("")} aria-label="Limpiar búsqueda"
            className="absolute right-1.5 top-1/2 h-6 w-6 -translate-y-1/2 rounded text-ink-faint hover:bg-canvas-sunken hover:text-ink">
            ✕
          </button>
        ) : null}
      </div>
      {state.kind === "idle" ? null : (
        <div className="crm-mail-open rounded-lg border border-line bg-canvas-raised px-4 py-3" aria-live="polite">
          {state.kind === "loading" ? (
            <p className="text-[13px] text-ink-faint">Buscando…</p>
          ) : state.kind === "error" ? (
            <p className="text-[13px] text-ink-muted">No se pudo buscar ahora. Intenta de nuevo.</p>
          ) : (
            <LookupResults result={state.data} onOpenProspect={onOpenProspect} />
          )}
        </div>
      )}
    </section>
  );
}
