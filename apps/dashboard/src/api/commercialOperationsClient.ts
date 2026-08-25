/**
 * Narrow client for durable commercial operations.
 *
 * This is the only general dashboard client allowed to issue CRM POST
 * commands. The browser never supplies X-OriginLab-Operator-Email; the
 * production Worker reconstructs operator identity from Cloudflare Access.
 */

import {
  OperatorApiError,
  operatorApiUrl,
} from "./operatorClient";

import {
  parseCommercialActivity,
  parseCommercialActivityListResponse,
  parseCommercialOperatorState,
  parseCommercialOperatorStateReadResponse,
  parseCommercialTask,
  parseCommercialTaskListResponse,
  parseCommercialWorkQueueResponse,
} from "./commercialOperationsParse";

import type {
  CommercialActivity,
  CommercialActivityListResponse,
  CommercialOperatorState,
  CommercialOperatorStateReadResponse,
  CommercialTask,
  CommercialTaskListResponse,
  CommercialTaskTransitionCommand,
  CreateCommercialActivityCommand,
  CreateCommercialTaskCommand,
  SetCommercialOpportunityStateCommand,
  CommercialWorkQueueResponse,
} from "./commercialOperationsTypes";


const COMMERCIAL_OPPORTUNITY_ID_RE =
  /^o_[0-9a-f]{32}$/;

const COMMERCIAL_TASK_ID_RE =
  /^task_[0-9a-f]{32}$/;

const IDEMPOTENCY_KEY_RE =
  /^[A-Za-z0-9._:-]{1,200}$/;


function requireCommercialOpportunityId(
  opportunityId: string,
): string {
  const normalized = opportunityId.trim();

  if (!COMMERCIAL_OPPORTUNITY_ID_RE.test(normalized)) {
    throw new OperatorApiError(
      "Invalid commercial opportunity ID",
      422,
    );
  }

  return normalized;
}


function requireCommercialTaskId(
  taskId: string,
): string {
  const normalized = taskId.trim();

  if (!COMMERCIAL_TASK_ID_RE.test(normalized)) {
    throw new OperatorApiError(
      "Invalid commercial task ID",
      422,
    );
  }

  return normalized;
}


function requireIdempotencyKey(
  idempotencyKey: string,
): string {
  const normalized = idempotencyKey.trim();

  if (!IDEMPOTENCY_KEY_RE.test(normalized)) {
    throw new OperatorApiError(
      "Invalid idempotency key",
      422,
    );
  }

  return normalized;
}


async function fetchJsonGet<T>(
  url: string,
): Promise<T> {
  const response = await fetch(url, {
    method: "GET",
    credentials: "include",
    headers: {
      Accept: "application/json",
    },
  });

  if (!response.ok) {
    const text = await response
      .text()
      .catch(() => "");

    throw new OperatorApiError(
      text ||
        response.statusText ||
        `HTTP ${response.status}`,
      response.status,
    );
  }

  return response.json() as Promise<T>;
}


async function fetchJsonPost<T>(
  url: string,
  body: unknown,
  idempotencyKey?: string,
): Promise<T> {
  const headers = new Headers({
    Accept: "application/json",
    "Content-Type": "application/json",
  });

  if (idempotencyKey !== undefined) {
    headers.set(
      "Idempotency-Key",
      requireIdempotencyKey(idempotencyKey),
    );
  }

  const response = await fetch(url, {
    method: "POST",
    credentials: "include",
    headers,
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    const text = await response
      .text()
      .catch(() => "");

    throw new OperatorApiError(
      text ||
        response.statusText ||
        `HTTP ${response.status}`,
      response.status,
    );
  }

  return response.json() as Promise<T>;
}


export function commercialOpportunityOperatorStatePath(
  opportunityId: string,
): string {
  return `/operations/opportunities/${requireCommercialOpportunityId(
    opportunityId,
  )}/state`;
}


export function commercialOpportunityActivitiesPath(
  opportunityId: string,
): string {
  return `/operations/opportunities/${requireCommercialOpportunityId(
    opportunityId,
  )}/activities`;
}


export function commercialOpportunityTasksPath(
  opportunityId: string,
): string {
  return `/operations/opportunities/${requireCommercialOpportunityId(
    opportunityId,
  )}/tasks`;
}


export function commercialTaskTransitionPath(
  taskId: string,
  action: "complete" | "cancel",
): string {
  return `/operations/tasks/${requireCommercialTaskId(
    taskId,
  )}/${action}`;
}


export function fetchCommercialOpportunityOperatorState(
  opportunityId: string,
): Promise<CommercialOperatorStateReadResponse> {
  return fetchJsonGet<unknown>(
    operatorApiUrl(
      commercialOpportunityOperatorStatePath(
        opportunityId,
      ),
    ),
  ).then(
    parseCommercialOperatorStateReadResponse,
  );
}


export function fetchCommercialOpportunityActivities(
  opportunityId: string,
): Promise<CommercialActivityListResponse> {
  return fetchJsonGet<unknown>(
    operatorApiUrl(
      commercialOpportunityActivitiesPath(
        opportunityId,
      ),
    ),
  ).then(
    parseCommercialActivityListResponse,
  );
}


export function fetchCommercialOpportunityTasks(
  opportunityId: string,
): Promise<CommercialTaskListResponse> {
  return fetchJsonGet<unknown>(
    operatorApiUrl(
      commercialOpportunityTasksPath(
        opportunityId,
      ),
    ),
  ).then(
    parseCommercialTaskListResponse,
  );
}


export function setCommercialOpportunityOperatorState(
  opportunityId: string,
  command: SetCommercialOpportunityStateCommand,
): Promise<CommercialOperatorState> {
  return fetchJsonPost<unknown>(
    operatorApiUrl(
      commercialOpportunityOperatorStatePath(
        opportunityId,
      ),
    ),
    command,
  ).then(parseCommercialOperatorState);
}


export function createCommercialActivity(
  command: CreateCommercialActivityCommand,
  idempotencyKey: string,
): Promise<CommercialActivity> {
  return fetchJsonPost<unknown>(
    operatorApiUrl("/operations/activities"),
    command,
    idempotencyKey,
  ).then(parseCommercialActivity);
}


export function createCommercialTask(
  command: CreateCommercialTaskCommand,
  idempotencyKey: string,
): Promise<CommercialTask> {
  return fetchJsonPost<unknown>(
    operatorApiUrl("/operations/tasks"),
    command,
    idempotencyKey,
  ).then(parseCommercialTask);
}


export function completeCommercialTask(
  taskId: string,
  command: CommercialTaskTransitionCommand,
): Promise<CommercialTask> {
  return fetchJsonPost<unknown>(
    operatorApiUrl(
      commercialTaskTransitionPath(
        taskId,
        "complete",
      ),
    ),
    command,
  ).then(parseCommercialTask);
}


export function cancelCommercialTask(
  taskId: string,
  command: CommercialTaskTransitionCommand,
): Promise<CommercialTask> {
  return fetchJsonPost<unknown>(
    operatorApiUrl(
      commercialTaskTransitionPath(
        taskId,
        "cancel",
      ),
    ),
    command,
  ).then(parseCommercialTask);
}


export function fetchCommercialWorkQueue(
  limit = 100,
): Promise<CommercialWorkQueueResponse> {
  if (
    !Number.isInteger(limit) ||
    limit < 1 ||
    limit > 200
  ) {
    throw new OperatorApiError(
      "Commercial work queue limit must be between 1 and 200",
      422,
    );
  }

  return fetchJsonGet<unknown>(
    operatorApiUrl(
      "/operations/work-queue",
      {
        limit,
      },
    ),
  ).then(parseCommercialWorkQueueResponse);
}

