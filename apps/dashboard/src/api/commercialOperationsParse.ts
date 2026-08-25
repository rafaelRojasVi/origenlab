import type {
  CommercialActivity,
  CommercialActivityListResponse,
  CommercialActivityType,
  CommercialConfirmationStatus,
  CommercialOperatorState,
  CommercialOperatorStateReadResponse,
  CommercialTask,
  CommercialTaskListResponse,
  CommercialTaskPriority,
  CommercialTaskStatus,
  CommercialWorkQueueOpportunity,
  CommercialWorkQueueResponse,
  CommercialWorkQueueTask,
} from "./commercialOperationsTypes";

function record(
  raw: unknown,
  label: string,
): Record<string, unknown> {
  if (
    !raw ||
    typeof raw !== "object" ||
    Array.isArray(raw)
  ) {
    throw new Error(`${label} must be an object`);
  }

  return raw as Record<string, unknown>;
}

function stringValue(
  value: unknown,
  label: string,
): string {
  if (typeof value !== "string") {
    throw new Error(`${label} must be a string`);
  }

  return value;
}

function nullableString(
  value: unknown,
  label: string,
): string | null {
  if (value === null) {
    return null;
  }

  return stringValue(value, label);
}

function positiveInteger(
  value: unknown,
  label: string,
): number {
  if (
    typeof value !== "number" ||
    !Number.isInteger(value) ||
    value < 1
  ) {
    throw new Error(
      `${label} must be a positive integer`,
    );
  }

  return value;
}

function enumValue<T extends string>(
  value: unknown,
  allowed: readonly T[],
  label: string,
): T {
  if (
    typeof value !== "string" ||
    !allowed.includes(value as T)
  ) {
    throw new Error(
      `${label} has an unsupported value`,
    );
  }

  return value as T;
}

const CONFIRMATION_STATUSES = [
  "confirmed",
  "rejected",
  "needs_review",
] as const;

const ACTIVITY_TYPES = [
  "call",
  "whatsapp",
  "meeting",
  "email",
  "note",
  "quote",
  "follow_up",
  "other",
] as const;

const TASK_STATUSES = [
  "open",
  "done",
  "cancelled",
] as const;

const TASK_PRIORITIES = [
  "low",
  "normal",
  "high",
  "urgent",
] as const;

export function parseCommercialOperatorState(
  raw: unknown,
): CommercialOperatorState {
  const row = record(
    raw,
    "operator state",
  );

  return {
    opportunity_id: stringValue(
      row.opportunity_id,
      "opportunity_id",
    ),
    confirmation_status:
      enumValue<CommercialConfirmationStatus>(
        row.confirmation_status,
        CONFIRMATION_STATUSES,
        "confirmation_status",
      ),
    manual_stage: nullableString(
      row.manual_stage,
      "manual_stage",
    ),
    owner_key: nullableString(
      row.owner_key,
      "owner_key",
    ),
    version: positiveInteger(
      row.version,
      "version",
    ),
    created_by: stringValue(
      row.created_by,
      "created_by",
    ),
    updated_by: stringValue(
      row.updated_by,
      "updated_by",
    ),
    created_at: stringValue(
      row.created_at,
      "created_at",
    ),
    updated_at: stringValue(
      row.updated_at,
      "updated_at",
    ),
  };
}

export function parseCommercialActivity(
  raw: unknown,
): CommercialActivity {
  const row = record(
    raw,
    "activity",
  );

  return {
    activity_id: stringValue(
      row.activity_id,
      "activity_id",
    ),
    opportunity_id: nullableString(
      row.opportunity_id,
      "opportunity_id",
    ),
    account_id: nullableString(
      row.account_id,
      "account_id",
    ),
    contact_id: nullableString(
      row.contact_id,
      "contact_id",
    ),
    activity_type:
      enumValue<CommercialActivityType>(
        row.activity_type,
        ACTIVITY_TYPES,
        "activity_type",
      ),
    occurred_at: stringValue(
      row.occurred_at,
      "occurred_at",
    ),
    summary: stringValue(
      row.summary,
      "summary",
    ),
    detail: nullableString(
      row.detail,
      "detail",
    ),
    created_by: stringValue(
      row.created_by,
      "created_by",
    ),
    created_at: stringValue(
      row.created_at,
      "created_at",
    ),
  };
}

export function parseCommercialTask(
  raw: unknown,
): CommercialTask {
  const row = record(
    raw,
    "task",
  );

  return {
    task_id: stringValue(
      row.task_id,
      "task_id",
    ),
    opportunity_id: nullableString(
      row.opportunity_id,
      "opportunity_id",
    ),
    account_id: nullableString(
      row.account_id,
      "account_id",
    ),
    contact_id: nullableString(
      row.contact_id,
      "contact_id",
    ),
    title: stringValue(
      row.title,
      "title",
    ),
    status: enumValue<CommercialTaskStatus>(
      row.status,
      TASK_STATUSES,
      "status",
    ),
    priority:
      enumValue<CommercialTaskPriority>(
        row.priority,
        TASK_PRIORITIES,
        "priority",
      ),
    due_at: nullableString(
      row.due_at,
      "due_at",
    ),
    owner_key: nullableString(
      row.owner_key,
      "owner_key",
    ),
    version: positiveInteger(
      row.version,
      "version",
    ),
    created_by: stringValue(
      row.created_by,
      "created_by",
    ),
    updated_by: stringValue(
      row.updated_by,
      "updated_by",
    ),
    completed_at: nullableString(
      row.completed_at,
      "completed_at",
    ),
    created_at: stringValue(
      row.created_at,
      "created_at",
    ),
    updated_at: stringValue(
      row.updated_at,
      "updated_at",
    ),
  };
}

export function parseCommercialOperatorStateReadResponse(
  raw: unknown,
): CommercialOperatorStateReadResponse {
  const row = record(
    raw,
    "operator state response",
  );

  return {
    state:
      row.state === null
        ? null
        : parseCommercialOperatorState(
            row.state,
          ),
  };
}

export function parseCommercialActivityListResponse(
  raw: unknown,
): CommercialActivityListResponse {
  const row = record(
    raw,
    "activity list",
  );

  if (!Array.isArray(row.items)) {
    throw new Error(
      "activity list items must be an array",
    );
  }

  return {
    items: row.items.map(
      parseCommercialActivity,
    ),
  };
}

export function parseCommercialTaskListResponse(
  raw: unknown,
): CommercialTaskListResponse {
  const row = record(
    raw,
    "task list",
  );

  if (!Array.isArray(row.items)) {
    throw new Error(
      "task list items must be an array",
    );
  }

  return {
    items: row.items.map(
      parseCommercialTask,
    ),
  };
}


export function parseCommercialWorkQueueTask(
  raw: unknown,
): CommercialWorkQueueTask {
  const row = record(
    raw,
    "commercial work queue task",
  );

  return {
    task: parseCommercialTask(row.task),

    contact_display_email: nullableString(
      row.contact_display_email,
      "contact_display_email",
    ),

    account_display_domain: nullableString(
      row.account_display_domain,
      "account_display_domain",
    ),

    canonical_stage: nullableString(
      row.canonical_stage,
      "canonical_stage",
    ),

    machine_review_status: nullableString(
      row.machine_review_status,
      "machine_review_status",
    ),
  };
}


export function parseCommercialWorkQueueOpportunity(
  raw: unknown,
): CommercialWorkQueueOpportunity {
  const row = record(
    raw,
    "commercial work queue opportunity",
  );

  return {
    opportunity_id: stringValue(
      row.opportunity_id,
      "opportunity_id",
    ),

    contact_display_email: nullableString(
      row.contact_display_email,
      "contact_display_email",
    ),

    account_display_domain: nullableString(
      row.account_display_domain,
      "account_display_domain",
    ),

    canonical_stage: stringValue(
      row.canonical_stage,
      "canonical_stage",
    ),

    machine_review_status: stringValue(
      row.machine_review_status,
      "machine_review_status",
    ),

    confirmation_status:
      row.confirmation_status === null
        ? null
        : enumValue<CommercialConfirmationStatus>(
            row.confirmation_status,
            CONFIRMATION_STATUSES,
            "confirmation_status",
          ),

    manual_stage: nullableString(
      row.manual_stage,
      "manual_stage",
    ),

    owner_key: nullableString(
      row.owner_key,
      "owner_key",
    ),

    operator_state_version:
      row.operator_state_version === null
        ? null
        : positiveInteger(
            row.operator_state_version,
            "operator_state_version",
          ),
  };
}


export function parseCommercialWorkQueueResponse(
  raw: unknown,
): CommercialWorkQueueResponse {
  const row = record(
    raw,
    "commercial work queue response",
  );

  if (!Array.isArray(row.open_tasks)) {
    throw new Error(
      "open_tasks must be an array",
    );
  }

  if (!Array.isArray(row.review_opportunities)) {
    throw new Error(
      "review_opportunities must be an array",
    );
  }

  if (!Array.isArray(row.quote_followups)) {
    throw new Error(
      "quote_followups must be an array",
    );
  }

  return {
    open_tasks: row.open_tasks.map(
      parseCommercialWorkQueueTask,
    ),

    review_opportunities:
      row.review_opportunities.map(
        parseCommercialWorkQueueOpportunity,
      ),

    quote_followups:
      row.quote_followups.map(
        parseCommercialWorkQueueOpportunity,
      ),
  };
}
