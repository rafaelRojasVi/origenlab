/**
 * UI types for the V2 durable read boundary (`/v2/*`).
 *
 * These describe the **durable** V2 core, not a rebuildable mirror. The distinction is the
 * point of the migration: a card fed from here is reading human commercial truth, while the
 * `mirror*` and `leadIntel*` clients beside it read machine projections that may be dropped
 * and rebuilt at any time.
 */

export interface V2Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

/** A channel, and only a channel. `usage` says what is actually known about its owner. */
export type V2ContactUsage = "personal" | "work" | "shared_mailbox" | "unattributed";

/** `machine_proposed` is the review queue by another name. */
export type V2Confirmation = "machine_proposed" | "confirmed";

export interface V2Contact {
  contact_point_id: string;
  address: string;
  usage: V2ContactUsage;
  confirmation: V2Confirmation;
  person_id: string | null;
  person_display_name: string | null;
  organization_id: string | null;
  organization_name: string | null;
  created_at: string | null;
}

export interface V2Organization {
  organization_id: string;
  name: string;
  /** `unknown` until an operator classifies it; the closed vocabulary is still open. */
  kind: string;
  confirmation: V2Confirmation;
  parent_organization_id: string | null;
  contact_point_count: number;
  created_at: string | null;
}

export interface V2Opportunity {
  opportunity_id: string;
  title: string;
  stage: string;
  organization_id: string | null;
  organization_name: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface V2Task {
  task_id: string;
  title: string;
  due_at: string | null;
  overdue: boolean;
  opportunity_id: string | null;
  opportunity_title: string | null;
  organization_name: string | null;
}

export interface V2Quote {
  quote_id: string;
  quote_number: string | null;
  revision_no: number;
  status: string;
  sent_at: string | null;
  opportunity_id: string | null;
  organization_name: string | null;
  updated_at: string | null;
}

/**
 * The operator review queue, counted by why.
 *
 * `ambiguous_assertions` is what the migration stopped and asked about — a small,
 * actionable number. The `machine_proposed_*` counts are a much larger backlog of
 * transcribed observations awaiting confirmation. They are different decisions and are
 * deliberately not summed into one figure.
 */
export interface V2ReviewSummary {
  ambiguous_assertions: number;
  unresolved_assertions: number;
  machine_proposed_organizations: number;
  machine_proposed_contact_points: number;
  unattributed_contact_points: number;
}
