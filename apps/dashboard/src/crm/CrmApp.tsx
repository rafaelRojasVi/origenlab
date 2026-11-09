import { Suspense, useCallback, useEffect, useState, type MouseEvent, type ReactNode } from "react";
import { SHARED_ACCOUNT, asShared } from "./sharedAccount";
import { MailSyncBanner } from "./MailSyncBanner";
import { Skeleton, Toaster } from "./ui";
import { googleLoginUrl } from "../api/authClient";
import { useAuthSession } from "../context/AuthSessionContext";
import { REDACTION_NOTICE, contactAddressesRedacted } from "./redaction";
import { AREA_HOME, areaOf, crmHash, sectionTitle, type AreaKey, type CotizadorPane, type CrmSection, type DatosTab } from "./crmRoute";
import { AREAS, visibleTabs, type AreaTab, type NavAccess } from "./nav";
import type { ShellRoute } from "./shellRoute";
import { OverviewPage } from "./pages/OverviewPage";
import { useMayOpenCotizador } from "./quotes/openOrCreateQuote";
import { lazyPage } from "../lib/lazyPage";
import { useTheme, type ThemeChoice } from "../lib/theme";

/**
 * Hoy is where every session lands, so it ships with the shell; every other section is its own
 * chunk, fetched the first time it is opened (the browser keeps it after that).
 */
const PipelinePage = lazyPage(() => import("./pages/PipelinePage").then((m) => m.PipelinePage));
const OrganizationsPage = lazyPage(() => import("./pages/OrganizationsPage").then((m) => m.OrganizationsPage));
const PeoplePage = lazyPage(() => import("./pages/PeoplePage").then((m) => m.PeoplePage));
const CatalogPage = lazyPage(() => import("./pages/CatalogPage").then((m) => m.CatalogPage));
const ModelSheetPage = lazyPage(() => import("./pages/ModelSheetPage").then((m) => m.ModelSheetPage));
const CotizadorModulePage = lazyPage(() => import("./pages/CotizadorModulePage").then((m) => m.CotizadorModulePage));
const LicitacionesPage = lazyPage(() => import("./tenders/LicitacionesPage").then((m) => m.LicitacionesPage));
const ProspectosPage = lazyPage(() => import("./prospects/ProspectosPage").then((m) => m.ProspectosPage));
const PagosPage = lazyPage(() => import("./pagos/PagosPage").then((m) => m.PagosPage));
const FinanzasMesPage = lazyPage(() => import("./finanzas/FinanzasMesPage").then((m) => m.FinanzasMesPage));
const FinanzasPorPagarPage = lazyPage(() => import("./finanzas/FinanzasPorPagarPage").then((m) => m.FinanzasPorPagarPage));
const FinanzasGastosPage = lazyPage(() => import("./finanzas/FinanzasGastosPage").then((m) => m.FinanzasGastosPage));
const FinanzasFinanciamientoPage = lazyPage(() => import("./finanzas/FinanzasFinanciamientoPage").then((m) => m.FinanzasFinanciamientoPage));
const FinanzasSociosPage = lazyPage(() => import("./finanzas/FinanzasSociosPage").then((m) => m.FinanzasSociosPage));
const MarketingPage = lazyPage(() => import("./pages/MarketingPage").then((m) => m.MarketingPage));
const HistoryPage = lazyPage(() => import("./pages/HistoryPage").then((m) => m.HistoryPage));
const OperacionesPage = lazyPage(() => import("./pages/OperacionesPage").then((m) => m.OperacionesPage));

/**
 * The dashboard: one shell, one navigation, one sign-in. Five areas (Hoy · Ventas · Finanzas ·
 * Catálogo · Ajustes) hold every page as a tab (Cotizaciones only for sales and admin, Catálogo web
 * only for admin; Operaciones' admin tabs inside).
 */
const NAV_COLLAPSED_KEY = "crm.nav.collapsed";

function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(NAV_COLLAPSED_KEY) === "1";
  } catch {
    return false;
  }
}

export function CrmApp({ route }: { route: ShellRoute }) {
  const [menuOpen, setMenuOpen] = useState(false);
  // On a wide screen the section list can fold into a rail of icons, so the Tablero gets the room.
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const toggleCollapsed = useCallback(() => {
    setCollapsed((v) => {
      try {
        window.localStorage.setItem(NAV_COLLAPSED_KEY, v ? "0" : "1");
      } catch {
        /* the choice just won't be remembered */
      }
      return !v;
    });
  }, []);
  const navigate = useCallback((section: CrmSection, id?: string | null, tab?: DatosTab) => {
    window.location.hash = crmHash(section, id, tab);
    window.scrollTo?.({ top: 0 });
  }, []);
  const title = sectionTitle(route.section);
  // The Tablero and the Cotizador use the whole screen; reading pages keep a comfortable line length.
  const wide = route.section === "oportunidades" || route.section === "cotizador";
  // A «Datos» tab is part of the address: a link to another tab remounts the page on it.
  const routeKey = route.tab ? `${route.section}/${route.tab}` : route.section;

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
        className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-ink focus:px-3 focus:py-1.5 focus:text-xs focus:text-on-solid"
      >
        Saltar al contenido
      </a>
      <TopBar onMenu={() => setMenuOpen((v) => !v)} menuOpen={menuOpen} wide={wide} />
      <MailSyncBanner refreshKey={routeKey} />
      <div className={`mx-auto flex w-full ${wide ? "max-w-none" : "max-w-[1600px]"}`}>
        <SideNav route={route} navigate={navigate} open={menuOpen} collapsed={collapsed} onToggle={toggleCollapsed} />
        <main id="crm-main" tabIndex={-1} className="min-w-0 flex-1 px-4 pb-10 pt-4 focus:outline-none sm:px-6">
          <SubNav section={route.section} navigate={navigate} collapsed={collapsed} menuOpen={menuOpen} />
          <div key={routeKey} className="animate-fade-in-up">
            <Suspense fallback={<Skeleton rows={6} />}>
              <Section section={route.section} id={route.id} tab={route.tab} pane={route.pane} navigate={navigate} />
            </Suspense>
          </div>
        </main>
      </div>
      <Toaster />
    </div>
  );
}

function Section({
  section,
  id,
  tab,
  pane,
  navigate,
}: {
  section: CrmSection;
  id: string | null;
  tab?: DatosTab;
  pane?: CotizadorPane;
  navigate: (s: CrmSection, id?: string | null, tab?: DatosTab) => void;
}) {
  switch (section) {
    case "oportunidades":
      return <PipelinePage key={id ?? "all"} initialOpportunityId={id} />;
    case "organizaciones":
      return <OrganizationsPage key={id ?? "all"} navigate={navigate} initialId={id} />;
    case "personas":
      return <PeoplePage key={id ?? "all"} navigate={navigate} initialId={id} />;
    case "catalogo":
      return <CatalogPage id={id} navigate={navigate} />;
    case "catalogo-web":
      return <ModelSheetPage id={id} navigate={navigate} />;
    case "cotizador":
      // Not keyed by the quote: the module stays mounted while its tabs change.
      return <CotizadorModulePage quoteId={id} pane={pane} navigate={navigate} />;
    case "licitaciones":
      return <LicitacionesPage key={id ?? "all"} id={id} navigate={navigate} />;
    case "prospectos":
      return <ProspectosPage key={id ?? "all"} id={id} navigate={navigate} />;
    case "fin-mes":
      return <FinanzasMesPage />;
    case "fin-por-pagar":
      return <FinanzasPorPagarPage />;
    case "fin-gastos":
      return <FinanzasGastosPage />;
    case "fin-financiamiento":
      return <FinanzasFinanciamientoPage />;
    case "fin-socios":
      return <FinanzasSociosPage />;
    case "pagos":
      return <PagosPage navigate={navigate} />;
    case "marketing":
      return <MarketingPage />;
    case "historial":
      return <HistoryPage navigate={navigate} />;
    case "datos":
      return <OperacionesPage navigate={navigate} tab={tab} />;
    default:
      return <OverviewPage navigate={navigate} />;
  }
}


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

export function TopBar({ onMenu, menuOpen, wide = false }: { onMenu: () => void; menuOpen: boolean; wide?: boolean }) {
  const { session, signOut, switchProfile } = useAuthSession();
  return (
    <header className="sticky top-0 z-30 border-b border-line bg-canvas-raised/95 backdrop-blur-sm">
      <div className={`mx-auto flex h-12 items-center gap-3 px-4 sm:px-6 ${wide ? "max-w-none" : "max-w-[1600px]"}`}>
        <button
          type="button"
          onClick={onMenu}
          aria-expanded={menuOpen}
          aria-controls="crm-sidenav"
          aria-label="Menú"
          className="-ml-1 h-8 w-8 shrink-0 rounded-md text-ink-muted hover:bg-canvas-sunken focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 lg:hidden"
        >
          ☰
        </button>
        <a href={crmHash("resumen")} className="flex shrink-0 items-center gap-2" aria-label="OrigenLab">
          <span aria-hidden="true" className="flex h-6 w-6 items-center justify-center rounded-md bg-brand-600 text-[11px] font-bold text-white">
            O
          </span>
          <span className="hidden text-[13px] font-semibold tracking-tight text-ink sm:inline">OrigenLab</span>
        </a>
        <span aria-hidden="true" className="hidden h-4 w-px bg-line-strong lg:inline" />
        <span className="hidden text-[13px] text-ink-muted lg:inline">Panel comercial</span>
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
          <ThemeToggle />
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

const THEME_NEXT: Record<ThemeChoice, ThemeChoice> = { system: "light", light: "dark", dark: "system" };
const THEME_LABEL: Record<ThemeChoice, string> = { system: "Sistema", light: "Claro", dark: "Oscuro" };
const THEME_ICON: Record<ThemeChoice, string> = {
  system: "M2.5 3.5h11v7h-11zM6 13.5h4M8 10.5v3",
  light: "M8 10.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5ZM8 1.5v1.5M8 13v1.5M1.5 8H3M13 8h1.5M3.4 3.4l1 1M11.6 11.6l1 1M3.4 12.6l1-1M11.6 4.4l1-1",
  dark: "M13 9.5A5.5 5.5 0 0 1 6.5 3a5.5 5.5 0 1 0 6.5 6.5Z",
};

/** One button cycles Sistema → Claro → Oscuro; the icon shows the current choice. */
function ThemeToggle() {
  const [choice, choose] = useTheme();
  const next = THEME_NEXT[choice];
  return (
    <button
      type="button"
      onClick={() => choose(next)}
      title={`Tema: ${THEME_LABEL[choice]} · cambiar a ${THEME_LABEL[next]}`}
      aria-label={`Tema: ${THEME_LABEL[choice]}. Cambiar a ${THEME_LABEL[next]}`}
      data-testid="crm-theme-toggle"
      className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-ink-muted transition-colors hover:bg-canvas-sunken hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
    >
      <svg viewBox="0 0 16 16" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d={THEME_ICON[choice]} />
      </svg>
    </button>
  );
}

/** Who may see which tab: Cotizaciones for those who quote, Catálogo web for an admin. */
function useNavAccess(): NavAccess {
  const { session } = useAuthSession();
  return { admin: session.kind === "signed_in" && session.operator.role === "admin", mayQuote: useMayOpenCotizador() };
}

function SideNav({
  route,
  navigate,
  open,
  collapsed,
  onToggle,
}: {
  route: ShellRoute;
  navigate: (s: CrmSection) => void;
  open: boolean;
  /** Wide screens only: the rail of icons. The phone menu always shows the names. */
  collapsed: boolean;
  onToggle: () => void;
}) {
  const activeArea = areaOf(route.section);
  const access = useNavAccess();
  return (
    <nav
      id="crm-sidenav"
      aria-label="Secciones del panel"
      className={`${open ? "block" : "hidden"} fixed inset-x-0 top-12 z-20 max-h-[calc(100vh-3rem)] overflow-y-auto border-b border-line bg-canvas-raised px-3 pb-3 pt-2 shadow-lg lg:sticky lg:top-12 lg:block lg:h-[calc(100vh-3rem)] lg:shrink-0 lg:border-b-0 lg:border-r lg:bg-transparent lg:pt-3 lg:shadow-none lg:transition-[width] lg:duration-150 ${
        collapsed ? "lg:w-14 lg:px-2" : "lg:w-52 lg:px-3"
      }`}
    >
      {/* The nav header: the fold toggle now; the goal chip of the «Franja de metas» joins it later. */}
      <div className="mb-2 hidden lg:flex">
        <button
          type="button"
          onClick={onToggle}
          aria-pressed={collapsed}
          title={collapsed ? "Mostrar los nombres" : "Ocultar los nombres"}
          className={`flex h-7 items-center gap-1.5 rounded-md text-[11px] text-ink-faint hover:bg-canvas-sunken hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 ${
            collapsed ? "w-10 justify-center" : "w-full px-2"
          }`}
        >
          <svg viewBox="0 0 16 16" className={`h-3.5 w-3.5 transition-transform ${collapsed ? "rotate-180" : ""}`} fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
            <path d="M10 3.5 5.5 8l4.5 4.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <span className={collapsed ? "sr-only" : ""}>{collapsed ? "Expandir menú" : "Contraer menú"}</span>
        </button>
      </div>
      <ul className="space-y-px">
        {AREAS.map((area) => {
          const current = area.key === activeArea;
          const areaTabs = visibleTabs(area, access);
          const tabs = current ? areaTabs : [];
          return (
            <li key={area.key}>
              <NavLink
                href={crmHash(AREA_HOME[area.key])}
                current={current}
                // An area with no tabs (Hoy) is itself the page; one with tabs marks the tab as the page.
                page={areaTabs.length === 0}
                collapsed={collapsed}
                icon={NAV_ICON[area.key]}
                title={collapsed ? area.label : undefined}
                onClick={(e) => {
                  e.preventDefault();
                  navigate(AREA_HOME[area.key]);
                }}
              >
                {area.label}
              </NavLink>
              {tabs.length > 0 ? (
                // The open area lists its tabs under it (canvas); the folded rail has no room, so the tab row above the page takes over.
                <ul className={`mb-2 ml-5 mt-0.5 border-l border-line ${collapsed ? "lg:hidden" : ""}`}>
                  {tabs.map((tab) => {
                    const on = tab.sections.includes(route.section);
                    return (
                      <li key={tab.label}>
                        <a
                          href={crmHash(tab.sections[0])}
                          aria-current={on ? "page" : undefined}
                          onClick={(e) => {
                            e.preventDefault();
                            navigate(tab.sections[0]);
                          }}
                          className={`-ml-px flex h-11 items-center rounded-r-md border-l-2 px-3 text-[13px] transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 lg:h-8 ${
                            on ? "border-accent font-semibold text-ink" : "border-transparent text-ink-muted hover:bg-canvas-sunken hover:text-ink"
                          }`}
                        >
                          <span className="truncate">{tab.label}</span>
                        </a>
                      </li>
                    );
                  })}
                </ul>
              ) : null}
            </li>
          );
        })}
      </ul>
      <p className={`mt-6 px-2 text-[10px] leading-4 text-ink-faint ${collapsed ? "lg:hidden" : ""}`}>
        Cada cambio queda registrado con quién lo hizo y cuándo.
      </p>
    </nav>
  );
}

/**
 * The tabs of the open area, above the page. On a wide screen the side nav already lists them, so
 * the row shows only on a phone or beside the folded rail; a two-page tab (Clientes, Listas y
 * llamadas) always adds its second row of links.
 */
function SubNav({
  section,
  navigate,
  collapsed,
  menuOpen,
}: {
  section: CrmSection;
  navigate: (s: CrmSection) => void;
  collapsed: boolean;
  /** The phone menu already lists these tabs under the open area: the row hides behind it. */
  menuOpen: boolean;
}) {
  const area = AREAS.find((a) => a.key === areaOf(section))!;
  const tabs = visibleTabs(area, useNavAccess());
  if (tabs.length === 0) return null;
  const open: AreaTab | undefined = tabs.find((t) => t.sections.includes(section));
  const go = (s: CrmSection) => (e: MouseEvent<HTMLAnchorElement>) => {
    e.preventDefault();
    navigate(s);
  };
  return (
    <div className={`mb-4 space-y-3 ${menuOpen ? "max-lg:hidden" : ""}`} data-testid="crm-subnav">
      <nav aria-label={`Pestañas de ${area.label}`} className={collapsed ? "" : "lg:hidden"}>
        <ul className="flex flex-wrap gap-x-5 border-b border-line">
          {tabs.map((tab) => {
            const on = tab === open;
            return (
              <li key={tab.label}>
                <a
                  href={crmHash(tab.sections[0])}
                  aria-current={on ? "page" : undefined}
                  onClick={go(tab.sections[0])}
                  className={`-mb-px inline-flex min-h-11 items-center border-b-2 text-[13px] transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 ${
                    on ? "border-accent font-semibold text-ink" : "border-transparent font-medium text-ink-muted hover:text-ink"
                  }`}
                >
                  {tab.label}
                </a>
              </li>
            );
          })}
        </ul>
      </nav>
      {open && open.sections.length > 1 ? (
        <div role="group" aria-label={open.label} className="inline-flex flex-wrap rounded-md bg-canvas-sunken p-0.5">
          {open.sections.map((s) => {
            const on = s === section;
            return (
              <a
                key={s}
                href={crmHash(s)}
                aria-current={on ? "page" : undefined}
                onClick={go(s)}
                className={`inline-flex min-h-11 items-center rounded-[5px] px-3 text-[13px] font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 ${
                  on ? "bg-canvas-raised text-ink shadow-[0_1px_2px_rgb(24_24_27/0.08)]" : "text-ink-muted hover:text-ink"
                }`}
              >
                {sectionTitle(s)}
              </a>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

/** 16 px line icons for the five areas, drawn here: the dashboard ships no icon library. */
const ICON_PATHS: Record<AreaKey, string> = {
  hoy: "M3 6.5h10M5.5 2.5v2M10.5 2.5v2M3.5 4.5h9a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1h-9a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1ZM6 10l1.5 1.5L10.5 8.5",
  ventas: "M2.5 3.5h3v9h-3zM6.5 3.5h3v6h-3zM10.5 3.5h3v4h-3z",
  finanzas: "M2.5 4.5h11v7h-11zM2.5 6.5h11M4.5 9.5h2.5",
  catalogo: "M3 2.5h7.5l2.5 2.5v8.5H3zM10.5 2.5V5H13M5.5 7.5h5M5.5 10h5",
  ajustes: "M2.5 4.5h6M11.5 4.5h2M2.5 11.5h2M7.5 11.5h6M10 3v3M6 10v3",
};

const NAV_ICON: Record<AreaKey, ReactNode> = Object.fromEntries(
  Object.entries(ICON_PATHS).map(([id, d]) => [
    id,
    <svg key={id} viewBox="0 0 16 16" className="h-4 w-4 shrink-0" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={d} />
    </svg>,
  ]),
) as Record<AreaKey, ReactNode>;

function NavLink({
  href,
  current,
  page = false,
  onClick,
  title,
  icon = null,
  collapsed = false,
  children,
}: {
  href: string;
  current: boolean;
  /** `aria-current="page"` rather than `"true"`: the link is the page itself, not its area. */
  page?: boolean;
  onClick?: (e: MouseEvent<HTMLAnchorElement>) => void;
  title?: string;
  icon?: ReactNode;
  collapsed?: boolean;
  children: ReactNode;
}) {
  return (
    <a
      href={href}
      title={title}
      aria-current={current ? (page ? "page" : "true") : undefined}
      onClick={onClick}
      className={`flex h-11 items-center lg:h-9 gap-2 rounded-md px-2 text-[13px] transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 ${collapsed ? "lg:w-10 lg:justify-center lg:px-0" : ""} ${
        current
          ? "bg-canvas-raised font-semibold text-ink shadow-[0_1px_2px_rgb(24_24_27/0.08)] ring-1 ring-line lg:bg-canvas-raised"
          : "text-ink-muted hover:bg-canvas-sunken hover:text-ink"
      }`}
    >
      <span className={current ? "text-accent" : ""}>{icon}</span>
      <span className={`truncate ${collapsed ? "lg:sr-only" : ""}`}>{children}</span>
    </a>
  );
}
