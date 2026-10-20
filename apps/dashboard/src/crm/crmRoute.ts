/** The CRM lives under `#/crm/<section>[/<id>]`; `shellRoute.ts` owns the hash listener. */
export type CrmSection =
  | "resumen"
  | "oportunidades"
  | "organizaciones"
  | "personas"
  | "catalogo"
  | "marketing"
  | "historial"
  | "datos"
  | "cotizador"
  | "licitaciones";

/** The tabs of «Datos» (admin only): what was technical in Revisión, plus suppliers and the Drive archive.
 *  «decidir» is the «Por ordenar» section linked from «Hoy». */
export type DatosTab = "bloqueos" | "no_importadas" | "evidencia" | "estado" | "acciones" | "proveedores" | "drive" | "decidir";
const DATOS_TABS = new Set<string>(["bloqueos", "no_importadas", "evidencia", "estado", "acciones", "proveedores", "drive", "decidir"]);

export interface CrmNavItem {
  id: CrmSection;
  label: string;
  group: "comercial" | "control";
  /** Shown only to an admin; anyone else who opens it sees «Sólo administración». */
  adminOnly?: boolean;
  /** Shown only to those who may quote (sales and admin); anyone else who opens it is told so. */
  quoting?: boolean;
}

export const CRM_NAV: CrmNavItem[] = [
  { id: "resumen", label: "Hoy", group: "comercial" },
  { id: "oportunidades", label: "Oportunidades", group: "comercial" },
  { id: "cotizador", label: "Cotizador", group: "comercial", quoting: true },
  { id: "licitaciones", label: "Licitaciones", group: "comercial" },
  { id: "organizaciones", label: "Organizaciones", group: "comercial" },
  { id: "personas", label: "Personas", group: "comercial" },
  { id: "catalogo", label: "Catálogo", group: "comercial" },
  { id: "marketing", label: "Marketing", group: "comercial" },
  { id: "historial", label: "Historial", group: "control" },
  { id: "datos", label: "Operaciones", group: "control" },
];

export const CRM_GROUP_LABEL: Record<CrmNavItem["group"], string> = {
  comercial: "Comercial",
  control: "Control",
};

/** Sections that moved: an old link still lands where its content lives now. */
const MOVED: Record<string, { section: CrmSection; tab?: DatosTab }> = {
  revision: { section: "historial" },
  proveedores: { section: "datos", tab: "proveedores" },
  drive: { section: "datos", tab: "drive" },
};

/** The page title of a section. */
export function sectionTitle(section: CrmSection): string {
  return CRM_NAV.find((n) => n.id === section)?.label ?? "OrigenLab";
}

const SECTIONS = new Set<string>(CRM_NAV.map((n) => n.id));
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Old CRM addresses that keep their record: the one-quote page `#/crm/cotizar/<id>` is now that
 * quote's tab in the module. Returns the address to replace it with, or null.
 */
export function movedCrmHash(hash: string): string | null {
  const m = /^#\/crm\/cotizar(?:\/([^/?]*))?(?:[/?].*)?$/.exec(hash);
  if (!m) return null;
  return crmHash("cotizador", m[1] && UUID.test(m[1]) ? m[1].toLowerCase() : null);
}

export function isCrmHash(hash: string): boolean {
  return /^#\/crm(\/|$)/.test(hash);
}

/** The Cotizador's fixed pane «Preferencias»: `#/crm/cotizador/preferencias`, not a quote tab. */
export const COTIZADOR_PREFERENCES = "preferencias";
/** The Cotizador's fixed pane «Rentabilidad»: `#/crm/cotizador/rentabilidad`, not a quote tab. */
export const COTIZADOR_RENTABILIDAD = "rentabilidad";
export type CotizadorPane = typeof COTIZADOR_PREFERENCES | typeof COTIZADOR_RENTABILIDAD;
const COTIZADOR_PANES: ReadonlySet<string> = new Set([COTIZADOR_PREFERENCES, COTIZADOR_RENTABILIDAD]);

export interface CrmRoute {
  section: CrmSection;
  id: string | null;
  /** Only for «Datos» reached through an old Proveedores / Archivo Drive link. */
  tab?: DatosTab;
  /** Only for the Cotizador: a fixed pane instead of the list or a quote. */
  pane?: CotizadorPane;
}

export function parseCrmHash(hash: string): CrmRoute {
  // A page's own `?query` (the catalog's supplier and search) is not part of the route.
  const parts = hash.split("?")[0].replace(/^#\/crm\/?/, "").split("/").filter(Boolean);
  if (parts[0] === "cotizar") parts[0] = "cotizador";
  const moved = MOVED[parts[0] ?? ""];
  if (moved) return moved.tab ? { section: moved.section, id: null, tab: moved.tab } : { section: moved.section, id: null };
  const section = (SECTIONS.has(parts[0] ?? "") ? parts[0] : "resumen") as CrmSection;
  if (section === "datos" && DATOS_TABS.has(parts[1] ?? "")) return { section, id: null, tab: parts[1] as DatosTab };
  if (section === "cotizador" && COTIZADOR_PANES.has(parts[1] ?? "")) return { section, id: null, pane: parts[1] as CotizadorPane };
  const id = parts[1] && UUID.test(parts[1]) ? parts[1].toLowerCase() : null;
  return { section, id };
}

/** `#/crm/<section>[/<id>]` (the Cotizador's fixed panes, `COTIZADOR_PREFERENCES` and `COTIZADOR_RENTABILIDAD`, go in the id's place); an old segment («proveedores», «drive») is accepted and resolved on read. */
export function crmHash(section: CrmSection | "proveedores" | "drive", id?: string | null, tab?: DatosTab): string {
  if (section === "datos" && tab) return `#/crm/datos/${tab}`;
  return `#/crm/${section}${id ? `/${id}` : ""}`;
}
