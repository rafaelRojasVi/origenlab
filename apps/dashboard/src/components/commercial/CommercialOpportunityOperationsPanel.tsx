import {
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";

import {
  cancelCommercialTask,
  completeCommercialTask,
  createCommercialActivity,
  createCommercialTask,
  fetchCommercialOpportunityActivities,
  fetchCommercialOpportunityOperatorState,
  fetchCommercialOpportunityTasks,
  setCommercialOpportunityOperatorState,
} from "../../api/commercialOperationsClient";

import type {
  CommercialActivity,
  CommercialActivityType,
  CommercialConfirmationStatus,
  CommercialOperatorState,
  CommercialTask,
  CommercialTaskPriority,
  CreateCommercialActivityCommand,
  CreateCommercialTaskCommand,
} from "../../api/commercialOperationsTypes";


const CONFIRMATION_OPTIONS: Array<{
  value: CommercialConfirmationStatus;
  label: string;
}> = [
  {
    value: "confirmed",
    label: "Confirmada",
  },
  {
    value: "needs_review",
    label: "Requiere revisión",
  },
  {
    value: "rejected",
    label: "Rechazada",
  },
];


const ACTIVITY_OPTIONS: Array<{
  value: CommercialActivityType;
  label: string;
}> = [
  { value: "call", label: "Llamada" },
  { value: "whatsapp", label: "WhatsApp" },
  { value: "meeting", label: "Reunión" },
  { value: "email", label: "Correo" },
  { value: "note", label: "Nota" },
  { value: "quote", label: "Cotización" },
  { value: "follow_up", label: "Seguimiento" },
  { value: "other", label: "Otro" },
];


const PRIORITY_OPTIONS: Array<{
  value: CommercialTaskPriority;
  label: string;
}> = [
  { value: "low", label: "Baja" },
  { value: "normal", label: "Normal" },
  { value: "high", label: "Alta" },
  { value: "urgent", label: "Urgente" },
];


function errorMessage(
  reason: unknown,
  fallback: string,
): string {
  return reason instanceof Error
    ? reason.message
    : fallback;
}


function dateLabel(
  value: string | null,
): string {
  if (!value) {
    return "Sin fecha";
  }

  const parsed = new Date(value);

  if (Number.isNaN(parsed.getTime())) {
    return value;
  }

  return parsed.toLocaleString("es-CL");
}


function confirmationLabel(
  value: CommercialConfirmationStatus,
): string {
  return (
    CONFIRMATION_OPTIONS.find(
      (option) => option.value === value,
    )?.label ?? value
  );
}


function activityLabel(
  value: CommercialActivityType,
): string {
  return (
    ACTIVITY_OPTIONS.find(
      (option) => option.value === value,
    )?.label ?? value
  );
}


function priorityLabel(
  value: CommercialTaskPriority,
): string {
  return (
    PRIORITY_OPTIONS.find(
      (option) => option.value === value,
    )?.label ?? value
  );
}


type PendingCreate<T> = {
  signature: string;
  idempotencyKey: string;
  command: T;
};


function newCommercialIdempotencyKey(
  kind: "activity" | "task",
): string {
  const cryptoApi = globalThis.crypto;

  if (!cryptoApi) {
    throw new Error(
      "No se pudo generar una clave segura para la operación.",
    );
  }

  if (
    typeof cryptoApi.randomUUID ===
    "function"
  ) {
    return `${kind}:${cryptoApi.randomUUID()}`;
  }

  const bytes = new Uint8Array(16);
  cryptoApi.getRandomValues(bytes);

  const token = Array.from(
    bytes,
    (value) =>
      value
        .toString(16)
        .padStart(2, "0"),
  ).join("");

  return `${kind}:${token}`;
}


export function CommercialOpportunityOperationsPanel({
  opportunityId,
}: {
  opportunityId: string;
}) {
  const [operatorState, setOperatorState] =
    useState<CommercialOperatorState | null>(null);

  const [activities, setActivities] =
    useState<CommercialActivity[]>([]);

  const [tasks, setTasks] =
    useState<CommercialTask[]>([]);

  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] =
    useState<string | null>(null);

  const [actionError, setActionError] =
    useState<string | null>(null);

  const [savingState, setSavingState] =
    useState(false);

  const [savingActivity, setSavingActivity] =
    useState(false);

  const [savingTask, setSavingTask] =
    useState(false);

  const [transitioningTaskId, setTransitioningTaskId] =
    useState<string | null>(null);

  const [
    confirmationStatus,
    setConfirmationStatus,
  ] = useState<CommercialConfirmationStatus>(
    "needs_review",
  );

  const [manualStage, setManualStage] =
    useState("");

  const [activityType, setActivityType] =
    useState<CommercialActivityType>("note");

  const [activitySummary, setActivitySummary] =
    useState("");

  const [activityDetail, setActivityDetail] =
    useState("");

  const [taskTitle, setTaskTitle] =
    useState("");

  const [taskPriority, setTaskPriority] =
    useState<CommercialTaskPriority>("normal");

  const [taskDueAt, setTaskDueAt] =
    useState("");

  const pendingActivityCreate =
    useRef<
      PendingCreate<CreateCommercialActivityCommand> | null
    >(null);

  const pendingTaskCreate =
    useRef<
      PendingCreate<CreateCommercialTaskCommand> | null
    >(null);


  useEffect(() => {
    let active = true;

    setLoading(true);
    setLoadError(null);
    setActionError(null);

    void (async () => {
      try {
        const [
          stateResult,
          activityResult,
          taskResult,
        ] = await Promise.all([
          fetchCommercialOpportunityOperatorState(
            opportunityId,
          ),
          fetchCommercialOpportunityActivities(
            opportunityId,
          ),
          fetchCommercialOpportunityTasks(
            opportunityId,
          ),
        ]);

        if (!active) {
          return;
        }

        setOperatorState(stateResult.state);
        setActivities(activityResult.items);
        setTasks(taskResult.items);

        if (stateResult.state) {
          setConfirmationStatus(
            stateResult.state.confirmation_status,
          );

          setManualStage(
            stateResult.state.manual_stage ?? "",
          );
        } else {
          setConfirmationStatus("needs_review");
          setManualStage("");
        }
      } catch (reason: unknown) {
        if (!active) {
          return;
        }

        setLoadError(
          errorMessage(
            reason,
            "No se pudo cargar el estado operativo.",
          ),
        );
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    })();

    return () => {
      active = false;
    };
  }, [opportunityId]);


  async function saveOperatorState(
    event: FormEvent,
  ) {
    event.preventDefault();

    setSavingState(true);
    setActionError(null);

    try {
      const result =
        await setCommercialOpportunityOperatorState(
          opportunityId,
          {
            confirmation_status:
              confirmationStatus,
            manual_stage:
              manualStage.trim() || null,
            owner_key:
              operatorState?.owner_key ?? null,
            expected_version:
              operatorState?.version ?? 0,
          },
        );

      setOperatorState(result);
      setConfirmationStatus(
        result.confirmation_status,
      );
      setManualStage(
        result.manual_stage ?? "",
      );
    } catch (reason: unknown) {
      setActionError(
        errorMessage(
          reason,
          "No se pudo guardar el estado.",
        ),
      );
    } finally {
      setSavingState(false);
    }
  }


  async function saveActivity(
    event: FormEvent,
  ) {
    event.preventDefault();

    const summary =
      activitySummary.trim();

    if (!summary) {
      setActionError(
        "Escribe un resumen de la actividad.",
      );
      return;
    }

    const detail =
      activityDetail.trim() || null;

    const signature = JSON.stringify({
      opportunity_id: opportunityId,
      activity_type: activityType,
      summary,
      detail,
    });

    setSavingActivity(true);
    setActionError(null);

    try {
      let pending =
        pendingActivityCreate.current;

      if (
        pending === null ||
        pending.signature !== signature
      ) {
        pending = {
          signature,
          idempotencyKey:
            newCommercialIdempotencyKey(
              "activity",
            ),
          command: {
            opportunity_id: opportunityId,
            activity_type: activityType,
            occurred_at:
              new Date().toISOString(),
            summary,
            detail,
          },
        };

        pendingActivityCreate.current =
          pending;
      }

      const created =
        await createCommercialActivity(
          pending.command,
          pending.idempotencyKey,
        );

      // Only clear after the server confirms success.
      // A network failure leaves the same command/key
      // available for a safe retry.
      pendingActivityCreate.current = null;

      setActivities((current) => [
        created,
        ...current,
      ]);

      setActivitySummary("");
      setActivityDetail("");
    } catch (reason: unknown) {
      setActionError(
        errorMessage(
          reason,
          "No se pudo registrar la actividad.",
        ),
      );
    } finally {
      setSavingActivity(false);
    }
  }


  async function saveTask(
    event: FormEvent,
  ) {
    event.preventDefault();

    const title = taskTitle.trim();

    if (!title) {
      setActionError(
        "Escribe el seguimiento pendiente.",
      );
      return;
    }

    setSavingTask(true);
    setActionError(null);

    try {
      let dueAt: string | null = null;

      if (taskDueAt) {
        const parsed = new Date(taskDueAt);

        if (Number.isNaN(parsed.getTime())) {
          throw new Error(
            "La fecha del seguimiento no es válida.",
          );
        }

        dueAt = parsed.toISOString();
      }

      const command: CreateCommercialTaskCommand = {
        opportunity_id: opportunityId,
        title,
        priority: taskPriority,
        due_at: dueAt,
      };

      const signature =
        JSON.stringify(command);

      let pending =
        pendingTaskCreate.current;

      if (
        pending === null ||
        pending.signature !== signature
      ) {
        pending = {
          signature,
          idempotencyKey:
            newCommercialIdempotencyKey(
              "task",
            ),
          command,
        };

        pendingTaskCreate.current =
          pending;
      }

      const created =
        await createCommercialTask(
          pending.command,
          pending.idempotencyKey,
        );

      pendingTaskCreate.current = null;

      setTasks((current) => [
        created,
        ...current,
      ]);

      setTaskTitle("");
      setTaskPriority("normal");
      setTaskDueAt("");
    } catch (reason: unknown) {
      setActionError(
        errorMessage(
          reason,
          "No se pudo crear el seguimiento.",
        ),
      );
    } finally {
      setSavingTask(false);
    }
  }


  async function transitionTask(
    task: CommercialTask,
    action: "complete" | "cancel",
  ) {
    setTransitioningTaskId(
      task.task_id,
    );
    setActionError(null);

    try {
      const updated =
        action === "complete"
          ? await completeCommercialTask(
              task.task_id,
              {
                expected_version:
                  task.version,
              },
            )
          : await cancelCommercialTask(
              task.task_id,
              {
                expected_version:
                  task.version,
              },
            );

      setTasks((current) =>
        current.map((item) =>
          item.task_id === updated.task_id
            ? updated
            : item,
        ),
      );
    } catch (reason: unknown) {
      setActionError(
        errorMessage(
          reason,
          action === "complete"
            ? "No se pudo completar el seguimiento."
            : "No se pudo cancelar el seguimiento.",
        ),
      );
    } finally {
      setTransitioningTaskId(null);
    }
  }


  if (loading) {
    return (
      <section
        className="space-y-2"
        data-testid="commercial-operations-panel"
      >
        <h3 className="text-sm font-semibold text-slate-800">
          Operación humana
        </h3>
        <p className="text-sm text-slate-600">
          Cargando seguimiento operativo…
        </p>
      </section>
    );
  }


  if (loadError) {
    return (
      <section
        className="space-y-2"
        data-testid="commercial-operations-panel"
      >
        <h3 className="text-sm font-semibold text-slate-800">
          Operación humana
        </h3>

        <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
          {loadError}
        </div>
      </section>
    );
  }


  return (
    <section
      className="space-y-5"
      data-testid="commercial-operations-panel"
    >
      <div>
        <h3 className="text-sm font-semibold text-slate-900">
          Operación humana
        </h3>
        <p className="mt-1 text-xs text-slate-500">
          Estado y seguimiento ingresados por el equipo.
          No modifica la evidencia PR3.
        </p>
      </div>

      {actionError ? (
        <div
          role="alert"
          className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800"
        >
          {actionError}
        </div>
      ) : null}

      <form
        onSubmit={saveOperatorState}
        className="space-y-3 rounded-lg border border-slate-200 bg-slate-50 p-3"
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="text-sm font-medium text-slate-900">
              Estado humano
            </p>

            <p className="text-xs text-slate-500">
              {operatorState
                ? `${confirmationLabel(
                    operatorState.confirmation_status,
                  )} · versión ${operatorState.version}`
                : "Sin revisión humana registrada"}
            </p>
          </div>
        </div>

        <label className="block space-y-1 text-xs font-medium text-slate-700">
          <span>Revisión humana</span>
          <select
            aria-label="Revisión humana"
            value={confirmationStatus}
            onChange={(event) =>
              setConfirmationStatus(
                event.target
                  .value as CommercialConfirmationStatus,
              )
            }
            className="w-full rounded-md border border-slate-300 bg-white px-2 py-2 text-sm"
          >
            {CONFIRMATION_OPTIONS.map(
              (option) => (
                <option
                  key={option.value}
                  value={option.value}
                >
                  {option.label}
                </option>
              ),
            )}
          </select>
        </label>

        <label className="block space-y-1 text-xs font-medium text-slate-700">
          <span>Etapa operativa manual</span>
          <input
            aria-label="Etapa operativa manual"
            value={manualStage}
            onChange={(event) =>
              setManualStage(
                event.target.value,
              )
            }
            placeholder="Ej. follow_up"
            maxLength={128}
            className="w-full rounded-md border border-slate-300 bg-white px-2 py-2 text-sm"
          />
        </label>

        <button
          type="submit"
          disabled={savingState}
          className="rounded-md bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
        >
          {savingState
            ? "Guardando…"
            : "Guardar estado"}
        </button>
      </form>

      <form
        onSubmit={saveActivity}
        className="space-y-3 rounded-lg border border-slate-200 p-3"
      >
        <div>
          <p className="text-sm font-medium text-slate-900">
            Registrar actividad
          </p>
          <p className="text-xs text-slate-500">
            Llamadas, WhatsApp, reuniones, notas o
            seguimientos.
          </p>
        </div>

        <label className="block space-y-1 text-xs font-medium text-slate-700">
          <span>Tipo de actividad</span>
          <select
            aria-label="Tipo de actividad"
            value={activityType}
            onChange={(event) =>
              setActivityType(
                event.target
                  .value as CommercialActivityType,
              )
            }
            className="w-full rounded-md border border-slate-300 bg-white px-2 py-2 text-sm"
          >
            {ACTIVITY_OPTIONS.map(
              (option) => (
                <option
                  key={option.value}
                  value={option.value}
                >
                  {option.label}
                </option>
              ),
            )}
          </select>
        </label>

        <label className="block space-y-1 text-xs font-medium text-slate-700">
          <span>Resumen</span>
          <input
            aria-label="Resumen de actividad"
            value={activitySummary}
            onChange={(event) =>
              setActivitySummary(
                event.target.value,
              )
            }
            maxLength={500}
            className="w-full rounded-md border border-slate-300 px-2 py-2 text-sm"
          />
        </label>

        <label className="block space-y-1 text-xs font-medium text-slate-700">
          <span>Detalle</span>
          <textarea
            aria-label="Detalle de actividad"
            value={activityDetail}
            onChange={(event) =>
              setActivityDetail(
                event.target.value,
              )
            }
            maxLength={10000}
            rows={3}
            className="w-full rounded-md border border-slate-300 px-2 py-2 text-sm"
          />
        </label>

        <button
          type="submit"
          disabled={savingActivity}
          className="rounded-md border border-brand-300 bg-brand-50 px-3 py-2 text-sm font-medium text-brand-800 hover:bg-brand-100 disabled:opacity-50"
        >
          {savingActivity
            ? "Registrando…"
            : "Registrar actividad"}
        </button>
      </form>

      <div className="space-y-2">
        <p className="text-sm font-medium text-slate-900">
          Actividad humana ({activities.length})
        </p>

        {activities.length ? (
          <ul className="space-y-2">
            {activities.map((activity) => (
              <li
                key={activity.activity_id}
                className="rounded-lg border border-slate-200 px-3 py-2"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="text-sm font-medium text-slate-900">
                    {activityLabel(
                      activity.activity_type,
                    )}
                  </p>
                  <p className="text-xs text-slate-500">
                    {dateLabel(
                      activity.occurred_at,
                    )}
                  </p>
                </div>

                <p className="mt-1 text-sm text-slate-800">
                  {activity.summary}
                </p>

                {activity.detail ? (
                  <p className="mt-1 whitespace-pre-wrap text-xs text-slate-600">
                    {activity.detail}
                  </p>
                ) : null}

                <p className="mt-1 text-[11px] text-slate-500">
                  Registrado por{" "}
                  {activity.created_by}
                </p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-slate-500">
            Sin actividad humana registrada.
          </p>
        )}
      </div>

      <form
        onSubmit={saveTask}
        className="space-y-3 rounded-lg border border-slate-200 p-3"
      >
        <div>
          <p className="text-sm font-medium text-slate-900">
            Crear seguimiento
          </p>
          <p className="text-xs text-slate-500">
            Tarea operativa pendiente para esta
            oportunidad.
          </p>
        </div>

        <label className="block space-y-1 text-xs font-medium text-slate-700">
          <span>Seguimiento</span>
          <input
            aria-label="Seguimiento"
            value={taskTitle}
            onChange={(event) =>
              setTaskTitle(
                event.target.value,
              )
            }
            maxLength={500}
            className="w-full rounded-md border border-slate-300 px-2 py-2 text-sm"
          />
        </label>

        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block space-y-1 text-xs font-medium text-slate-700">
            <span>Prioridad</span>
            <select
              aria-label="Prioridad"
              value={taskPriority}
              onChange={(event) =>
                setTaskPriority(
                  event.target
                    .value as CommercialTaskPriority,
                )
              }
              className="w-full rounded-md border border-slate-300 bg-white px-2 py-2 text-sm"
            >
              {PRIORITY_OPTIONS.map(
                (option) => (
                  <option
                    key={option.value}
                    value={option.value}
                  >
                    {option.label}
                  </option>
                ),
              )}
            </select>
          </label>

          <label className="block space-y-1 text-xs font-medium text-slate-700">
            <span>Fecha límite</span>
            <input
              aria-label="Fecha límite"
              type="datetime-local"
              value={taskDueAt}
              onChange={(event) =>
                setTaskDueAt(
                  event.target.value,
                )
              }
              className="w-full rounded-md border border-slate-300 px-2 py-2 text-sm"
            />
          </label>
        </div>

        <button
          type="submit"
          disabled={savingTask}
          className="rounded-md border border-brand-300 bg-brand-50 px-3 py-2 text-sm font-medium text-brand-800 hover:bg-brand-100 disabled:opacity-50"
        >
          {savingTask
            ? "Creando…"
            : "Crear seguimiento"}
        </button>
      </form>

      <div className="space-y-2">
        <p className="text-sm font-medium text-slate-900">
          Seguimientos ({tasks.length})
        </p>

        {tasks.length ? (
          <ul className="space-y-2">
            {tasks.map((task) => (
              <li
                key={task.task_id}
                className="rounded-lg border border-slate-200 px-3 py-2"
              >
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <p className="text-sm font-medium text-slate-900">
                      {task.title}
                    </p>
                    <p className="mt-1 text-xs text-slate-500">
                      {priorityLabel(
                        task.priority,
                      )}
                      {" · "}
                      {dateLabel(
                        task.due_at,
                      )}
                      {" · "}
                      {task.status === "open"
                        ? "Pendiente"
                        : task.status === "done"
                          ? "Completada"
                          : "Cancelada"}
                    </p>
                  </div>

                  {task.status === "open" ? (
                    <div className="flex gap-2">
                      <button
                        type="button"
                        disabled={
                          transitioningTaskId ===
                          task.task_id
                        }
                        onClick={() =>
                          void transitionTask(
                            task,
                            "complete",
                          )
                        }
                        className="rounded-md border border-emerald-300 bg-emerald-50 px-2 py-1 text-xs font-medium text-emerald-800 disabled:opacity-50"
                      >
                        Completar
                      </button>

                      <button
                        type="button"
                        disabled={
                          transitioningTaskId ===
                          task.task_id
                        }
                        onClick={() =>
                          void transitionTask(
                            task,
                            "cancel",
                          )
                        }
                        className="rounded-md border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 disabled:opacity-50"
                      >
                        Cancelar
                      </button>
                    </div>
                  ) : null}
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-slate-500">
            Sin seguimientos registrados.
          </p>
        )}
      </div>
    </section>
  );
}
