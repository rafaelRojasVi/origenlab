import { useEffect, useState } from "react";
import { AREA_HOME, crmHash, isShellHash, legacyCrmTarget, matchSectionPath, parseCrmHash, type AreaKey, type CotizadorPane, type CrmSection, type DatosTab } from "./crmRoute";

/**
 * One dashboard, one navigation, one sign-in: five areas, `#/hoy`, `#/ventas/…`, `#/finanzas/…`,
 * `#/catalogo…` and `#/ajustes/…`. The bare root opens Hoy. Old `#/crm/…` links, and bookmarks
 * from the earlier operator panel, are redirected to the page that now covers the same ground
 * (keeping the record and the query), and to Hoy when nothing does.
 */
export const SHELL_HOME_HASH = "#/hoy";

export interface ShellRoute {
  section: CrmSection;
  id: string | null;
  /** «Datos» opened through an old Proveedores / Archivo Drive link. */
  tab?: DatosTab;
  /** The Cotizador's fixed pane «Preferencias». */
  pane?: CotizadorPane;
}

/** Earlier-panel paths (without `#/` and query) → the page they now open («proveedores» and «drive» are «Datos» tabs).
 *  «ventas» and «catalogo» are absent: they are area routes now, and open the same page as before. */
export const LEGACY_REDIRECTS: Record<string, CrmSection | "proveedores" | "drive"> = {
  today: "resumen",
  inbox: "resumen",
  pipeline: "oportunidades",
  deals: "oportunidades",
  cotizaciones: "oportunidades",
  casos: "oportunidades",
  prospectos: "organizaciones",
  instituciones: "organizaciones",
  contacts: "personas",
  contactos: "personas",
  suppliers: "proveedores",
  archivo: "drive",
  revision: "historial",
  importacion: "historial",
  "crm-v2": "resumen",
  tenders: "resumen",
  "payments-logistics": "resumen",
  system: "resumen",
};

/** The page an address shows; an address about to be redirected already reads as its target. */
export function parseShellHash(hash: string): ShellRoute {
  return parseCrmHash(redirectHash(hash) ?? hash);
}

const CASE_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * The case an earlier-panel bookmark selected (`?opportunity=` or `?id=`), when it is a V2
 * case UUID. V1 `sales_…` ids name no V2 case, so they open the list instead of a guess.
 */
function legacyCaseId(query: string | undefined): string | null {
  if (!query) return null;
  const params = new URLSearchParams(query);
  const value = (params.get("opportunity") ?? params.get("id") ?? "").trim();
  return CASE_ID.test(value) ? value.toLowerCase() : null;
}

/**
 * The hash an address should be replaced with, or null when it is already final. A new hash is
 * final unless it is a bare area root (→ that area's first page) or a path no page has (→ Hoy);
 * an old `#/crm/…` → `legacyCrmTarget`; anything else → `LEGACY_REDIRECTS`. Every target is final.
 */
export function redirectHash(hash: string): string | null {
  const [path, query] = hash.replace(/^#\/?/, "").split("?");
  if (isShellHash(hash)) {
    const parts = path.split("/").filter(Boolean);
    if (parts.length === 1) {
      const home = AREA_HOME[parts[0] as AreaKey];
      // «Hoy» and «Catálogo» are pages at their area root (the catalog keeps its `?query` there).
      if (crmHash(home) === `#/${parts[0]}`) return null;
      // An earlier-panel `#/ventas?opportunity=<uuid>` keeps its case.
      return crmHash(home, home === "oportunidades" ? legacyCaseId(query) : null);
    }
    return matchSectionPath(hash) ? null : SHELL_HOME_HASH;
  }
  const legacy = legacyCrmTarget(hash);
  if (legacy) return legacy;
  const section = LEGACY_REDIRECTS[path.trim().toLowerCase()] ?? "resumen";
  // Only Oportunidades opens a single record; other sections land on their list.
  return crmHash(section, section === "oportunidades" ? legacyCaseId(query) : null);
}

export function useShellRoute(): ShellRoute {
  const read = () => (typeof window === "undefined" ? "" : window.location.hash);
  const [route, setRoute] = useState<ShellRoute>(() => parseShellHash(read()));
  useEffect(() => {
    const sync = () => {
      const target = redirectHash(read());
      if (target) {
        window.location.replace(target);
        return;
      }
      setRoute(parseShellHash(read()));
    };
    sync();
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);
  return route;
}
