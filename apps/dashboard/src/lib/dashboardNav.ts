/** Secciones principales del panel operador (Phase 7B). */

export type DashboardSection =
  | "today"
  | "inbox"
  | "deals"
  | "prospectos"
  | "catalogo"
  | "suppliers"
  | "tenders"
  | "payments-logistics"
  | "contacts"
  | "system";

export type DashboardNavIconName =
  | "home"
  | "inbox"
  | "deals"
  | "prospectos"
  | "contacts"
  | "tenders"
  | "payments"
  | "suppliers"
  | "catalog"
  | "system";

export interface DashboardNavItem {
  id: DashboardSection;
  label: string;
  shortLabel: string;
  description: string;
  iconName: DashboardNavIconName;
}

export interface DashboardNavGroup {
  id: string;
  label: string;
  items: DashboardNavItem[];
}

export const DASHBOARD_NAV_GROUPS: DashboardNavGroup[] = [
  {
    id: "inicio",
    label: "Inicio",
    items: [
      {
        id: "today",
        label: "Hoy",
        shortLabel: "Hoy",
        description: "Resumen del día y conteos",
        iconName: "home",
      },
      {
        id: "inbox",
        label: "Bandeja de revisión",
        shortLabel: "Bandeja",
        description: "Correos tibios con filtros por rol",
        iconName: "inbox",
      },
    ],
  },
  {
    id: "comercial",
    label: "Comercial",
    items: [
      {
        id: "deals",
        label: "Negocios",
        shortLabel: "Negocios",
        description: "Espejo de negocios comerciales",
        iconName: "deals",
      },
      {
        id: "prospectos",
        label: "Prospectos",
        shortLabel: "Prospectos",
        description: "Nuevas oportunidades de clientes (investigación DeepSearch)",
        iconName: "prospectos",
      },
      {
        id: "contacts",
        label: "Clientes / instituciones",
        shortLabel: "Clientes",
        description: "Instituciones compradoras, contactos e historial",
        iconName: "contacts",
      },
    ],
  },
  {
    id: "operacion",
    label: "Operación",
    items: [
      {
        id: "tenders",
        label: "Licitaciones / equipos",
        shortLabel: "Licit.",
        description: "Cola de equipos y señales de compras públicas",
        iconName: "tenders",
      },
      {
        id: "payments-logistics",
        label: "Pagos y logística",
        shortLabel: "Pagos",
        description: "Banco, transferencias, DHL e importación",
        iconName: "payments",
      },
      {
        id: "suppliers",
        label: "Proveedores",
        shortLabel: "Prov.",
        description: "Cotizaciones y seguimientos de proveedores",
        iconName: "suppliers",
      },
      {
        id: "catalogo",
        label: "Catálogo",
        shortLabel: "Catálogo",
        description: "Productos, reactivos, equipos y repuestos cotizables",
        iconName: "catalog",
      },
    ],
  },
  {
    id: "sistema",
    label: "Sistema",
    items: [
      {
        id: "system",
        label: "Sistema",
        shortLabel: "Sistema",
        description: "Estado del servicio y política de lectura",
        iconName: "system",
      },
    ],
  },
];

export const DASHBOARD_NAV_ITEMS: DashboardNavItem[] = DASHBOARD_NAV_GROUPS.flatMap(
  (group) => group.items,
);

export const DEFAULT_DASHBOARD_SECTION: DashboardSection = "today";

export function dashboardSectionLabel(section: DashboardSection): string {
  return DASHBOARD_NAV_ITEMS.find((item) => item.id === section)?.label ?? section;
}

export function dashboardSectionGroupLabel(section: DashboardSection): string | null {
  for (const group of DASHBOARD_NAV_GROUPS) {
    if (group.items.some((item) => item.id === section)) {
      return group.label;
    }
  }
  return null;
}
