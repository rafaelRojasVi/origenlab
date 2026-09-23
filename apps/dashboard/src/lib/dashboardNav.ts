/** Secciones principales del panel operador (Dashboard V2 — IA Cotizaciones-first). */

/*
 * `crm-v2` sits beside `contacts`, not in place of it. `contacts` reads the V1 lead-intel
 * mirror — a rebuildable projection — and is what operators use today; `crm-v2` reads the
 * durable V2 core, which holds different rows. Replacing one with the other before the V1
 * durable migration lands would silently drop data an operator is relying on.
 *
 * `revision` sits beside it for the same reason and under the same rule: it is the human
 * review queue over `crm-v2`'s evidence card, still read-only, and reached from Inicio.
 *
 * `casos` is the third of them: the commercial case that follows a reviewed document. Its
 * six commands exist in `apps/api` and none of them is reachable through the proxy, so it
 * is a reading surface like the other two and belongs in the registry rather than the
 * sidebar until that changes.
 *
 * None of the three is in `DASHBOARD_TOP_NAV_IDS`. The Cotizaciones-first sidebar is an
 * accepted IA decision, and a technical surface whose cards are still read-only has not
 * earned a slot in it. Like `today`, `deals`, `suppliers` and `payments-logistics`, each
 * lives in the registry — so a deep link renders with a correct page title — and is
 * reached from the review card on Inicio.
 *
 * `contactos` and `instituciones` are the exception, and they are in the sidebar by an
 * owner decision of 2026-09-23. They are not another console over the same rows: they are
 * the Contacto 360 / Institución 360 surfaces, which are where an operator starts rather
 * than where a developer checks a table. They sit **beside** `contacts` ("Clientes") and do
 * not replace it: `contacts` reads the V1 lead-intel mirror and still carries rows the V2
 * durable core will not hold until the V1 durable migration lands, so retiring it now would
 * silently drop data the operator relies on.
 */

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
  | "contactos"
  | "instituciones"
  | "crm-v2"
  | "revision"
  | "casos"
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
  | "crm"
  | "system";

export interface DashboardNavItem {
  id: DashboardSection;
  label: string;
  shortLabel: string;
  description: string;
  iconName: DashboardNavIconName;
}

/**
 * Full section registry, used for id -> label lookups so deep-linked hidden
 * sections (today/deals/suppliers/payments-logistics/crm-v2/revision/casos) still get
 * a correct page title. Sidebar rendering uses `DASHBOARD_TOP_NAV_ITEMS`
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
    id: "contactos",
    label: "Contactos",
    shortLabel: "Contactos",
    description:
      "Personas y canales del núcleo durable V2: cómo contactarles, en qué casos están y qué se les puede enviar",
    iconName: "contacts",
  },
  {
    id: "instituciones",
    label: "Instituciones",
    shortLabel: "Instit.",
    description:
      "Instituciones del núcleo durable V2: sus canales, sus casos, sus cotizaciones y su papel comercial",
    iconName: "crm",
  },
  {
    id: "crm-v2",
    label: "CRM V2",
    shortLabel: "CRM V2",
    description: "Contactos, organizaciones, prospectos y evidencia del núcleo durable V2",
    iconName: "crm",
  },
  {
    id: "revision",
    label: "Revisión de evidencia",
    shortLabel: "Revisión",
    description:
      "Cola humana sobre la evidencia pendiente: qué afirma cada correo y qué falta decidir",
    iconName: "crm",
  },
  {
    id: "casos",
    label: "Casos comerciales",
    shortLabel: "Casos",
    description:
      "Quién pide, qué busca y por qué lo cree: el caso comercial del núcleo durable V2",
    iconName: "deals",
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

/**
 * The flat, ordered top-level nav: Cotizaciones-first, with the two V2 360 surfaces beside
 * «Clientes». Ten items since 2026-09-23; it was eight before Contacto 360 and
 * Institución 360 were promoted into it.
 */
export const DASHBOARD_TOP_NAV_IDS: readonly DashboardSection[] = [
  "cotizaciones",
  "tenders",
  "pipeline",
  "contacts",
  "contactos",
  "instituciones",
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

/**
 * The V2 surfaces, which read the durable core and write nothing.
 *
 * The shell's status chrome — the operator verdict ("Estado: BLOQUEADO") and the mirror
 * backend ("SQLite local") — is about the **V1** read path: the daily core run and which
 * store the operator mirror is being served from. None of it describes these three pages,
 * which read PostgreSQL through `/v2` and never touch that mirror. Showing it above them
 * told an operator their screen was blocked when nothing about it was, so these sections
 * get a header that says what is actually true of them instead.
 *
 * `crm-v2` and `revision` are the technical consoles over the same rows and are reached
 * from Inicio; they are listed here for the same reason.
 */
export const V2_READ_ONLY_SECTIONS: ReadonlySet<DashboardSection> = new Set([
  "contactos",
  "instituciones",
  "casos",
  "crm-v2",
  "revision",
]);

export function isV2ReadOnlySection(section: DashboardSection): boolean {
  return V2_READ_ONLY_SECTIONS.has(section);
}

export const DEFAULT_DASHBOARD_SECTION: DashboardSection = "cotizaciones";

export function dashboardSectionLabel(section: DashboardSection): string {
  return DASHBOARD_NAV_ITEMS.find((item) => item.id === section)?.label ?? section;
}
