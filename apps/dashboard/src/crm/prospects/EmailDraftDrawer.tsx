/**
 * «Preparar correo» — preview the intro email and create a Gmail draft (never sends).
 *
 * States:
 *   loading   — preview fetch in flight
 *   preview   — email preview loaded, «Crear borrador» button ready
 *   creating  — draft creation in flight
 *   done      — draft created, shows the Gmail link
 *   error     — any fetch or command error
 */
import { useCallback, useEffect, useState } from "react";
import { Button } from "../ui";
import {
  type PrepareProspectEmailResult,
  type ProspectEmailPreview,
  fetchEmailPreview,
  newProspectCommandKey,
  prepareProspectEmail,
  prospectRefusal,
} from "./prospectApi";

type DrawerState =
  | { kind: "loading" }
  | { kind: "preview"; data: ProspectEmailPreview; idempotencyKey: string }
  | { kind: "creating"; data: ProspectEmailPreview; idempotencyKey: string }
  | { kind: "done"; result: PrepareProspectEmailResult }
  | { kind: "error"; message: string };

interface Props {
  findingId: string;
  /** The finding's last draft, if any: the button then makes another one (`again`). */
  existingDraft?: { gmail_url: string | null } | null;
  onClose: () => void;
  /** Called after a draft is successfully created (allows the parent to reload the list). */
  onDrafted?: () => void;
}

export function EmailDraftDrawer({ findingId, existingDraft, onClose, onDrafted }: Props) {
  const [state, setState] = useState<DrawerState>({ kind: "loading" });

  // Load preview on mount
  useEffect(() => {
    fetchEmailPreview(findingId)
      .then((data) => setState({ kind: "preview", data, idempotencyKey: newProspectCommandKey() }))
      .catch((err: unknown) => {
        const refusal = prospectRefusal(err);
        setState({ kind: "error", message: refusal?.message ?? "No se pudo cargar la vista previa." });
      });
  }, [findingId]);

  const handleCreate = useCallback(async () => {
    if (state.kind !== "preview") return;
    const { idempotencyKey } = state;
    setState({ kind: "creating", data: state.data, idempotencyKey });
    try {
      const result = await prepareProspectEmail({ finding_id: findingId, again: Boolean(existingDraft) }, idempotencyKey);
      setState({ kind: "done", result });
      onDrafted?.();
    } catch (err: unknown) {
      const refusal = prospectRefusal(err);
      setState({ kind: "error", message: refusal?.message ?? "No se pudo crear el borrador." });
    }
  }, [state, findingId, existingDraft, onDrafted]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Preparar correo"
      data-testid="email-draft-drawer"
      className="fixed inset-0 z-50 flex items-end justify-center bg-scrim/50 p-4 sm:items-center"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="w-full max-w-xl rounded-xl border border-line bg-canvas-raised shadow-xl">
        <div className="flex items-center justify-between border-b border-line px-5 py-3">
          <h2 className="text-sm font-semibold text-ink-strong">Preparar correo</h2>
          <button
            aria-label="Cerrar"
            onClick={onClose}
            className="rounded p-1 text-ink-subtle hover:text-ink-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
          >
            ✕
          </button>
        </div>

        <div className="px-5 py-4 space-y-3 max-h-[70vh] overflow-y-auto">
          {existingDraft && state.kind !== "done" ? (
            <div className="rounded-lg bg-canvas-subtle border border-line px-3 py-2 text-sm text-ink-subtle">
              Ya existe un borrador para este prospecto.{" "}
              {existingDraft.gmail_url ? (
                <a
                  href={existingDraft.gmail_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-brand-600 underline hover:no-underline"
                >
                  Ver en Gmail
                </a>
              ) : null}
            </div>
          ) : null}

          {state.kind === "loading" ? (
            <p className="text-sm text-ink-subtle">Cargando vista previa…</p>
          ) : state.kind === "error" ? (
            <p className="text-sm text-red-600" role="alert">{state.message}</p>
          ) : state.kind === "done" ? (
            <div className="space-y-2">
              <p className="text-sm text-ink-strong">
                Borrador creado en Gmail para{" "}
                <span className="font-mono text-xs">{state.result.to}</span>.
              </p>
              {!state.result.signature ? (
                <p className="text-xs text-amber-600">
                  No se pudo leer la firma de Gmail; se usó el bloque de texto alternativo.
                </p>
              ) : null}
              {!state.result.labels_applied ? (
                <p className="text-xs text-amber-600">
                  No se pudieron poner las etiquetas «Claude – revisar» y «Prospecto»: falta autorizar Gmail de nuevo.
                </p>
              ) : null}
              <a
                href={state.result.gmail_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-block rounded-lg bg-brand-50 border border-brand-200 px-3 py-2 text-sm text-brand-700 hover:bg-brand-100"
                data-testid="gmail-draft-link"
              >
                Abrir borrador en Gmail →
              </a>
            </div>
          ) : (
            <div className="space-y-3">
              <div>
                <p className="text-xs font-medium text-ink-subtle uppercase tracking-wide mb-0.5">Asunto</p>
                <p className="text-sm text-ink-strong">{state.data.subject}</p>
              </div>
              {state.data.to ? (
                <div>
                  <p className="text-xs font-medium text-ink-subtle uppercase tracking-wide mb-0.5">Para</p>
                  <p className="text-sm text-ink-strong font-mono">{state.data.to}</p>
                </div>
              ) : (
                <p className="text-sm text-amber-600">Este prospecto no tiene correo registrado.</p>
              )}
              <div>
                <p className="text-xs font-medium text-ink-subtle uppercase tracking-wide mb-0.5">Vista previa</p>
                <pre className="whitespace-pre-wrap text-xs text-ink-strong bg-canvas-subtle rounded-lg border border-line p-3 max-h-48 overflow-y-auto">
                  {state.data.text}
                </pre>
              </div>
              <p className="text-xs text-ink-subtle">
                La firma real de contacto@ se agrega al crear el borrador.
              </p>
            </div>
          )}
        </div>

        {(state.kind === "preview" || state.kind === "creating") ? (
          <div className="flex justify-end gap-2 border-t border-line px-5 py-3">
            <Button variant="quiet" onClick={onClose}>
              Cancelar
            </Button>
            <Button
              variant="primary"
              disabled={state.kind === "creating" || !state.data.to}
              onClick={handleCreate}
              data-testid="create-draft-btn"
            >
              {state.kind === "creating" ? "Creando…" : existingDraft ? "Crear otro borrador" : "Crear borrador en Gmail"}
            </Button>
          </div>
        ) : state.kind === "done" ? (
          <div className="flex justify-end border-t border-line px-5 py-3">
            <Button variant="quiet" onClick={onClose}>
              Cerrar
            </Button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
