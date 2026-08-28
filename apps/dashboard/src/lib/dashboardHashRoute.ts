import { useCallback, useEffect, useState } from "react";
import { DEFAULT_DASHBOARD_SECTION, type DashboardSection } from "./dashboardNav";

const VALID_SECTIONS = new Set<string>([
  "today",
  "inbox",
  "deals",
  "prospectos",
  "catalogo",
  "suppliers",
  "tenders",
  "payments-logistics",
  "contacts",
  "system",
]);

export function parseDashboardSectionFromHash(hash: string): DashboardSection {
  const raw = hash.replace(/^#\/?/, "").trim().toLowerCase();
  if (!raw || raw === "/") {
    return DEFAULT_DASHBOARD_SECTION;
  }
  const section = raw.split("?")[0];
  if (VALID_SECTIONS.has(section)) {
    return section as DashboardSection;
  }
  return DEFAULT_DASHBOARD_SECTION;
}

export function dashboardSectionToHash(section: DashboardSection): string {
  return section === DEFAULT_DASHBOARD_SECTION ? "#/" : `#/${section}`;
}

export function useDashboardSection(): [DashboardSection, (section: DashboardSection) => void] {
  const read = useCallback(
    () => parseDashboardSectionFromHash(typeof window !== "undefined" ? window.location.hash : ""),
    [],
  );

  const [section, setSectionState] = useState<DashboardSection>(read);

  useEffect(() => {
    const onHashChange = () => setSectionState(read());
    window.addEventListener("hashchange", onHashChange);
    if (!window.location.hash) {
      window.location.replace(dashboardSectionToHash(DEFAULT_DASHBOARD_SECTION));
    }
    return () => window.removeEventListener("hashchange", onHashChange);
  }, [read]);

  const navigate = useCallback((next: DashboardSection) => {
    const hash = dashboardSectionToHash(next);
    if (window.location.hash !== hash) {
      window.location.hash = hash;
    }
    setSectionState(next);
  }, []);

  return [section, navigate];
}
