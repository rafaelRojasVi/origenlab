import { ActionableOpportunitiesTable } from "../components/tenders/ActionableOpportunitiesTable";

/**
 * Public procurement / tender queue.
 *
 * The W1-backed "Oportunidades accionables" table is the ONLY procurement-
 * opportunity content on this page — the single authoritative source for
 * confirmed-actionable current opportunities, already gated on fail-closed
 * eligibility/lifecycle/catalog/line-evidence rules.
 *
 * The legacy equipment_first feed (EquipmentOpportunitiesTable, GET
 * /opportunities/equipment) predates procurement-eligibility work and can
 * still label a restricted-eligibility tender `next_action=quote_now`. A
 * surrounding warning does not neutralize that row-level signal, so it is
 * deliberately NOT rendered here at all (not even demoted) — the legacy
 * pipeline, API, and client remain intact for possible future discovery/
 * review use, just not on this page.
 */
export function TendersPage() {
  return <ActionableOpportunitiesTable />;
}
