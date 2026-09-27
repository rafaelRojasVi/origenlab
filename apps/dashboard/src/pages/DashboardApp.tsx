import { AuthGate } from "../components/auth/AuthGate";
import { CrmApp } from "../crm/CrmApp";
import { useShellRoute } from "../crm/shellRoute";

/** One dashboard: a single Google Workspace sign-in gate around the CRM shell. */
export function DashboardApp() {
  const route = useShellRoute();
  return (
    <AuthGate>
      <CrmApp route={route} />
    </AuthGate>
  );
}
