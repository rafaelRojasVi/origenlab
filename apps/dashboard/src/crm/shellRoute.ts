import { useEffect, useState } from "react";
import { crmHash, isCrmHash, parseCrmHash, type CrmSection } from "./crmRoute";

/**
 * One dashboard, one navigation, one sign-in: the CRM under `#/crm/*`. The bare root opens
 * Resumen. Bookmarks from the earlier operator panel are redirected to the CRM section that
 * now covers the same ground, and to Resumen when nothing does; the old screens are gone.
 */
export const SHELL_HOME_HASH = "#/crm/resumen";

export interface ShellRoute {
  section: CrmSection;
  id: string | null;
}

/** Earlier-panel hash (without `#/` and query) → the CRM section that replaced it. */
export const LEGACY_REDIRECTS: Record<string, CrmSection> = {
  today: "resumen",
  inbox: "resumen",
  pipeline: "oportunidades",
  ventas: "oportunidades",
  deals: "oportunidades",
  cotizaciones: "oportunidades",
  casos: "oportunidades",
  prospectos: "organizaciones",
  instituciones: "organizaciones",
  contacts: "personas",
  contactos: "personas",
  suppliers: "proveedores",
  archivo: "drive",
  revision: "revision",
  importacion: "revision",
  "crm-v2": "resumen",
  catalogo: "catalogo",
  tenders: "resumen",
  "payments-logistics": "resumen",
  system: "resumen",
};

export function parseShellHash(hash: string): ShellRoute {
  return isCrmHash(hash) ? parseCrmHash(hash) : { section: "resumen", id: null };
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

/** The hash a non-CRM address should be replaced with, or null when it is already a CRM route. */
export function redirectHash(hash: string): string | null {
  if (isCrmHash(hash)) return null;
  const [path, query] = hash.replace(/^#\/?/, "").split("?");
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
