import { useCallback, useEffect, useState, type MouseEvent, type ReactNode } from "react";
import { googleLoginUrl } from "../api/authClient";
import { useAuthSession } from "../context/AuthSessionContext";
import { REDACTION_NOTICE, contactAddressesRedacted } from "./redaction";
import { CRM_GROUP_LABEL, CRM_NAV, crmHash, type CrmSection } from "./crmRoute";
import type { ShellRoute } from "./shellRoute";
import { DrivePage } from "./pages/DrivePage";
import { MarketingPage } from "./pages/MarketingPage";
import { OrganizationsPage } from "./pages/OrganizationsPage";
import { OverviewPage } from "./pages/OverviewPage";
import { PeoplePage } from "./pages/PeoplePage";
import { PipelinePage } from "./pages/PipelinePage";
import { ProvidersPage } from "./pages/ProvidersPage";
import { ReviewPage } from "./pages/ReviewPage";

/**
 * The dashboard: one shell, one navigation, one sign-in. The eight CRM sections over the V2
 * durable core are the whole navigation.
 */
export function CrmApp({ route }: { route: ShellRoute }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const navigate = useCallback((section: CrmSection, id?: string | null) => {
    window.location.hash = crmHash(section, id);
    window.scrollTo?.({ top: 0 });
  }, []);
  const title = CRM_NAV.find((n) => n.id === route.section)!.label;
  const routeKey = route.section;

  useEffect(() => {
    document.title = `${title} · OrigenLab`;
    setMenuOpen(false);
  }, [title, routeKey]);

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
        <SideNav route={route} navigate={navigate} open={menuOpen} />
        <main id="crm-main" tabIndex={-1} className="min-w-0 flex-1 px-4 pb-10 pt-4 focus:outline-none sm:px-6">
          <div key={routeKey} className="animate-fade-in-up">
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

/** The shared account the team works in; Drive and Gmail open as it, not as the browser's first account. */
const SHARED_ACCOUNT = "contacto@origenlab.cl";
const asShared = (url: string) => `${url}?authuser=${encodeURIComponent(SHARED_ACCOUNT)}`;

/** Always-visible shortcuts to the tools the team leaves the panel for. */
export const QUICK_LINKS: { label: string; short: string; href: string; title: string }[] = [
  { label: "Drive", short: "Drive", href: asShared("https://drive.google.com/drive/my-drive"), title: `Google Drive de ${SHARED_ACCOUNT}` },
  { label: "Gmail", short: "Gmail", href: asShared("https://mail.google.com/mail/"), title: `Gmail de ${SHARED_ACCOUNT}` },
  { label: "Sitio web", short: "Web", href: "https://origenlab.cl/", title: "origenlab.cl" },
];

function QuickLinks() {
  return (
    <nav aria-label="Accesos directos" className="flex shrink-0 items-center gap-0.5 sm:gap-1">
      {QUICK_LINKS.map((l) => (
        <a
          key={l.label}
          href={l.href}
          target="_blank"
          rel="noopener noreferrer"
          title={l.title}
          aria-label={l.label}
          className="inline-flex h-7 items-center gap-1 rounded-md border border-line bg-canvas-raised px-1.5 text-[12px] font-medium text-ink-muted transition-colors hover:border-line-strong hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 sm:px-2"
        >
          {/* Phones get the short word and no arrow, so the sign-out button stays on screen. */}
          <span className="sm:hidden">{l.short}</span>
          <span className="hidden sm:inline">{l.label}</span>
          <span aria-hidden="true" className="hidden text-[10px] sm:inline">
            ↗
          </span>
        </a>
      ))}
    </nav>
  );
}

export function TopBar({ onMenu, menuOpen }: { onMenu: () => void; menuOpen: boolean }) {
  const { session, signOut, switchProfile } = useAuthSession();
  return (
    <header className="sticky top-0 z-30 border-b border-line bg-canvas-raised/95 backdrop-blur-sm">
      <div className="mx-auto flex h-12 max-w-[1600px] items-center gap-3 px-4 sm:px-6">
        <button
          type="button"
          onClick={onMenu}
          aria-expanded={menuOpen}
          aria-controls="crm-sidenav"
          aria-label="Menú"
          className="-ml-1 h-8 w-8 shrink-0 rounded-md text-ink-muted hover:bg-canvas-sunken lg:hidden"
        >
          ☰
        </button>
        <a href="#/crm/resumen" className="flex shrink-0 items-center gap-2" aria-label="OrigenLab">
          <span aria-hidden="true" className="flex h-6 w-6 items-center justify-center rounded-md bg-brand-600 text-[11px] font-bold text-white">
            O
          </span>
          <span className="hidden text-[13px] font-semibold tracking-tight text-ink sm:inline">OrigenLab</span>
        </a>
        <span aria-hidden="true" className="hidden h-4 w-px bg-line-strong lg:inline" />
        <span className="hidden text-[13px] text-ink-muted lg:inline">Panel comercial</span>
        <span className="hidden shrink-0 rounded-full border border-line bg-canvas-sunken px-2 py-px text-[11px] font-medium text-ink-muted xl:inline" data-testid="crm-read-only-chip">
          Sin envíos · datos locales
        </span>
        {contactAddressesRedacted(session) ? (
          <span
            className="min-w-0 truncate rounded-full border border-warn/30 bg-warn-bg px-2 py-px text-[11px] font-medium text-warn"
            title="La API enmascara toda dirección de correo y teléfono para este rol; lo que ves como ***@dominio no es un dato faltante."
            data-testid="crm-redaction-chip"
          >
            {REDACTION_NOTICE}
          </span>
        ) : null}
        <div className="ml-auto flex min-w-0 items-center gap-2">
          <QuickLinks />
          {session.kind === "signed_in" ? (
            <span
              className="inline-flex min-w-0 items-center gap-1.5 rounded-full border border-line bg-canvas-raised py-0.5 pl-1 pr-1 text-xs text-ink-muted"
              title={`${session.operator.displayName} · ${session.profile?.roleLabel || ROLE_LABEL[session.operator.role] || session.operator.role}`}
              data-testid="crm-operator-chip"
            >
              <span aria-hidden="true" className="flex h-5 w-5 items-center justify-center rounded-full bg-canvas-sunken text-[10px] font-semibold text-ink">
                {(session.operator.displayName || session.operator.email).slice(0, 1).toUpperCase()}
              </span>
              <span
                className={`${session.canSwitchProfile ? "max-w-[5.5rem]" : "hidden"} truncate font-medium text-ink sm:inline sm:max-w-[14rem]`}
                data-testid="crm-operator-name"
              >
                {session.operator.displayName || session.operator.email}
              </span>
              <span className="hidden rounded-full bg-canvas-sunken px-1.5 text-[10px] font-semibold text-ink-muted sm:inline" data-testid="crm-operator-role">
                {session.profile?.roleLabel || (ROLE_LABEL[session.operator.role] ?? session.operator.role)}
              </span>
              {session.canSwitchProfile ? (
                <>
                  {session.method === "dev_profile" ? (
                    <span className="hidden rounded-full bg-warn-bg px-1.5 text-[10px] font-semibold text-warn sm:inline">desarrollo</span>
                  ) : null}
                  <button
                    type="button"
                    onClick={() => void switchProfile?.()}
                    className="whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-medium hover:bg-canvas-sunken hover:text-ink"
                    title="Vuelve a la pantalla de perfiles. La sesión de Google sigue abierta."
                    aria-label="Cambiar perfil"
                    data-testid="crm-switch-profile"
                  >
                    <span className="sm:hidden">Perfil</span>
                    <span className="hidden sm:inline">Cambiar perfil</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => void signOut()}
                    className="whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-medium hover:bg-canvas-sunken hover:text-ink"
                    aria-label="Cerrar sesión"
                    data-testid="crm-sign-out"
                  >
                    <span className="sm:hidden">Salir</span>
                    <span className="hidden sm:inline">Cerrar sesión</span>
                  </button>
                </>
              ) : session.method === "dev_header" ? (
                <span className="hidden rounded-full bg-warn-bg px-1.5 text-[10px] font-semibold text-warn sm:inline">desarrollo</span>
              ) : (
                <>
                  <button
                    type="button"
                    onClick={() => void switchAccount(signOut)}
                    className="hidden rounded-full px-2 py-0.5 text-[11px] font-medium hover:bg-canvas-sunken hover:text-ink sm:inline"
                    title="Cierra esta sesión y abre el selector de cuentas de Google"
                    data-testid="crm-switch-account"
                  >
                    Cambiar cuenta
                  </button>
                  <button
                    type="button"
                    onClick={() => void signOut()}
                    className="whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-medium hover:bg-canvas-sunken hover:text-ink"
                    data-testid="crm-sign-out"
                  >
                    Cerrar sesión
                  </button>
                </>
              )}
            </span>
          ) : null}
        </div>
      </div>
    </header>
  );
}

const ROLE_LABEL: Record<string, string> = { admin: "Administración", sales: "Ventas", viewer: "Lectura" };

/**
 * Account switching goes through Google, never through a user picker: close this session,
 * then start a new sign-in, which asks Google for its account chooser (`prompt=select_account`).
 */
async function switchAccount(signOut: () => Promise<boolean>): Promise<void> {
  // Only a confirmed logout moves on to Google; a failed one stays here, signed in, and says so.
  if (await signOut()) {
    window.location.assign(googleLoginUrl());
  }
}

function SideNav({
  route,
  navigate,
  open,
}: {
  route: ShellRoute;
  navigate: (s: CrmSection) => void;
  open: boolean;
}) {
  const groups = ["comercial", "archivo", "control"] as const;
  const activeCrm = route.section;
  return (
    <nav
      id="crm-sidenav"
      aria-label="Secciones del panel"
      className={`${open ? "block" : "hidden"} fixed inset-x-0 top-12 z-20 max-h-[calc(100vh-3rem)] overflow-y-auto border-b border-line bg-canvas-raised px-3 pb-3 pt-2 shadow-lg lg:sticky lg:top-12 lg:block lg:h-[calc(100vh-3rem)] lg:w-56 lg:shrink-0 lg:border-b-0 lg:border-r lg:bg-transparent lg:px-3 lg:pt-4 lg:shadow-none`}
    >
      {groups.map((g) => (
        <div key={g} className="mb-4">
          <p className="px-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-ink-faint">{CRM_GROUP_LABEL[g]}</p>
          <ul className="space-y-px">
            {CRM_NAV.filter((n) => n.group === g).map((n) => (
              <li key={n.id}>
                <NavLink
                  href={`#/crm/${n.id}`}
                  current={n.id === activeCrm}
                  onClick={(e) => {
                    e.preventDefault();
                    navigate(n.id);
                  }}
                >
                  {n.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </div>
      ))}
      <p className="mt-6 px-2 text-[10px] leading-4 text-ink-faint">
        Las únicas escrituras son de Marketing — borradores, congelar una audiencia y la planificación
        interna — donde el entorno las habilita. Todo lo demás es de lectura, y nada se envía.
      </p>
    </nav>
  );
}

function NavLink({
  href,
  current,
  onClick,
  title,
  children,
}: {
  href: string;
  current: boolean;
  onClick?: (e: MouseEvent<HTMLAnchorElement>) => void;
  title?: string;
  children: ReactNode;
}) {
  return (
    <a
      href={href}
      title={title}
      aria-current={current ? "page" : undefined}
      onClick={onClick}
      className={`flex h-8 items-center rounded-md px-2 text-[13px] transition-colors ${
        current
          ? "bg-canvas-raised font-semibold text-ink shadow-[0_1px_2px_rgb(24_24_27/0.08)] ring-1 ring-line lg:bg-canvas-raised"
          : "text-ink-muted hover:bg-canvas-sunken hover:text-ink"
      }`}
    >
      <span aria-hidden="true" className={`mr-2 h-3.5 w-0.5 shrink-0 rounded-full ${current ? "bg-brand-600" : "bg-transparent"}`} />
      {children}
    </a>
  );
}
