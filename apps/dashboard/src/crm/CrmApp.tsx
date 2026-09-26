import { useEffect, useState } from "react";
import { useAuthSession } from "../context/AuthSessionContext";
import { REDACTION_NOTICE, contactAddressesRedacted } from "./redaction";
import { CRM_GROUP_LABEL, CRM_NAV, useCrmRoute, type CrmSection } from "./crmRoute";
import { DrivePage } from "./pages/DrivePage";
import { MarketingPage } from "./pages/MarketingPage";
import { OrganizationsPage } from "./pages/OrganizationsPage";
import { OverviewPage } from "./pages/OverviewPage";
import { PeoplePage } from "./pages/PeoplePage";
import { PipelinePage } from "./pages/PipelinePage";
import { ProvidersPage } from "./pages/ProvidersPage";
import { ReviewPage } from "./pages/ReviewPage";

/**
 * The CRM workspace: eight read-only sections over the V2 durable core. It sits beside the
 * V1 operator panel (every other hash route) and shares only its sign-in gate.
 */
export function CrmApp() {
  const [route, navigate] = useCrmRoute();
  const [menuOpen, setMenuOpen] = useState(false);
  const current = CRM_NAV.find((n) => n.id === route.section)!;

  useEffect(() => {
    document.title = `${current.label} · CRM OrigenLab`;
    setMenuOpen(false);
  }, [current.label]);

  return (
    <div className="min-h-screen bg-canvas text-ink">
      <a
        href="#crm-main"
        onClick={(e) => {
          e.preventDefault();
          document.getElementById("crm-main")?.focus();
        }}
        className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-ink focus:px-3 focus:py-1.5 focus:text-xs focus:text-white"
      >
        Saltar al contenido
      </a>
      <TopBar onMenu={() => setMenuOpen((v) => !v)} menuOpen={menuOpen} />
      <div className="mx-auto flex w-full max-w-[1600px]">
        <SideNav active={route.section} navigate={navigate} open={menuOpen} />
        <main id="crm-main" tabIndex={-1} className="min-w-0 flex-1 px-4 pb-10 pt-4 focus:outline-none sm:px-6">
          <div key={route.section} className="animate-fade-in-up">
            <Section section={route.section} id={route.id} navigate={navigate} />
          </div>
        </main>
      </div>
    </div>
  );
}

function Section({
  section,
  id,
  navigate,
}: {
  section: CrmSection;
  id: string | null;
  navigate: (s: CrmSection, id?: string | null) => void;
}) {
  switch (section) {
    case "oportunidades":
      return <PipelinePage key={id ?? "all"} initialOpportunityId={id} />;
    case "organizaciones":
      return <OrganizationsPage navigate={navigate} />;
    case "personas":
      return <PeoplePage navigate={navigate} />;
    case "proveedores":
      return <ProvidersPage />;
    case "drive":
      return <DrivePage navigate={navigate} />;
    case "marketing":
      return <MarketingPage />;
    case "revision":
      return <ReviewPage navigate={navigate} />;
    default:
      return <OverviewPage navigate={navigate} />;
  }
}

function TopBar({ onMenu, menuOpen }: { onMenu: () => void; menuOpen: boolean }) {
  const { session, signOut } = useAuthSession();
  return (
    <header className="sticky top-0 z-30 border-b border-line bg-canvas-raised/95 backdrop-blur-sm">
      <div className="mx-auto flex h-12 max-w-[1600px] items-center gap-3 px-4 sm:px-6">
        <button
          type="button"
          onClick={onMenu}
          aria-expanded={menuOpen}
          aria-controls="crm-sidenav"
          aria-label="Menú"
          className="-ml-1 h-8 w-8 rounded-md text-ink-muted hover:bg-canvas-sunken lg:hidden"
        >
          ☰
        </button>
        <a href="#/crm/resumen" className="flex items-center gap-2">
          <span aria-hidden="true" className="flex h-6 w-6 items-center justify-center rounded-md bg-brand-600 text-[11px] font-bold text-white">
            O
          </span>
          <span className="text-[13px] font-semibold tracking-tight text-ink">OrigenLab</span>
        </a>
        <span aria-hidden="true" className="h-4 w-px bg-line-strong" />
        <span className="text-[13px] text-ink-muted">CRM</span>
        <span className="hidden rounded-full border border-line bg-canvas-sunken px-2 py-px text-[11px] font-medium text-ink-muted sm:inline" data-testid="crm-read-only-chip">
          Sólo lectura · datos locales
        </span>
        {contactAddressesRedacted(session) ? (
          <span
            className="rounded-full border border-warn/30 bg-warn-bg px-2 py-px text-[11px] font-medium text-warn"
            title="La API enmascara toda dirección de correo y teléfono para este rol; lo que ves como ***@dominio no es un dato faltante."
            data-testid="crm-redaction-chip"
          >
            {REDACTION_NOTICE}
          </span>
        ) : null}
        <div className="ml-auto flex items-center gap-2">
          <a href="#/cotizaciones" className="hidden text-xs text-ink-muted hover:text-ink md:inline">
            Panel anterior
          </a>
          {session.kind === "signed_in" ? (
            <span
              className="inline-flex items-center gap-1.5 rounded-full border border-line bg-canvas-raised py-0.5 pl-1 pr-1 text-xs text-ink-muted"
              title={`${session.operator.displayName} · ${session.operator.role}`}
              data-testid="crm-operator-chip"
            >
              <span aria-hidden="true" className="flex h-5 w-5 items-center justify-center rounded-full bg-canvas-sunken text-[10px] font-semibold text-ink">
                {(session.operator.displayName || session.operator.email).slice(0, 1).toUpperCase()}
              </span>
              <span className="hidden max-w-[14rem] truncate sm:inline">{session.operator.email}</span>
              {session.method === "dev_header" ? (
                <span className="rounded-full bg-warn-bg px-1.5 text-[10px] font-semibold text-warn">desarrollo</span>
              ) : (
                <button
                  type="button"
                  onClick={() => void signOut()}
                  className="rounded-full px-2 py-0.5 text-[11px] font-medium hover:bg-canvas-sunken hover:text-ink"
                >
                  Salir
                </button>
              )}
            </span>
          ) : null}
        </div>
      </div>
    </header>
  );
}

function SideNav({
  active,
  navigate,
  open,
}: {
  active: CrmSection;
  navigate: (s: CrmSection) => void;
  open: boolean;
}) {
  const groups = ["comercial", "archivo", "control"] as const;
  return (
    <nav
      id="crm-sidenav"
      aria-label="Secciones del CRM"
      className={`${open ? "block" : "hidden"} fixed inset-x-0 top-12 z-20 max-h-[calc(100vh-3rem)] overflow-y-auto border-b border-line bg-canvas-raised px-3 pb-3 pt-2 shadow-lg lg:sticky lg:top-12 lg:block lg:h-[calc(100vh-3rem)] lg:w-52 lg:shrink-0 lg:border-b-0 lg:border-r lg:bg-transparent lg:px-3 lg:pt-4 lg:shadow-none`}
    >
      {groups.map((g) => (
        <div key={g} className="mb-4">
          <p className="px-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-ink-faint">{CRM_GROUP_LABEL[g]}</p>
          <ul className="space-y-px">
            {CRM_NAV.filter((n) => n.group === g).map((n) => {
              const current = n.id === active;
              return (
                <li key={n.id}>
                  <a
                    href={`#/crm/${n.id}`}
                    aria-current={current ? "page" : undefined}
                    onClick={(e) => {
                      e.preventDefault();
                      navigate(n.id);
                    }}
                    className={`flex h-8 items-center rounded-md px-2 text-[13px] transition-colors ${
                      current
                        ? "bg-canvas-raised font-semibold text-ink shadow-[0_1px_2px_rgb(24_24_27/0.08)] ring-1 ring-line lg:bg-canvas-raised"
                        : "text-ink-muted hover:bg-canvas-sunken hover:text-ink"
                    }`}
                  >
                    <span
                      aria-hidden="true"
                      className={`mr-2 h-3.5 w-0.5 rounded-full ${current ? "bg-brand-600" : "bg-transparent"}`}
                    />
                    {n.label}
                  </a>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
      <p className="mt-6 px-2 text-[10px] leading-4 text-ink-faint">
        Todas las acciones de escritura están desactivadas hasta su aprobación.
      </p>
    </nav>
  );
}
