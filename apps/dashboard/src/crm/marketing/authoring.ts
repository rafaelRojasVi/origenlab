import type { AuthSessionState } from "../../api/authClient";
import { useAuthSession } from "../../context/AuthSessionContext";

/**
 * Which roles are offered any Marketing write: «Nueva campaña», «Editar», «Duplicar», «Nueva
 * versión», saving a draft, freezing an audience and planning a day. Every one of those commands
 * is `Deciding` on the API (`campaign_draft_routes.py`, `audience_freeze_routes.py`,
 * `campaign_planning_routes.py`), which refuses any other role — the API stays the authority.
 * Hiding the controls only spares a viewer an editor whose save would be refused.
 *
 * Fails closed: a viewer, an unknown role or a session that is not confirmed gets no write
 * affordance, and keeps every read-only view of a campaign.
 */
export const ROLES_THAT_AUTHOR_CAMPAIGNS: ReadonlySet<string> = new Set(["sales", "admin"]);

export function mayAuthorCampaigns(session: AuthSessionState): boolean {
  return session.kind === "signed_in" && ROLES_THAT_AUTHOR_CAMPAIGNS.has(session.operator.role);
}

export function useMayAuthorCampaigns(): boolean {
  return mayAuthorCampaigns(useAuthSession().session);
}

