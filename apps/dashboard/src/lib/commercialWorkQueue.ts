import type {
  CommercialWorkQueueResponse,
  CommercialWorkQueueTask,
} from "../api/commercialOperationsTypes";


export type CommercialTaskDueBucket =
  | "overdue"
  | "today"
  | "upcoming"
  | "unscheduled";


export interface CommercialWorkQueueSummary {
  overdueTasks: CommercialWorkQueueTask[];
  todayTasks: CommercialWorkQueueTask[];
  upcomingTasks: CommercialWorkQueueTask[];
  unscheduledTasks: CommercialWorkQueueTask[];

  reviewCount: number;
  quoteFollowupCount: number;
}


export function commercialTaskDueBucket(
  dueAt: string | null,
  now = new Date(),
): CommercialTaskDueBucket {
  if (!dueAt) {
    return "unscheduled";
  }

  const due = new Date(dueAt);

  if (Number.isNaN(due.getTime())) {
    return "unscheduled";
  }

  const todayStart = new Date(
    now.getFullYear(),
    now.getMonth(),
    now.getDate(),
  );

  const tomorrowStart = new Date(
    now.getFullYear(),
    now.getMonth(),
    now.getDate() + 1,
  );

  if (due < todayStart) {
    return "overdue";
  }

  if (due < tomorrowStart) {
    return "today";
  }

  return "upcoming";
}


export function summarizeCommercialWorkQueue(
  queue: CommercialWorkQueueResponse,
  now = new Date(),
): CommercialWorkQueueSummary {
  const overdueTasks: CommercialWorkQueueTask[] = [];
  const todayTasks: CommercialWorkQueueTask[] = [];
  const upcomingTasks: CommercialWorkQueueTask[] = [];
  const unscheduledTasks: CommercialWorkQueueTask[] = [];

  for (const item of queue.open_tasks) {
    switch (
      commercialTaskDueBucket(
        item.task.due_at,
        now,
      )
    ) {
      case "overdue":
        overdueTasks.push(item);
        break;

      case "today":
        todayTasks.push(item);
        break;

      case "upcoming":
        upcomingTasks.push(item);
        break;

      case "unscheduled":
        unscheduledTasks.push(item);
        break;
    }
  }

  return {
    overdueTasks,
    todayTasks,
    upcomingTasks,
    unscheduledTasks,

    reviewCount:
      queue.review_opportunities.length,

    quoteFollowupCount:
      queue.quote_followups.length,
  };
}
