import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  cancelCommercialTask,
  completeCommercialTask,
  createCommercialActivity,
  createCommercialTask,
  fetchSalesOpportunityActivities,
  fetchSalesOpportunityTasks,
} from "../../api/commercialOperationsClient";
import type { CommercialActivity, CommercialTask } from "../../api/commercialOperationsTypes";

function errorMessage(reason: unknown, fallback: string): string {
  return reason instanceof Error ? reason.message : fallback;
}

function newIdempotencyKey(kind: "activity" | "task"): string {
  const cryptoApi = globalThis.crypto;
  if (!cryptoApi) throw new Error("No se pudo generar una clave segura para la operación.");
  if (typeof cryptoApi.randomUUID === "function") return `${kind}:${cryptoApi.randomUUID()}`;
  const bytes = new Uint8Array(16);
  cryptoApi.getRandomValues(bytes);
  return `${kind}:${Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("")}`;
}

export function SalesOpportunityWorkPanel({ salesOpportunityId }: { salesOpportunityId: string }) {
  const [tasks, setTasks] = useState<CommercialTask[]>([]);
  const [activities, setActivities] = useState<CommercialActivity[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [taskTitle, setTaskTitle] = useState("");
  const [activitySummary, setActivitySummary] = useState("");
  const [transitioningTaskId, setTransitioningTaskId] = useState<string | null>(null);
  const pendingTaskKey = useRef<string | null>(null);
  const pendingActivityKey = useRef<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setLoadError(null);

    void Promise.all([
      fetchSalesOpportunityTasks(salesOpportunityId),
      fetchSalesOpportunityActivities(salesOpportunityId),
    ])
      .then(([taskResult, activityResult]) => {
        if (!active) return;
        setTasks(taskResult.items);
        setActivities(activityResult.items);
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setLoadError(errorMessage(reason, "No se pudo cargar el trabajo asociado."));
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [salesOpportunityId]);

  async function addTask(event: FormEvent) {
    event.preventDefault();
    const title = taskTitle.trim();
    if (!title) {
      setActionError("Escribe el seguimiento pendiente.");
      return;
    }

    setActionError(null);
    const key = pendingTaskKey.current ?? newIdempotencyKey("task");
    pendingTaskKey.current = key;

    try {
      const created = await createCommercialTask(
        { sales_opportunity_id: salesOpportunityId, title, priority: "normal" },
        key,
      );
      pendingTaskKey.current = null;
      setTasks((current) => [created, ...current]);
      setTaskTitle("");
    } catch (reason: unknown) {
      setActionError(errorMessage(reason, "No se pudo crear el seguimiento."));
    }
  }

  async function addActivity(event: FormEvent) {
    event.preventDefault();
    const summary = activitySummary.trim();
    if (!summary) {
      setActionError("Escribe un resumen de la actividad.");
      return;
    }

    setActionError(null);
    const key = pendingActivityKey.current ?? newIdempotencyKey("activity");
    pendingActivityKey.current = key;

    try {
      const created = await createCommercialActivity(
        {
          sales_opportunity_id: salesOpportunityId,
          activity_type: "note",
          occurred_at: new Date().toISOString(),
          summary,
        },
        key,
      );
      pendingActivityKey.current = null;
      setActivities((current) => [created, ...current]);
      setActivitySummary("");
    } catch (reason: unknown) {
      setActionError(errorMessage(reason, "No se pudo registrar la actividad."));
    }
  }

  async function transitionTask(task: CommercialTask, action: "complete" | "cancel") {
    setTransitioningTaskId(task.task_id);
    setActionError(null);

    try {
      const updated =
        action === "complete"
          ? await completeCommercialTask(task.task_id, { expected_version: task.version })
          : await cancelCommercialTask(task.task_id, { expected_version: task.version });

      setTasks((current) => current.map((item) => (item.task_id === updated.task_id ? updated : item)));
    } catch (reason: unknown) {
      setActionError(
        errorMessage(reason, action === "complete" ? "No se pudo completar el seguimiento." : "No se pudo cancelar el seguimiento."),
      );
    } finally {
      setTransitioningTaskId(null);
    }
  }

  if (loading) {
    return <p className="text-sm text-slate-600">Cargando trabajo asociado…</p>;
  }

  if (loadError) {
    return (
      <div role="alert" className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
        {loadError}
      </div>
    );
  }

  const openTasks = tasks.filter((task) => task.status === "open");

  return (
    <div className="space-y-5">
      {actionError ? (
        <div role="alert" className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
          {actionError}
        </div>
      ) : null}

      <section className="space-y-2">
        <h3 className="text-sm font-semibold text-slate-800">Seguimientos ({openTasks.length} pendientes)</h3>
        <form onSubmit={addTask} className="flex gap-2">
          <label className="sr-only" htmlFor="new-sales-task">
            Nuevo seguimiento
          </label>
          <input
            id="new-sales-task"
            aria-label="Nuevo seguimiento"
            value={taskTitle}
            onChange={(event) => setTaskTitle(event.target.value)}
            maxLength={500}
            className="flex-1 rounded-md border border-slate-300 px-2 py-1.5 text-sm"
          />
          <button type="submit" className="rounded-md border border-brand-300 bg-brand-50 px-3 py-1.5 text-sm font-medium text-brand-800">
            Agregar seguimiento
          </button>
        </form>
        <ul className="space-y-1">
          {tasks.map((task) => (
            <li key={task.task_id} className="flex items-center justify-between gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm">
              <span className={task.status === "open" ? "text-slate-900" : "text-slate-500 line-through"}>{task.title}</span>
              {task.status === "open" ? (
                <div className="flex shrink-0 gap-2">
                  <button
                    type="button"
                    disabled={transitioningTaskId === task.task_id}
                    onClick={() => void transitionTask(task, "complete")}
                    className="rounded-md border border-emerald-300 bg-emerald-50 px-2 py-1 text-xs font-medium text-emerald-800 disabled:opacity-50"
                  >
                    Completar
                  </button>
                  <button
                    type="button"
                    disabled={transitioningTaskId === task.task_id}
                    onClick={() => void transitionTask(task, "cancel")}
                    className="rounded-md border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 disabled:opacity-50"
                  >
                    Cancelar
                  </button>
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold text-slate-800">Actividad ({activities.length})</h3>
        <form onSubmit={addActivity} className="flex gap-2">
          <label className="sr-only" htmlFor="new-sales-activity">
            Resumen de actividad
          </label>
          <input
            id="new-sales-activity"
            aria-label="Resumen de actividad"
            value={activitySummary}
            onChange={(event) => setActivitySummary(event.target.value)}
            maxLength={500}
            className="flex-1 rounded-md border border-slate-300 px-2 py-1.5 text-sm"
          />
          <button type="submit" className="rounded-md border border-brand-300 bg-brand-50 px-3 py-1.5 text-sm font-medium text-brand-800">
            Registrar actividad
          </button>
        </form>
        <ul className="space-y-1">
          {activities.map((activity) => (
            <li key={activity.activity_id} className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-800">
              {activity.summary}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
