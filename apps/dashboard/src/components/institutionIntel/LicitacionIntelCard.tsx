import { useEffect, useState } from "react";
import type { ItemBudgetRow, LicitacionIntel, TermFact } from "../../api/institutionIntel/types";
import { institutionIntelAdapter } from "../../api/institutionIntel/adapter";
import { TERM_FACT_STATE_LABEL, formatEquipmentCategory } from "../../lib/institutionIntelLabels";
import { AvailabilityBlock } from "./AvailabilityBlock";
import { EvidenceDisclosure } from "./EvidenceDisclosure";
import { EligibilityBadge } from "./IntelBadges";

const TERM_STATE_TONE: Record<TermFact["state"], string> = {
  explicit: "bg-emerald-50 text-emerald-700",
  derived: "bg-sky-50 text-sky-700",
  not_explicitly_found: "bg-slate-100 text-slate-500",
  unknown: "bg-slate-100 text-slate-500",
  conflicting: "bg-amber-50 text-amber-700",
};

function TermFactTile({ fact }: { fact: TermFact }) {
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-3.5 py-3">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xs font-medium text-[var(--color-muted)]">{fact.label}</span>
        <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${TERM_STATE_TONE[fact.state]}`}>
          {TERM_FACT_STATE_LABEL[fact.state]}
        </span>
      </div>
      <p className="mt-0.5 text-base font-bold tabular-nums text-slate-900">
        {fact.value === null
          ? "—"
          : `${typeof fact.value === "number" ? fact.value.toLocaleString("es-CL") : fact.value}${
              fact.unit ? ` ${fact.unit}` : ""
            }`}
      </p>
      {fact.evidence ? <EvidenceDisclosure evidence={fact.evidence} /> : null}
      {fact.candidates && fact.candidates.length > 0 ? (
        <div className="mt-1.5 space-y-1">
          {fact.candidates.map((c, i) => (
            <div key={i} className="text-[11px] text-amber-900">
              <span className="font-medium">{c.value}</span>
              <EvidenceDisclosure evidence={c.evidence} label="Ver fuente" />
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function ItemBudgetLine({ item }: { item: ItemBudgetRow }) {
  return (
    <div className="rounded-md border border-[var(--color-border)] bg-slate-50/40 px-3 py-2">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-slate-800">{item.itemLabel}</span>
        <div className="flex items-center gap-2 text-xs">
          {item.quantity != null ? (
            <span className="text-[var(--color-muted)]">Cant. {item.quantity}</span>
          ) : null}
          {item.amount != null ? (
            <span className="font-bold tabular-nums text-slate-900">
              {item.amount.toLocaleString("es-CL")} {item.currency ?? ""}
            </span>
          ) : (
            <span className="italic text-[var(--color-muted)]">No desglosado — incluido en el total</span>
          )}
        </div>
      </div>
      {item.evidence ? <EvidenceDisclosure evidence={item.evidence} /> : null}
    </div>
  );
}

export function LicitacionIntelCard({ tenderCode }: { tenderCode: string }) {
  const [intel, setIntel] = useState<LicitacionIntel | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void institutionIntelAdapter.getLicitacionIntel(tenderCode).then((data) => {
      if (!cancelled) {
        setIntel(data);
        setLoading(false);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [tenderCode]);

  if (loading) {
    return <div className="h-24 animate-pulse rounded-lg bg-slate-100" role="status" aria-live="polite" />;
  }

  if (!intel) {
    return (
      <p className="rounded-md border border-dashed border-[var(--color-border)] px-3 py-2 text-xs text-[var(--color-muted)]">
        Inteligencia de anexos no disponible para esta licitación todavía.
      </p>
    );
  }

  return (
    <div className="space-y-5" data-testid="licitacion-intel-card">
      <div className="flex items-center gap-2">
        <EligibilityBadge status={intel.eligibilityStatus} />
        {intel.procurementMethodRaw ? (
          <span className="text-xs text-[var(--color-muted)]">Método: {intel.procurementMethodRaw}</span>
        ) : null}
      </div>

      <section>
        <h3 className="mb-1 text-sm font-semibold text-slate-900">Términos comerciales</h3>
        <p className="mb-2 text-xs text-[var(--color-muted)]">
          Extraídos de las bases y anexos técnicos, no del resumen de la ficha.
        </p>
        <AvailabilityBlock
          availed={intel.terms}
          render={(terms) => (
            <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
              {terms.map((fact) => (
                <TermFactTile key={fact.fieldName} fact={fact} />
              ))}
            </div>
          )}
        />

        <div className="mt-3">
          <div className="mb-1.5 flex items-center justify-between">
            <span className="text-xs font-semibold text-slate-700">Presupuesto por producto</span>
            {intel.totalBudgetReconciled ? (
              <span className="text-[11px] font-medium text-emerald-700">
                ✓ Suma coincide con el total
              </span>
            ) : null}
          </div>
          <AvailabilityBlock
            availed={intel.itemBudget}
            emptyLabel="Sin desglose por producto — solo se conoce el total de la licitación."
            render={(items) => (
              <div className="space-y-1.5">
                {items.map((item, i) => (
                  <ItemBudgetLine key={i} item={item} />
                ))}
              </div>
            )}
          />
        </div>
      </section>

      <section>
        <h3 className="mb-1 text-sm font-semibold text-slate-900">Reconocimiento por anexo</h3>
        <p className="mb-2 text-xs text-[var(--color-muted)]">
          Comparación entre lo que dice el resumen de la licitación y lo que dicen sus anexos.
        </p>
        <AvailabilityBlock
          availed={intel.recognitionDelta}
          notAvailableLabel="Sin evidencia de anexo leída todavía para esta licitación."
          emptyLabel="Anexo leído — no cambió la categorización del resumen."
          render={(delta) => (
            <div>
              {delta.addedCategories.length > 0 ? (
                <span className="mb-2 inline-block rounded bg-emerald-50 px-2 py-0.5 text-[11px] font-bold uppercase tracking-wide text-emerald-700">
                  Anexo agregó equipo
                </span>
              ) : null}
              <div className="flex flex-wrap items-center gap-2 text-sm">
                {delta.baselineCategories.map((c) => (
                  <span key={c} className="rounded-full border border-[var(--color-border)] bg-white px-2.5 py-1 text-slate-700">
                    {formatEquipmentCategory(c)}
                  </span>
                ))}
                {delta.addedCategories.length > 0 ? <span className="text-[var(--color-muted)]">→</span> : null}
                {delta.addedCategories.map((c) => (
                  <span
                    key={c}
                    className="rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-1 font-semibold text-emerald-700"
                  >
                    + {formatEquipmentCategory(c)}
                  </span>
                ))}
              </div>
              {delta.evidence ? (
                <EvidenceDisclosure
                  evidence={delta.evidence}
                  label={`Ver evidencia (confianza: ${delta.confidenceBand})`}
                  defaultOpen
                />
              ) : null}
            </div>
          )}
        />
      </section>

      <section>
        <h3 className="mb-1 text-sm font-semibold text-slate-900">Cobertura de lectura</h3>
        <AvailabilityBlock
          availed={intel.coverage}
          render={(coverage) => (
            <div>
              <div className="flex items-center gap-2">
                <div className="h-1.5 flex-1 rounded-full bg-slate-100">
                  <div
                    className={`h-full rounded-full ${
                      coverage.incompleteReasonCodes.length > 0 ? "bg-amber-500" : "bg-emerald-600"
                    }`}
                    style={{
                      width: `${
                        coverage.documentsDiscovered > 0
                          ? Math.round((coverage.documentsRead / coverage.documentsDiscovered) * 100)
                          : 0
                      }%`,
                    }}
                  />
                </div>
                <span className="shrink-0 text-xs font-semibold text-slate-700">
                  {coverage.documentsRead} / {coverage.documentsDiscovered} documentos
                </span>
              </div>
              <p className="mt-1.5 text-[11px] text-[var(--color-muted)]">
                Si un término no aparece arriba, es porque genuinamente no está especificado — no por un
                documento sin leer.
              </p>
            </div>
          )}
        />
      </section>
    </div>
  );
}
