/** Secciones principales del panel operador (Dashboard V2 — IA plana). */

export type DashboardSection =
  | "today"
  | "inbox"
  | "pipeline"
  | "deals"
  | "prospectos"
  | "cotizaciones"
  | "catalogo"
  | "suppliers"
  | "tenders"
  | "payments-logistics"
  | "contacts"
  | "system";

export type DashboardNavIconName =
  | "home"
  | "inbox"
  | "pipeline"
  | "deals"
  | "prospectos"
  | "quotes"
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

/**
 * Full section registry (12 ids), used for id → label lookups so deep-linked
 * hidden sections (inbox/deals/prospectos) still get a correct page title.
 * Sidebar rendering uses `DASHBOARD_TOP_NAV_ITEMS` below, not this list.
 */
export const DASHBOARD_NAV_ITEMS: DashboardNavItem[] = [
  {
    id: "today",
    label: "Inicio",
    shortLabel: "Inicio",
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
  {
    id: "pipeline",
    label: "Ventas",
    shortLabel: "Ventas",
    description: "Oportunidades de venta en gestión activa (CRM durable)",
    iconName: "pipeline",
  },
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
    id: "cotizaciones",
    label: "Cotizaciones",
    shortLabel: "Cotiz.",
    description: "Vista consolidada de cotizaciones (próximamente)",
    iconName: "quotes",
  },
  {
    id: "contacts",
    label: "Clientes",
    shortLabel: "Clientes",
    description: "Instituciones compradoras, contactos e historial",
    iconName: "contacts",
  },
  {
    id: "tenders",
    label: "Licitaciones",
    shortLabel: "Licit.",
    description: "Cola de equipos y señales de compras públicas",
    iconName: "tenders",
  },
  {
    id: "suppliers",
    label: "Proveedores",
    shortLabel: "Prov.",
    description: "Cotizaciones y seguimientos de proveedores",
    iconName: "suppliers",
  },
  {
    id: "payments-logistics",
    label: "Pagos y logística",
    shortLabel: "Pagos",
    description: "Banco, transferencias, DHL e importación",
    iconName: "payments",
  },
  {
    id: "catalogo",
    label: "Catálogo",
    shortLabel: "Catálogo",
    description: "Productos, reactivos, equipos y repuestos cotizables",
    iconName: "catalog",
  },
  {
    id: "system",
    label: "Sistema",
    shortLabel: "Sistema",
    description: "Estado del servicio y política de lectura",
    iconName: "system",
  },
];

/** The flat, ordered top-level nav — exactly the 9 sections in the new IA. */
export const DASHBOARD_TOP_NAV_IDS: readonly DashboardSection[] = [
  "today",
  "pipeline",
  "cotizaciones",
  "contacts",
  "tenders",
  "suppliers",
  "payments-logistics",
  "catalogo",
  "system",
];

export const DASHBOARD_TOP_NAV_ITEMS: DashboardNavItem[] = DASHBOARD_TOP_NAV_IDS.map(
  (id) => DASHBOARD_NAV_ITEMS.find((item) => item.id === id)!,
);

export const DEFAULT_DASHBOARD_SECTION: DashboardSection = "today";

export function dashboardSectionLabel(section: DashboardSection): string {
  return DASHBOARD_NAV_ITEMS.find((item) => item.id === section)?.label ?? section;
}
