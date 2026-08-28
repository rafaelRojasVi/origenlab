import { useDashboardData } from "../context/DashboardDataContext";
import { CommercialDealHighlightCards } from "../components/commercial/CommercialDealHighlightCards";
import { CommercialDealsTable } from "../components/commercial/CommercialDealsTable";
import { CommercialOpportunitiesCockpit } from "../components/commercial/CommercialOpportunitiesCockpit";

export function DealsPage() {
  const {
    commercialDeals,
    commercialDealsLoading,
    commercialDealsError,
    commercialDealsErrorDetail,
    loadCommercialDeals,
    setContactEmail,
  } = useDashboardData();

  return (
    <div className="space-y-10">
      <CommercialOpportunitiesCockpit
        onSelectContact={setContactEmail}
      />

      <section className="space-y-5 border-t border-[var(--color-border)] pt-8">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">
            Registro financiero de negocios
          </h2>
          <p className="mt-1 text-sm text-[var(--color-muted)]">
            Ledger histórico de negocios y márgenes · evidencia de contexto, no flujo operativo.
          </p>
        </div>

        <CommercialDealHighlightCards data={commercialDeals} />
        <CommercialDealsTable
          data={commercialDeals}
          loading={commercialDealsLoading}
          error={commercialDealsError}
          errorDetail={commercialDealsErrorDetail}
          onRetry={() => void loadCommercialDeals()}
        />
      </section>
    </div>
  );
}
