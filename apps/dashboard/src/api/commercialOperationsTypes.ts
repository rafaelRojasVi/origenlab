export type CommercialConfirmationStatus =
  | "confirmed"
  | "rejected"
  | "needs_review";

export type CommercialActivityType =
  | "call"
  | "whatsapp"
  | "meeting"
  | "email"
  | "note"
  | "quote"
  | "follow_up"
  | "other";

export type CommercialTaskPriority =
  | "low"
  | "normal"
  | "high"
  | "urgent";

export type CommercialTaskStatus =
  | "open"
  | "done"
  | "cancelled";

export interface CommercialOperatorState {
  opportunity_id: string;
  confirmation_status: CommercialConfirmationStatus;
  manual_stage: string | null;
  owner_key: string | null;
  version: number;
  created_by: string;
  updated_by: string;
  created_at: string;
  updated_at: string;
}

export interface CommercialActivity {
  activity_id: string;
  opportunity_id: string | null;
  account_id: string | null;
  contact_id: string | null;
  activity_type: CommercialActivityType;
  occurred_at: string;
  summary: string;
  detail: string | null;
  created_by: string;
  created_at: string;
}

export interface CommercialTask {
  task_id: string;
  opportunity_id: string | null;
  account_id: string | null;
  contact_id: string | null;
  title: string;
  status: CommercialTaskStatus;
  priority: CommercialTaskPriority;
  due_at: string | null;
  owner_key: string | null;
  version: number;
  created_by: string;
  updated_by: string;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface CommercialOperatorStateReadResponse {
  state: CommercialOperatorState | null;
}

export interface CommercialActivityListResponse {
  items: CommercialActivity[];
}

export interface CommercialTaskListResponse {
  items: CommercialTask[];
}

export interface SetCommercialOpportunityStateCommand {
  confirmation_status: CommercialConfirmationStatus;
  manual_stage?: string | null;
  owner_key?: string | null;
  expected_version: number;
}

export interface CreateCommercialActivityCommand {
  sales_opportunity_id?: string | null;
  opportunity_id?: string | null;
  account_id?: string | null;
  contact_id?: string | null;
  activity_type: CommercialActivityType;
  occurred_at: string;
  summary: string;
  detail?: string | null;
}

export interface CreateCommercialTaskCommand {
  sales_opportunity_id?: string | null;
  opportunity_id?: string | null;
  account_id?: string | null;
  contact_id?: string | null;
  title: string;
  priority?: CommercialTaskPriority;
  due_at?: string | null;
  owner_key?: string | null;
}

export interface CommercialTaskTransitionCommand {
  expected_version: number;
}


export interface CommercialWorkQueueTask {
  task: CommercialTask;

  contact_display_email: string | null;
  account_display_domain: string | null;

  canonical_stage: string | null;
  machine_review_status: string | null;
}


export interface CommercialWorkQueueOpportunity {
  opportunity_id: string;

  contact_display_email: string | null;
  account_display_domain: string | null;

  canonical_stage: string;
  machine_review_status: string;

  confirmation_status:
    | CommercialConfirmationStatus
    | null;

  manual_stage: string | null;
  owner_key: string | null;
  operator_state_version: number | null;
}


export interface CommercialWorkQueueResponse {
  open_tasks: CommercialWorkQueueTask[];

  review_opportunities:
    CommercialWorkQueueOpportunity[];

  quote_followups:
    CommercialWorkQueueOpportunity[];
}

export type SalesOpportunityStage =
  | "new"
  | "qualifying"
  | "qualified"
  | "quoting"
  | "negotiating"
  | "won"
  | "lost"
  | "dormant";

export interface SalesOpportunity {
  sales_opportunity_id: string;

  source_kind: "pr3" | "manual";
  source_opportunity_id: string;

  account_id: string | null;
  primary_contact_id: string | null;
  organization_id: string | null;
  primary_crm_contact_id: string | null;

  title: string;
  stage: SalesOpportunityStage;

  owner_key: string;

  version: number;

  created_by: string;
  updated_by: string;

  created_at: string;
  updated_at: string;
}

export interface SalesOpportunityListItem extends SalesOpportunity {
  stage_updated_at: string;

  contact_display_email: string | null;
  account_display_domain: string | null;

  organization_display_name: string | null;
  contact_display_name: string | null;
  contact_primary_email: string | null;

  open_task_count: number;
  next_task_id: string | null;
  next_task_title: string | null;
  next_task_due_at: string | null;
}

export interface SalesOpportunitiesMeta {
  data_source: "postgres";
  read_only: true;
  count: number;
  total_count: number;
  limit: number;
  offset: number;
}

export interface SalesOpportunitiesResponse {
  meta: SalesOpportunitiesMeta;
  items: SalesOpportunityListItem[];
}

export interface SalesOpportunityReadResponse {
  meta: { data_source: "postgres"; read_only: true };
  item: SalesOpportunity;
}

export interface PromoteSalesOpportunityCommand {
  source_opportunity_id: string;
  title: string;
  owner_key?: string | null;
}

export interface ManualSalesOpportunityCreateCommand {
  title: string;
  owner_key?: string;
  organization_id?: string;
  organization_display_name?: string;
  contact_id?: string;
  contact_display_name?: string;
  contact_email?: string;
}

export interface TransitionSalesOpportunityStageCommand {
  stage: SalesOpportunityStage;
  expected_version: number;
}
