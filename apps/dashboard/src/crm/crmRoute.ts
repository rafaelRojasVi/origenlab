import { useCallback, useEffect, useState } from "react";

/** The CRM workspace lives under `#/crm/<section>[/<id>]`, beside the V1 operator panel. */
export type CrmSection =
  | "resumen"
  | "oportunidades"
  | "organizaciones"
  | "personas"
  | "proveedores"
  | "drive"
  | "marketing"
  | "revision";

export interface CrmNavItem {
  id: CrmSection;
  label: string;
  group: "comercial" | "archivo" | "control";
}

export const CRM_NAV: CrmNavItem[] = [
  { id: "resumen", label: "Resumen", group: "comercial" },
  { id: "oportunidades", label: "Oportunidades", group: "comercial" },
  { id: "organizaciones", label: "Organizaciones", group: "comercial" },
  { id: "personas", label: "Personas", group: "comercial" },
  { id: "proveedores", label: "Proveedores", group: "comercial" },
  { id: "drive", label: "Archivo Drive", group: "archivo" },
  { id: "marketing", label: "Marketing", group: "archivo" },
  { id: "revision", label: "Revisión", group: "control" },
];

export const CRM_GROUP_LABEL: Record<CrmNavItem["group"], string> = {
  comercial: "Comercial",
  archivo: "Archivo y difusión",
  control: "Control",
};

const SECTIONS = new Set<string>(CRM_NAV.map((n) => n.id));
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isCrmHash(hash: string): boolean {
  return /^#\/crm(\/|$)/.test(hash);
}

export interface CrmRoute {
  section: CrmSection;
  id: string | null;
}

export function parseCrmHash(hash: string): CrmRoute {
  const parts = hash.replace(/^#\/crm\/?/, "").split("/").filter(Boolean);
  const section = (SECTIONS.has(parts[0] ?? "") ? parts[0] : "resumen") as CrmSection;
  const id = parts[1] && UUID.test(parts[1]) ? parts[1].toLowerCase() : null;
  return { section, id };
}

export function crmHash(section: CrmSection, id?: string | null): string {
  return `#/crm/${section}${id ? `/${id}` : ""}`;
}

export function useCrmRoute(): [CrmRoute, (section: CrmSection, id?: string | null) => void] {
  const [route, setRoute] = useState<CrmRoute>(() => parseCrmHash(window.location.hash));
  useEffect(() => {
    const onHash = () => setRoute(parseCrmHash(window.location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const navigate = useCallback((section: CrmSection, id?: string | null) => {
    window.location.hash = crmHash(section, id);
    window.scrollTo?.({ top: 0 });
  }, []);
  return [route, navigate];
}

export function useIsCrmHash(): boolean {
  const [crm, setCrm] = useState(() => typeof window !== "undefined" && isCrmHash(window.location.hash));
  useEffect(() => {
    const onHash = () => setCrm(isCrmHash(window.location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  return crm;
}
