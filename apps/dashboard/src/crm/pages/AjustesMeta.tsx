import { useAuthSession } from "../../context/AuthSessionContext";
import { PageHeader } from "../ui";
import { FinanceSettingsPanel } from "./DatosPage";

/**
 * «Ajustes → Meta y socios»: the finance settings panel (monthly target + partner rule) reachable
 * from #/ajustes/meta. Admin only; anyone else sees a one-line guard. This avoids the dead end
 * where FinanzasSociosPage linked to «Ajustes → Metas y socios» but no such URL existed.
 */
export function AjustesMeta() {
  const { session } = useAuthSession();
  const isAdmin = session.kind === "signed_in" && session.operator.role === "admin";

  if (!isAdmin) {
    return (
      <div className="space-y-3">
        <PageHeader title="Meta y socios" />
        <p className="text-sm text-ink-muted">Sólo administración puede configurar la meta y la regla de socios.</p>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <PageHeader title="Meta y socios" subtitle="Meta mensual de ventas y regla de distribución entre socios." />
      <FinanceSettingsPanel />
    </div>
  );
}
