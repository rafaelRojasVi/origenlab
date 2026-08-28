import { DashboardDataProvider } from "../context/DashboardDataContext";
import { DashboardShell } from "../components/layout/DashboardShell";
import { useDashboardSection } from "../lib/dashboardHashRoute";
import type { DashboardSection } from "../lib/dashboardNav";
import { CatalogPage } from "./CatalogPage";
import { ProspectosPage } from "./ProspectosPage";
import { ContactsPage } from "./ContactsPage";
import { DealsPage } from "./DealsPage";
import { InboxTriagePage } from "./InboxTriagePage";
import { PaymentsLogisticsPage } from "./PaymentsLogisticsPage";
import { SuppliersPage } from "./SuppliersPage";
import { SystemPage } from "./SystemPage";
import { TendersPage } from "./TendersPage";
import { TodaySummaryPage } from "./TodaySummaryPage";

function DashboardSectionView({ section }: { section: DashboardSection }) {
  switch (section) {
    case "today":
      return <TodaySummaryPage />;
    case "inbox":
      return <InboxTriagePage />;
    case "deals":
      return <DealsPage />;
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
    case "system":
      return <SystemPage />;
    default:
      return <TodaySummaryPage />;
  }
}

export function DashboardApp() {
  const [section, navigate] = useDashboardSection();

  return (
    <DashboardDataProvider>
      <DashboardShell section={section} onNavigate={navigate}>
        <DashboardSectionView section={section} />
      </DashboardShell>
    </DashboardDataProvider>
  );
}
