import { useCallback, useId, useState } from "react";
import { fetchCaseMemory } from "../crmApi";
import type { CaseMemoryResponse, MemoryFact, SubjectMemory } from "../crmApi";
import { fmtDateTime } from "../ui";
import { useResource } from "../useResource";

/** Fact kinds in the order they read best; «other» becomes «Detalles». */
const FACT_GROUPS: { k: string; label: string }[] = [
  { k: "person", label: "Personas" },
  { k: "product", label: "Productos" },
  { k: "quote", label: "Cotizaciones" },
  { k: "amount", label: "Montos" },
  { k: "date", label: "Fechas" },
  { k: "other", label: "Detalles" },
];

const NEXT_ACTION_LABEL: Record<string, string> = {
  responder: "Responder", esperar: "Esperar respuesta", seguimiento: "Hacer seguimiento",
  vincular: "Vincular", marcar_ganado: "Marcar ganado", marcar_perdido: "Marcar perdido",
  confirmar_institucion: "Confirmar institución", crear_solicitud: "Crear solicitud", nada: "Sin acción",
};

const CONFIDENCE_LABEL: Record<string, string> = { high: "confianza alta", medium: "confianza media", low: "confianza baja" };

/** The institution is already the drawer's title: repeating it as a fact is noise. */
function groupFacts(facts: MemoryFact[]): { label: string; items: string[] }[] {
  const known = new Set(FACT_GROUPS.map((g) => g.k));
  return FACT_GROUPS.map((g) => ({
    label: g.label,
    items: facts
      .filter((f) => f.k !== "institution" && (f.k === g.k || (g.k === "other" && !known.has(f.k))))
      .map((f) => f.v),
  })).filter((g) => g.items.length > 0);
}

/** Four-point sparkle: the mark for «a model read this», not a brand logo. */
function Sparkle({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" className={className} fill="currentColor" aria-hidden="true">
      <path d="M8 1.5c.3 2.9 1.6 4.2 4.5 4.5-2.9.3-4.2 1.6-4.5 4.5-.3-2.9-1.6-4.2-4.5-4.5 2.9-.3 4.2-1.6 4.5-4.5Z" />
      <path className="crm-ai-twinkle-2" d="M12.5 9.5c.15 1.4.8 2.05 2 2.2-1.2.15-1.85.8-2 2.3-.15-1.5-.8-2.15-2-2.3 1.2-.15 1.85-.8 2-2.2Z" />
    </svg>
  );
}

/** The machine memory the triage worker keeps per case («Lo que sabemos»). Hidden when no memory. */
export function CaseMemory({ opportunityId }: { opportunityId: string }) {
  const load = useCallback(() => fetchCaseMemory(opportunityId), [opportunityId]);
  const [state] = useResource(load, [opportunityId]);
  if (state.kind !== "ready") return null;
  const { case_memory: mem } = state.data as CaseMemoryResponse;
  if (!mem) return null;
  return <CaseMemoryCard mem={mem} />;
}

export function CaseMemoryCard({ mem }: { mem: SubjectMemory }) {
  const [open, setOpen] = useState(false);
  const titleId = useId();
  const factsId = useId();
  const groups = groupFacts(mem.facts);
  const factCount = groups.reduce((n, g) => n + g.items.length, 0);
  const questions = mem.open_questions ?? [];
  const next = mem.next_step && mem.next_step.action !== "nada" ? mem.next_step : null;

  return (
    <section
      aria-labelledby={titleId}
      className="crm-ai-card relative overflow-hidden rounded-lg border border-brand-100 bg-gradient-to-b from-brand-50/70 to-canvas-raised p-3.5"
      data-testid="case-memory"
    >
      <div aria-hidden="true" className="crm-ai-sweep pointer-events-none absolute inset-0" />
      <header className="mb-2 flex items-center gap-2">
        <Sparkle className="crm-ai-twinkle h-[18px] w-[18px] shrink-0 text-accent" />
        <h3 id={titleId} className="text-[13px] font-semibold text-ink">Lo que sabemos</h3>
        <span
          className="ml-auto truncate text-[11px] text-ink-faint"
          title={mem.model ? `Resumen escrito por ${mem.model} a partir de los correos del caso` : "Resumen escrito por IA a partir de los correos del caso"}
        >
          Leído por IA{mem.written_at ? ` · ${fmtDateTime(mem.written_at)}` : ""}
        </span>
      </header>

      {next && (
        <div className="crm-ai-reveal mb-2.5 rounded-md border border-brand-100 bg-canvas-raised px-3 py-2" data-testid="case-memory-next-step">
          <p className="flex items-center gap-1.5 text-[12px]">
            <span className="font-semibold text-accent">La IA sugiere: {NEXT_ACTION_LABEL[next.action] ?? next.action}</span>
            <span className="text-ink-faint">· {CONFIDENCE_LABEL[next.confidence] ?? next.confidence}</span>
          </p>
          <p className="mt-0.5 text-[12.5px] leading-5 text-ink-muted">{next.why}</p>
        </div>
      )}

      <p className="crm-ai-reveal text-[13px] leading-[1.6] text-ink [--d:1]" data-testid="case-memory-summary">
        {mem.summary}
      </p>

      {questions.length > 0 && (
        <div className="crm-ai-reveal mt-3 [--d:2]">
          <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-ink-faint">Por confirmar</p>
          <ul className="space-y-1">
            {questions.map((q, i) => (
              <li key={i} className="flex gap-2 text-[12.5px] leading-5 text-ink-muted">
                <span aria-hidden="true" className="mt-[3px] flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border border-warn/50 text-[9px] font-bold text-warn">?</span>
                <span>{q}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {factCount > 0 && (
        <div className="crm-ai-reveal mt-3 border-t border-line pt-2 [--d:3]">
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            aria-controls={factsId}
            className="flex items-center gap-1 rounded text-[12px] font-medium text-ink-muted hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
          >
            <svg viewBox="0 0 16 16" className={`h-3 w-3 transition-transform ${open ? "rotate-90" : ""}`} fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
              <path d="M6 3.5 10.5 8 6 12.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            {open ? "Ocultar los datos" : `Ver los ${factCount} datos`}
          </button>
          {open && (
            <dl id={factsId} className="mt-2 space-y-2" data-testid="case-memory-facts">
              {groups.map((g) => (
                <div key={g.label}>
                  <dt className="text-[11px] font-semibold text-ink-faint">{g.label}</dt>
                  {g.items.map((v, i) => (
                    <dd key={i} className="ml-0 text-[12.5px] leading-5 text-ink">{v}</dd>
                  ))}
                </div>
              ))}
            </dl>
          )}
        </div>
      )}
    </section>
  );
}
