/** The CRM lives under five areas, `#/<area>/<page>[/<id>]` (`SECTION_PATH`); `shellRoute.ts` owns the hash listener. */
export type CrmSection =
  | "resumen"
  | "oportunidades"
  | "organizaciones"
  | "personas"
  | "catalogo"
  | "catalogo-web"
  | "marketing"
  | "historial"
  | "datos"
  | "cotizador"
  | "licitaciones"
  | "prospectos"
  | "pagos"
  | "fin-mes"
  | "fin-por-pagar"
  | "fin-gastos"
  | "fin-financiamiento"
  | "fin-socios";

/** The tabs of «Datos» (admin only): what was technical in Revisión, plus suppliers and the Drive archive.
 *  «decidir» is the «Por ordenar» section linked from «Hoy». */
export type DatosTab = "bloqueos" | "no_importadas" | "evidencia" | "estado" | "acciones" | "proveedores" | "drive" | "decidir" | "meta";
const DATOS_TABS = new Set<string>(["bloqueos", "no_importadas", "evidencia", "estado", "acciones", "proveedores", "drive", "decidir", "meta"]);

/** Every page, in the order of the five areas (`nav.ts` groups them into tabs). */
export interface CrmNavItem {
  id: CrmSection;
  /** The page's title: its tab, or its link inside a two-page tab («Organizaciones», «Personas»). */
  label: string;
  /** Shown only to an admin; anyone else who opens it sees «Sólo administración». */
  adminOnly?: boolean;
  /** Shown only to those who may quote (sales and admin); anyone else who opens it is told so. */
  quoting?: boolean;
}

export const CRM_NAV: CrmNavItem[] = [
  { id: "resumen", label: "Hoy" },
  { id: "oportunidades", label: "Pipeline" },
  { id: "cotizador", label: "Cotizaciones", quoting: true },
  { id: "organizaciones", label: "Organizaciones" },
  { id: "personas", label: "Personas" },
  { id: "prospectos", label: "Prospectos" },
  { id: "marketing", label: "Campañas" },
  { id: "licitaciones", label: "Licitaciones" },
  { id: "fin-mes", label: "Mes" },
  { id: "pagos", label: "Por cobrar" },
  { id: "fin-por-pagar", label: "Por pagar y pedidos" },
  { id: "fin-gastos", label: "Gastos" },
  { id: "fin-financiamiento", label: "Financiamiento" },
  { id: "fin-socios", label: "Socios", adminOnly: true },
  { id: "catalogo", label: "Productos" },
  { id: "catalogo-web", label: "Catálogo web", adminOnly: true },
  { id: "datos", label: "Operaciones" },
  { id: "historial", label: "Historial" },
];

/** Old `#/crm/<segment>` names that moved: an old link still lands where its content lives now. */
const MOVED: Record<string, { section: CrmSection; tab?: DatosTab }> = {
  revision: { section: "historial" },
  proveedores: { section: "datos", tab: "proveedores" },
  drive: { section: "datos", tab: "drive" },
};

/** The five areas of the dashboard: the first segment of every hash. */
export type AreaKey = "hoy" | "ventas" | "finanzas" | "catalogo" | "ajustes";

/** Where each page lives: `#/<SECTION_PATH>[/<id>|/<tab>|/<pane>]`. The section ids stay the page's identity. */
export const SECTION_PATH: Record<CrmSection, string> = {
  resumen: "hoy",
  oportunidades: "ventas/pipeline",
  cotizador: "ventas/cotizaciones",
  organizaciones: "ventas/clientes",
  personas: "ventas/clientes/personas",
  prospectos: "ventas/listas",
  marketing: "ventas/listas/campanas",
  licitaciones: "ventas/licitaciones",
  "fin-mes": "finanzas/mes",
  pagos: "finanzas/por-cobrar",
  "fin-por-pagar": "finanzas/por-pagar",
  "fin-gastos": "finanzas/gastos",
  "fin-financiamiento": "finanzas/financiamiento",
  "fin-socios": "finanzas/socios",
  catalogo: "catalogo",
  "catalogo-web": "catalogo/web",
  datos: "ajustes/operaciones",
  historial: "ajustes/historial",
};

/** The page an area's bare root (`#/ventas`) opens. */
export const AREA_HOME: Record<AreaKey, CrmSection> = {
  hoy: "resumen",
  ventas: "oportunidades",
  finanzas: "fin-mes",
  catalogo: "catalogo",
  ajustes: "datos",
};

export function areaOf(section: CrmSection): AreaKey {
  return SECTION_PATH[section].split("/")[0] as AreaKey;
}

/** Every path a page answers to, longest first: its own, plus the explicit first tab of Clientes and Listas. */
const FIRST_TAB_PATHS: [string[], CrmSection][] = [
  [["ventas", "clientes", "organizaciones"], "organizaciones"],
  [["ventas", "listas", "prospectos"], "prospectos"],
];
const PATHS: [string[], CrmSection][] = [
  ...(Object.entries(SECTION_PATH) as [CrmSection, string][]).map(([section, path]): [string[], CrmSection] => [path.split("/"), section]),
  ...FIRST_TAB_PATHS,
].sort((a, b) => b[0].length - a[0].length);

/** The page title of a section. */
export function sectionTitle(section: CrmSection): string {
  return CRM_NAV.find((n) => n.id === section)?.label ?? "OrigenLab";
}

const SECTIONS = new Set<string>(CRM_NAV.map((n) => n.id));
/** A model sheet of the public site is named by its slug (the schema's `^[a-z0-9-]+$`, at most 60 characters as the proxy allows). */
const WEB_MODEL_ID = /^[a-z0-9-]{1,60}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
/** «Licitaciones» also opens a Mercado Público notice's page: `#/ventas/licitaciones/mp-<notice id>`. */
const NOTICE_ROUTE_ID = /^mp-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const noticeRouteId = (noticeId: string): string => `mp-${noticeId}`;
export const noticeIdOfRoute = (id: string | null | undefined): string | null =>
  id && NOTICE_ROUTE_ID.test(id) ? id.slice(3).toLowerCase() : null;

/** A hash in one of the five areas (`#/hoy`, `#/ventas/…`, `#/finanzas/…`, `#/catalogo…`, `#/ajustes/…`). */
export function isShellHash(hash: string): boolean {
  return /^#\/(hoy|ventas|finanzas|catalogo|ajustes)(\/|\?|$)/.test(hash);
}

/**
 * An old `#/crm/<section>[/<id>][?query]` address (and `#/crm/cotizar/<id>`, `#/crm/proveedores`,
 * `#/crm/drive`, `#/crm/revision`) → the new hash of the same page, id, tab or pane, query kept. Else null.
 */
export function legacyCrmTarget(hash: string): string | null {
  if (!/^#\/crm(\/|\?|$)/.test(hash)) return null;
  const [path, query] = splitQuery(hash);
  const parts = path.replace(/^#\/crm\/?/, "").split("/").filter(Boolean);
  if (parts[0] === "cotizar") parts[0] = "cotizador";
  const moved = MOVED[parts[0] ?? ""];
  const route: CrmRoute = moved
    ? { section: moved.section, id: null, ...(moved.tab ? { tab: moved.tab } : {}) }
    : routeOf((SECTIONS.has(parts[0] ?? "") ? parts[0] : "resumen") as CrmSection, parts.slice(1));
  return `${crmHash(route.section, route.pane ?? route.id, route.tab)}${query !== undefined ? `?${query}` : ""}`;
}

/** `#/a/b?x` → [`#/a/b`, `x`]; no `?` → query undefined. */
function splitQuery(hash: string): [string, string | undefined] {
  const at = hash.indexOf("?");
  return at < 0 ? [hash, undefined] : [hash.slice(0, at), hash.slice(at + 1)];
}

/** The Cotizador's fixed pane «Preferencias»: `#/ventas/cotizaciones/preferencias`, not a quote tab. */
export const COTIZADOR_PREFERENCES = "preferencias";
/** The Cotizador's fixed pane «Rentabilidad»: `#/ventas/cotizaciones/rentabilidad`, not a quote tab. */
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

/** The page a section opens with what follows its path: a «Datos» tab, a Cotizador pane, or an id. */
function routeOf(section: CrmSection, rest: string[]): CrmRoute {
  const next = rest[0] ?? "";
  if (section === "datos" && DATOS_TABS.has(next)) return { section, id: null, tab: next as DatosTab };
  if (section === "cotizador" && COTIZADOR_PANES.has(next)) return { section, id: null, pane: next as CotizadorPane };
  // «Catálogo web» names a model of the public site by its slug: `#/catalogo/web/up400st`.
  if (section === "catalogo-web") return { section, id: next && WEB_MODEL_ID.test(next) ? next : null };
  const id = next && (UUID.test(next) || (section === "licitaciones" && NOTICE_ROUTE_ID.test(next))) ? next.toLowerCase() : null;
  return { section, id };
}

/** The section whose path the hash starts with, and the segments after it; null when no page has that path. */
export function matchSectionPath(hash: string): { section: CrmSection; rest: string[] } | null {
  if (!isShellHash(hash)) return null;
  const parts = splitQuery(hash)[0].replace(/^#\//, "").split("/").filter(Boolean);
  for (const [path, section] of PATHS) {
    if (path.every((seg, i) => parts[i] === seg)) return { section, rest: parts.slice(path.length) };
  }
  return null;
}

/** Reads a five-area hash; anything else (an old `#/crm/…` included, see `legacyCrmTarget`) is «Hoy». */
export function parseCrmHash(hash: string): CrmRoute {
  // A page's own `?query` (the catalog's supplier and search) is not part of the route.
  const match = matchSectionPath(hash);
  return match ? routeOf(match.section, match.rest) : { section: "resumen", id: null };
}

/** `#/<area>/<page>[/<id>]` (the Cotizador's fixed panes, `COTIZADOR_PREFERENCES` and `COTIZADOR_RENTABILIDAD`, go in the id's place); an old segment («proveedores», «drive») is accepted and becomes that «Datos» tab. */
export function crmHash(section: CrmSection | "proveedores" | "drive", id?: string | null, tab?: DatosTab): string {
  if (section === "proveedores" || section === "drive") return `#/${SECTION_PATH.datos}/${section}`;
  if (section === "datos" && tab) return `#/${SECTION_PATH.datos}/${tab}`;
  return `#/${SECTION_PATH[section]}${id ? `/${id}` : ""}`;
}
