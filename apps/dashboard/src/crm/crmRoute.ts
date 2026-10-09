/** The CRM lives under `#/crm/<section>[/<id>]`; `shellRoute.ts` owns the hash listener. */
export type CrmSection =
  | "resumen"
  | "oportunidades"
  | "organizaciones"
  | "personas"
  | "catalogo"
  | "marketing"
  | "historial"
  | "datos";

/** The tabs of «Datos» (admin only): what was technical in Revisión, plus suppliers and the Drive archive. */
export type DatosTab = "bloqueos" | "no_importadas" | "evidencia" | "estado" | "acciones" | "proveedores" | "drive";
const DATOS_TABS = new Set<string>(["bloqueos", "no_importadas", "evidencia", "estado", "acciones", "proveedores", "drive"]);

export interface CrmNavItem {
  id: CrmSection;
  label: string;
  group: "comercial" | "control";
  /** Shown only to an admin; anyone else who opens it sees «Sólo administración». */
  adminOnly?: boolean;
}

export const CRM_NAV: CrmNavItem[] = [
  { id: "resumen", label: "Hoy", group: "comercial" },
  { id: "oportunidades", label: "Oportunidades", group: "comercial" },
  { id: "organizaciones", label: "Organizaciones", group: "comercial" },
  { id: "personas", label: "Personas", group: "comercial" },
  { id: "catalogo", label: "Catálogo", group: "comercial" },
  { id: "marketing", label: "Marketing", group: "comercial" },
  { id: "historial", label: "Historial", group: "control" },
  { id: "datos", label: "Datos", group: "control", adminOnly: true },
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

const SECTIONS = new Set<string>(CRM_NAV.map((n) => n.id));
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isCrmHash(hash: string): boolean {
  return /^#\/crm(\/|$)/.test(hash);
}

export interface CrmRoute {
  section: CrmSection;
  id: string | null;
  /** Only for «Datos» reached through an old Proveedores / Archivo Drive link. */
  tab?: DatosTab;
}

export function parseCrmHash(hash: string): CrmRoute {
  const parts = hash.replace(/^#\/crm\/?/, "").split("/").filter(Boolean);
  const moved = MOVED[parts[0] ?? ""];
  if (moved) return moved.tab ? { section: moved.section, id: null, tab: moved.tab } : { section: moved.section, id: null };
  const section = (SECTIONS.has(parts[0] ?? "") ? parts[0] : "resumen") as CrmSection;
  if (section === "datos" && DATOS_TABS.has(parts[1] ?? "")) return { section, id: null, tab: parts[1] as DatosTab };
  const id = parts[1] && UUID.test(parts[1]) ? parts[1].toLowerCase() : null;
  return { section, id };
}

/** `#/crm/<section>[/<id>]`; an old segment («proveedores», «drive») is accepted and resolved on read. */
export function crmHash(section: CrmSection | "proveedores" | "drive", id?: string | null, tab?: DatosTab): string {
  if (section === "datos" && tab) return `#/crm/datos/${tab}`;
  return `#/crm/${section}${id ? `/${id}` : ""}`;
}
