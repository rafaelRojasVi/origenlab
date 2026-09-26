import { AuthGate } from "../components/auth/AuthGate";
import { CrmApp } from "../crm/CrmApp";
import { useIsCrmHash } from "../crm/crmRoute";
import { DashboardDataProvider } from "../context/DashboardDataContext";
import { DashboardShell } from "../components/layout/DashboardShell";
import { useDashboardSection } from "../lib/dashboardHashRoute";
import { buildVentasDeepLinkHash, useVentasDeepLinkOpportunityId } from "../lib/ventasDeepLink";
import type { DashboardSection } from "../lib/dashboardNav";
import { CatalogPage } from "./CatalogPage";
import { ProspectosPage } from "./ProspectosPage";
import { ContactsPage } from "./ContactsPage";
import { Contact360Page } from "./Contact360Page";
import { Institution360Page } from "./Institution360Page";
import { CommercialCasePage } from "./CommercialCasePage";
import { CrmV2Page } from "./CrmV2Page";
import { EvidenceReviewPage } from "./EvidenceReviewPage";
import { QuoteImportReviewPage } from "./QuoteImportReviewPage";
import { CaseArchivePage } from "./CaseArchivePage";
import { DealsPage } from "./DealsPage";
import { InboxTriagePage } from "./InboxTriagePage";
import { PaymentsLogisticsPage } from "./PaymentsLogisticsPage";
import { VentasPage } from "./VentasPage";
import { CotizacionesPage } from "./CotizacionesPage";
import { SuppliersPage } from "./SuppliersPage";
import { SystemPage } from "./SystemPage";
import { TendersPage } from "./TendersPage";
import { TodaySummaryPage } from "./TodaySummaryPage";

function DashboardSectionView({
  section,
  navigate,
}: {
  section: DashboardSection;
  navigate: (section: DashboardSection) => void;
}) {
  const deepLinkOpportunityId = useVentasDeepLinkOpportunityId();

  switch (section) {
    case "today":
      return <TodaySummaryPage />;
    case "inbox":
      return <InboxTriagePage />;
    case "pipeline":
      return <VentasPage deepLinkOpportunityId={deepLinkOpportunityId} />;
    case "cotizaciones":
      return (
        <CotizacionesPage
          onOpenVentas={(opportunityId) => {
            if (opportunityId) {
              window.location.hash = buildVentasDeepLinkHash(opportunityId);
            } else {
              navigate("pipeline");
            }
          }}
        />
      );
    case "deals":
      return <DealsPage onOpenPipeline={() => navigate("pipeline")} />;
    case "prospectos":
      return <ProspectosPage />;
    case "catalogo":
      return <CatalogPage />;
    case "suppliers":
      return <SuppliersPage />;
    case "tenders":
      return <TendersPage />;
    case "payments-logistics":
      return <PaymentsLogisticsPage />;
    case "contacts":
      return <ContactsPage />;
    case "contactos":
      return <Contact360Page />;
    case "instituciones":
      return <Institution360Page />;
    case "crm-v2":
      return <CrmV2Page />;
    case "revision":
      return <EvidenceReviewPage />;
    case "casos":
      return <CommercialCasePage />;
    case "importacion":
      return <QuoteImportReviewPage />;
    case "archivo":
      return <CaseArchivePage />;
    case "system":
      return <SystemPage />;
    default:
      return <TodaySummaryPage />;
  }
}

export function DashboardApp() {
  const [section, navigate] = useDashboardSection();
  const crm = useIsCrmHash();

  // The CRM workspace shares the sign-in gate and nothing else: it reads `/v2` only, so it
  // does not mount the V1 data provider.
  if (crm) {
    return (
      <AuthGate>
        <CrmApp />
      </AuthGate>
    );
  }

  // The gate sits outside the data provider so no operator data is requested before the
  // session is known.
  return (
    <AuthGate>
      <DashboardDataProvider>
        <DashboardShell section={section} onNavigate={navigate}>
          <DashboardSectionView section={section} navigate={navigate} />
        </DashboardShell>
      </DashboardDataProvider>
    </AuthGate>
  );
}
