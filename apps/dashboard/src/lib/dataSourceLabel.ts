import type { ApiBackend } from "../api/operatorTypes";
import type { ContactDataSource } from "../api/contactTypes";

export function operatorBackendSourceLabel(backend: ApiBackend): string {
  if (backend === "postgres") {
    return "Espejo Postgres (solo lectura) — no define envíos ni contactos";
  }
  return "SQLite operativo en vivo (vía API)";
}

export function warmCasesSourceLabel(
  backend: ApiBackend,
  metaSource: "sqlite" | "postgres_mirror",
): string {
  if (backend === "postgres" || metaSource === "postgres_mirror") {
    return "Espejo Postgres — solo vistas previas, no autoriza envíos";
  }
  return "SQLite en vivo vía API";
}

export function contactDataSourceLabel(
  backend: ApiBackend,
  metaSource: ContactDataSource,
): string {
  if (backend === "postgres" || metaSource === "postgres_mirror") {
    return "Contacto desde espejo Postgres — no autoriza envíos";
  }
  return "Contacto desde SQLite vía API";
}
