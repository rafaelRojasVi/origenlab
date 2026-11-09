/**
 * «Socios» (admin only) — the active partner distribution rule and a history of rule changes.
 *
 * Shows the current rule (rule_name, valid_from, arriendo_clp, split_pct, tasa_sebastian,
 * sueldo_tatiana, arriendo_pct_empresa) and a summary of what it means for a sample month.
 * Changing the rule is done from Ajustes (this page reads only).
 *
 * This tab is admin-only in the nav (adminOnly: true in nav.ts). The route gate in crmRoute.ts
 * and the upstream route both enforce it; this page is an additional UX signal.
 */
import { formatMoney } from "../money";
import { EmptyState, PageHeader, ResourceGate, Skeleton, fmtDate } from "../ui";
import { useResource } from "../useResource";
import { fetchFinanceSettings } from "./financeApi";

const clp = (n: number) => formatMoney(String(n), "CLP") ?? "—";
const pct = (n: number) => `${(n * 100).toFixed(0)} %`;

const RULE_LABEL: Record<string, string> = {
  actual_50_50: "Actual 50/50",
  opcion_a: "Opción A",
  opcion_b: "Opción B",
  opcion_c: "Opción C",
  opcion_d: "Opción D",
};

export function FinanzasSociosPage() {
  const [state, reload] = useResource(fetchFinanceSettings);

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Socios"
        subtitle="Regla de distribución entre socios (sólo administración)"
      />
      <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={3} />} errorTitle="No se pudo leer la regla de socios">
        {(data) => {
          const rule = data.partner_rule;
          if (!rule) return <EmptyState title="Sin regla de socios configurada" />;
          return (
            <div className="flex flex-col gap-4">
              <div className="rounded-lg border border-line bg-surface-raised px-4 py-4">
                <div className="flex items-baseline gap-3">
                  <p className="text-[15px] font-semibold text-ink">{RULE_LABEL[rule.rule_name] ?? rule.rule_name}</p>
                  <p className="text-xs text-ink-muted">desde {fmtDate(rule.valid_from)}</p>
                </div>
                <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-3 text-sm">
                  <div>
                    <dt className="text-xs text-ink-muted">Arriendo</dt>
                    <dd className="font-medium tabular-nums text-ink">{clp(rule.arriendo_clp)}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-ink-muted">Split</dt>
                    <dd className="font-medium tabular-nums text-ink">{pct(rule.split_pct)}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-ink-muted">% arriendo empresa</dt>
                    <dd className="font-medium tabular-nums text-ink">{pct(rule.arriendo_pct_empresa)}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-ink-muted">Sueldo Tatiana</dt>
                    <dd className="font-medium tabular-nums text-ink">{clp(rule.sueldo_tatiana)}</dd>
                  </div>
                  {rule.tasa_sebastian != null && (
                    <div>
                      <dt className="text-xs text-ink-muted">Tasa Sebastián</dt>
                      <dd className="font-medium tabular-nums text-ink">{pct(rule.tasa_sebastian)}</dd>
                    </div>
                  )}
                </dl>
                {rule.notes && <p className="mt-3 text-xs text-ink-muted">{rule.notes}</p>}
              </div>
              <p className="text-xs text-ink-faint">
                La regla se modifica desde Ajustes › Regla de socios. Cada cambio crea una nueva fila
                (registro inmutable); la fila más reciente es la vigente.
              </p>
            </div>
          );
        }}
      </ResourceGate>
    </div>
  );
}
