/** Secciones principales del panel operador (Dashboard V2 — IA Cotizaciones-first). */

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
 * Full section registry (12 ids), used for id -> label lookups so deep-linked
 * hidden sections (today/deals/suppliers/payments-logistics) still get a
 * correct page title. Sidebar rendering uses `DASHBOARD_TOP_NAV_ITEMS`
 * below, not this list.
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
    id: "cotizaciones",
    label: "Cotizaciones",
    shortLabel: "Cotiz.",
    description: "Cola global de cotizaciones y su carpeta en Drive (CRM durable)",
    iconName: "quotes",
  },
  {
    id: "tenders",
    label: "Licitaciones",
    shortLabel: "Licit.",
    description: "Cola de equipos y señales de compras públicas",
    iconName: "tenders",
  },
  {
    id: "pipeline",
    label: "Ventas",
    shortLabel: "Ventas",
    description: "Oportunidades de venta en gestión activa (CRM durable)",
    iconName: "pipeline",
  },
  {
    id: "contacts",
    label: "Clientes",
    shortLabel: "Clientes",
    description: "Instituciones compradoras, contactos e historial",
    iconName: "contacts",
  },
  {
    id: "prospectos",
    label: "Prospectos",
    shortLabel: "Prospectos",
    description: "Nuevas oportunidades de clientes (investigación DeepSearch)",
    iconName: "prospectos",
  },
  {
    id: "inbox",
    label: "Correos",
    shortLabel: "Correos",
    description: "Correspondencia entrante con filtros por rol",
    iconName: "inbox",
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
  {
    id: "deals",
    label: "Negocios",
    shortLabel: "Negocios",
    description: "Espejo de negocios comerciales",
    iconName: "deals",
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
];

/** The flat, ordered top-level nav — exactly the 8 primary sections, Cotizaciones-first. */
export const DASHBOARD_TOP_NAV_IDS: readonly DashboardSection[] = [
  "cotizaciones",
  "tenders",
  "pipeline",
  "contacts",
  "prospectos",
  "inbox",
  "catalogo",
  "system",
];

export const DASHBOARD_TOP_NAV_ITEMS: DashboardNavItem[] = DASHBOARD_TOP_NAV_IDS.map(
  (id) => DASHBOARD_NAV_ITEMS.find((item) => item.id === id)!,
);

/** Visually emphasized primary-work items, per the Phase 2 IA reset. */
export const DASHBOARD_EMPHASIZED_NAV_IDS: ReadonlySet<DashboardSection> = new Set([
  "cotizaciones",
  "tenders",
  "pipeline",
]);

export const DEFAULT_DASHBOARD_SECTION: DashboardSection = "cotizaciones";

export function dashboardSectionLabel(section: DashboardSection): string {
  return DASHBOARD_NAV_ITEMS.find((item) => item.id === section)?.label ?? section;
}
