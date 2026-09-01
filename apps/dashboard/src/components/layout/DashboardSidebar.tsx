import { OrigenLabStaticLogo } from "../brand/OrigenLabStaticLogo";
import {
  DASHBOARD_TOP_NAV_ITEMS,
  type DashboardNavItem,
  type DashboardSection,
} from "../../lib/dashboardNav";
import { NavIcon } from "./NavIcon";

function navHref(section: DashboardSection): string {
  return section === "today" ? "#/" : `#/${section}`;
}

function SidebarCollapseToggle({
  collapsed,
  onToggle,
}: {
  collapsed: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className="shrink-0 rounded-lg p-1.5 text-slate-400 transition-colors hover:bg-slate-800 hover:text-white motion-reduce:transition-none"
      aria-expanded={!collapsed}
      aria-controls="dashboard-sidebar"
      aria-label={collapsed ? "Expandir navegación" : "Contraer navegación"}
      data-testid="sidebar-collapse-toggle"
    >
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        className={`h-4 w-4 transition-transform duration-200 motion-reduce:transition-none ${
          collapsed ? "rotate-180" : ""
        }`}
        aria-hidden
      >
        <path d="M15 6l-6 6 6 6" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </button>
  );
}

function NavLink({
  item,
  isActive,
  collapsed,
  onNavigate,
}: {
  item: DashboardNavItem;
  isActive: boolean;
  collapsed: boolean;
  onNavigate: (section: DashboardSection) => void;
}) {
  return (
    <a
      href={navHref(item.id)}
      onClick={(e) => {
        e.preventDefault();
        onNavigate(item.id);
      }}
      aria-current={isActive ? "page" : undefined}
      aria-label={item.label}
      title={collapsed ? item.label : item.description}
      className={`group flex items-center gap-3 rounded-lg text-sm font-medium transition-colors motion-reduce:transition-none ${
        collapsed ? "justify-center px-2 py-2.5" : "px-3 py-2"
      } ${
        isActive
          ? "bg-brand-600 text-white shadow-sm ring-1 ring-brand-700/50"
          : "text-slate-300 hover:bg-slate-800 hover:text-white"
      }`}
    >
      <NavIcon
        name={item.iconName}
        className={`h-5 w-5 shrink-0 ${isActive ? "text-white" : "text-slate-400 group-hover:text-white"}`}
      />
      {!collapsed ? <span className="truncate">{item.label}</span> : null}
    </a>
  );
}

export function DashboardSidebar({
  active,
  collapsed,
  onNavigate,
  onToggleCollapsed,
}: {
  active: DashboardSection;
  collapsed: boolean;
  onNavigate: (section: DashboardSection) => void;
  onToggleCollapsed: () => void;
}) {
  return (
    <aside
      id="dashboard-sidebar"
      className={`flex shrink-0 flex-col border-r border-slate-800 bg-slate-900 text-slate-100 shadow-lg transition-[width] duration-200 ease-in-out motion-reduce:transition-none ${
        collapsed ? "w-16" : "w-64"
      }`}
      data-testid="dashboard-sidebar"
      data-collapsed={collapsed ? "true" : "false"}
    >
      <div className="border-b border-slate-800 px-2.5 py-2.5">
        {collapsed ? (
          <div className="flex flex-col items-center gap-2">
            <div className="flex w-full items-center justify-between gap-1">
              <div className="flex min-w-0 flex-1 justify-center" data-testid="origenlab-logo-static">
                <span
                  className="flex h-8 w-8 items-center justify-center rounded-lg text-xs font-bold text-white"
                  aria-label="OrigenLab"
                  title="OrigenLab"
                >
                  OL
                </span>
              </div>
              <SidebarCollapseToggle collapsed={collapsed} onToggle={onToggleCollapsed} />
            </div>
          </div>
        ) : (
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0 flex-1">
              <OrigenLabStaticLogo compact />
            </div>
            <SidebarCollapseToggle collapsed={collapsed} onToggle={onToggleCollapsed} />
          </div>
        )}
      </div>

      <nav className="flex-1 overflow-y-auto px-2 py-3" aria-label="Navegación del panel">
        <ul className="space-y-0.5">
          {DASHBOARD_TOP_NAV_ITEMS.map((item) => (
            <li key={item.id}>
              <NavLink
                item={item}
                isActive={item.id === active}
                collapsed={collapsed}
                onNavigate={onNavigate}
              />
            </li>
          ))}
        </ul>
      </nav>
    </aside>
  );
}
