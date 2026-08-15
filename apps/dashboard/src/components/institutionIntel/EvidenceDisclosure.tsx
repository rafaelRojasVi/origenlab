import type { EvidenceRef } from "../../api/institutionIntel/types";

/** Every extracted fact should be traceable to its exact source — validated pattern from the concept mockups. */
export function EvidenceDisclosure({
  evidence,
  label = "Ver evidencia",
  defaultOpen = false,
}: {
  evidence: EvidenceRef;
  label?: string;
  defaultOpen?: boolean;
}) {
  return (
    <details className="mt-1.5 text-xs" open={defaultOpen}>
      <summary className="cursor-pointer select-none font-medium text-brand-700 hover:text-brand-900">
        {label}
      </summary>
      <div className="mt-1.5 rounded-md border-l-2 border-brand-100 bg-slate-50 px-2.5 py-2 text-[var(--color-muted)]">
        <p className="text-slate-700">&ldquo;{evidence.excerpt}&rdquo;</p>
        <p className="mt-1 font-mono text-[11px] text-[var(--color-muted)]">
          {evidence.documentLabel}
          {evidence.locator ? ` · ${evidence.locator}` : ""}
          {evidence.sourceHint ? ` · ${evidence.sourceHint}` : ""}
        </p>
      </div>
    </details>
  );
}
