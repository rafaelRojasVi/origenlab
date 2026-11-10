import { CRM_NAV, type AreaKey, type CrmSection } from "./crmRoute";

export interface AreaTab {
  label: string;
  /** The pages of the tab, first one first. Two pages → a second row of links (Organizaciones · Personas). */
  sections: CrmSection[];
  /** Shown only to those who may quote (sales and admin). */
  quoting?: boolean;
  /** Shown only to an admin. */
  adminOnly?: boolean;
}

export interface Area {
  key: AreaKey;
  label: string;
  tabs: AreaTab[];
}

/**
 * The five sections of the side nav and the tabs inside each (canvas `Main.dc.html`). «Hoy» has no
 * tabs in phase 1; Finanzas has only «Por cobrar» until its other tabs are built.
 */
export const AREAS: Area[] = [
  { key: "hoy", label: "Hoy", tabs: [] },
  {
    key: "ventas",
    label: "Ventas",
    tabs: [
      { label: "Pipeline", sections: ["oportunidades"] },
      { label: "Cotizaciones", sections: ["cotizador"], quoting: true },
      { label: "Clientes", sections: ["organizaciones", "personas"] },
      { label: "Listas y llamadas", sections: ["prospectos", "marketing"] },
      { label: "Licitaciones", sections: ["licitaciones"] },
    ],
  },
  {
    key: "finanzas",
    label: "Finanzas",
    tabs: [
      { label: "Resumen", sections: ["fin-resumen"] },
      { label: "Cobrar", sections: ["fin-cobrar"] },
      { label: "Pagar", sections: ["fin-pagar"] },
    ],
  },
  {
    key: "catalogo",
    label: "Catálogo",
    tabs: [
      { label: "Productos", sections: ["catalogo"] },
      { label: "Catálogo web", sections: ["catalogo-web"], adminOnly: true },
    ],
  },
  {
    key: "ajustes",
    label: "Ajustes",
    tabs: [
      { label: "Operaciones", sections: ["datos"] },
      { label: "Historial", sections: ["historial"] },
      { label: "Meta y socios", sections: ["ajustes-meta"], adminOnly: true },
    ],
  },
];

export interface NavAccess {
  admin: boolean;
  mayQuote: boolean;
}

function allowed(item: { adminOnly?: boolean; quoting?: boolean }, access: NavAccess): boolean {
  return (!item.adminOnly || access.admin) && (!item.quoting || access.mayQuote);
}

/** The tabs of an area this operator sees, each with only the pages they may open. */
export function visibleTabs(area: Area, access: NavAccess): AreaTab[] {
  return area.tabs
    .filter((t) => allowed(t, access))
    .map((t) => ({ ...t, sections: t.sections.filter((s) => allowed(CRM_NAV.find((n) => n.id === s) ?? {}, access)) }))
    .filter((t) => t.sections.length > 0);
}
