import type { AuthSessionState } from "../../api/authClient";
import { useAuthSession } from "../../context/AuthSessionContext";

/**
 * Which roles are offered any CRM write: «Nuevo contacto», «Nueva organización», «Agregar nota»,
 * and all freeform authoring commands. Every one of those commands is `Deciding` on the API
 * (`crm_authoring_routes.py`), which refuses any other role — the API stays the authority.
 * Hiding the controls only spares a viewer an editor whose save would be refused.
 *
 * Admin-only: archive-person, restore-person, merge-people, archive-organization,
 * restore-organization. The UI enforces `isAdmin(session)` before showing those.
 *
 * Fails closed: a viewer, an unknown role or a session that is not confirmed gets no write
 * affordance, and keeps every read-only view.
 */
export const ROLES_THAT_AUTHOR_CRM: ReadonlySet<string> = new Set(["sales", "admin"]);

export function mayAuthorCrm(session: AuthSessionState): boolean {
  return session.kind === "signed_in" && ROLES_THAT_AUTHOR_CRM.has(session.operator.role);
}

export function isAdmin(session: AuthSessionState): boolean {
  return session.kind === "signed_in" && session.operator.role === "admin";
}

export function useMayAuthorCrm(): boolean {
  return mayAuthorCrm(useAuthSession().session);
}

export function useIsAdmin(): boolean {
  return isAdmin(useAuthSession().session);
}
