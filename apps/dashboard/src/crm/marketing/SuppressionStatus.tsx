import { useState } from "react";
import { useAuthSession } from "../../context/AuthSessionContext";
import { refusalText } from "../commandRefusal";
import { useResource } from "../useResource";
import { Badge, EmptyState, Panel, ResourceGate, Skeleton, StatLine, fmtDate, fmtInt } from "../ui";
import {
  dismissUnsubscribeReview,
  fetchSuppressions,
  newIdempotencyKey,
  resolveUnsubscribeReview,
} from "./marketingApi";
import type { PendingUnsubscribeReview, SuppressionsResponse } from "./marketingTypes";

const SOURCE_LABEL: Record<string, string> = {
  unsubscribe_handler: "Respuesta BAJA aplicada",
  wave1a_suppression: "Supresión heredada (V1)",
  operator_command: "Bloqueo de un operador",
  ndr_handler: "Rebote",
  complaint_handler: "Queja",
};

type ReviewMode = "confirm" | "dismiss";

/**
 * One «BAJA» held for review: confirm it as a permanent unsubscribe (sales/admin), or dismiss it
 * as a false positive (admin only; it needs the review's current `review_sha256`). Each opened
 * form keeps one Idempotency-Key, so a retry after a lost answer replays instead of repeating.
 * The database re-checks the role, the address, the version and that the hold is still pending.
 */
function ReviewActions({
  review,
  canDismiss,
  onDone,
}: {
  review: PendingUnsubscribeReview;
  canDismiss: boolean;
  onDone: () => void;
}) {
  const [mode, setMode] = useState<ReviewMode | null>(null);
  const [text, setText] = useState("");
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const open = (m: ReviewMode) => {
    setMode(m);
    setText("");
    setError(null);
    setKey(newIdempotencyKey());
  };
  const submit = async () => {
    if (!mode || !text.trim()) return;
    setBusy(true);
    setError(null);
    try {
      if (mode === "confirm") {
        await resolveUnsubscribeReview({ assertion_id: review.assertion_id, expected_address: review.address, note: text }, key);
      } else {
        await dismissUnsubscribeReview(
          {
            assertion_id: review.assertion_id,
            expected_address: review.address,
            expected_review_sha256: review.review_sha256 ?? "",
            explanation: text,
          },
          key,
        );
      }
      setMode(null);
      onDone();
    } catch (err) {
      setError(refusalText(err, { fallback: "No se pudo registrar la decisión" }));
    } finally {
      setBusy(false);
    }
  };

  if (mode === null) {
    return (
      <span className="ml-auto flex gap-1.5">
        <button
          type="button"
          onClick={() => open("confirm")}
          className="h-7 rounded-md border border-bad/50 px-2.5 text-xs font-medium text-bad hover:bg-bad-bg"
        >
          Confirmar BAJA
        </button>
        {canDismiss && review.review_sha256 ? (
          <button
            type="button"
            onClick={() => open("dismiss")}
            className="h-7 rounded-md border border-line px-2.5 text-xs font-medium text-ink hover:bg-canvas-sunken"
          >
            Descartar (falso positivo)
          </button>
        ) : null}
      </span>
    );
  }
  const confirm = mode === "confirm";
  const label = confirm ? "Nota de la revisión" : "Explicación del descarte";
  return (
    <form
      className="mt-1.5 w-full space-y-1.5 rounded-md border border-line bg-canvas-sunken p-2"
      data-testid={confirm ? "confirm-review-form" : "dismiss-review-form"}
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <p className="text-[11px] text-ink-muted">
        {confirm
          ? "La dirección quedará suprimida de marketing de forma permanente. No se puede deshacer."
          : "La retención se levanta y la dirección vuelve a poder recibir marketing. Queda registrado quién, cuándo y por qué; la BAJA aún podrá confirmarse después."}
      </p>
      <label className="block text-[11px] font-medium text-ink">
        {label}
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          maxLength={confirm ? 500 : 1000}
          rows={2}
          className="mt-1 block w-full rounded-md border border-line bg-canvas-raised px-2 py-1 text-xs text-ink"
        />
      </label>
      {error ? (
        <p role="alert" className="text-[11px] text-bad">
          {error}
        </p>
      ) : null}
      <div className="flex gap-1.5">
        <button
          type="submit"
          disabled={busy || !text.trim()}
          className="h-7 rounded-md border border-line bg-canvas-raised px-2.5 text-xs font-medium text-ink disabled:opacity-50"
        >
          {confirm ? "Confirmar BAJA permanente" : "Descartar retención"}
        </button>
        <button
          type="button"
          onClick={() => setMode(null)}
          disabled={busy}
          className="h-7 rounded-md px-2.5 text-xs text-ink-muted hover:bg-canvas-raised"
        >
          Cancelar
        </button>
      </div>
    </form>
  );
}

/**
 * W10 — who asked not to receive marketing, since when, and what that refuses today. The only
 * actions are on «BAJA»s held for review, and only when the API mounts the review commands:
 * confirm (sales/admin) and dismiss a false positive (admin). There is no unsubscribe-lifting,
 * re-subscribe or Gmail action, and no Send button anywhere. For a viewer the API masks every
 * address (`***@dominio`) and no action is offered; this component shows what it is given.
 */
export function SuppressionStatus() {
  const [state, reload] = useResource(fetchSuppressions);
  const { session } = useAuthSession();
  const role = session.kind === "signed_in" ? session.operator.role : null;
  const canConfirm = role === "sales" || role === "admin";
  const canDismiss = role === "admin";
  return (
    <div className="space-y-3" data-testid="suppression-status">
      <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={4} />}>
        {(data: SuppressionsResponse) => (
          <>
            <p role="status" data-testid="gmail-sync-notice" className="rounded-md border border-warn/40 bg-warn-bg px-3 py-2 text-xs text-warn">
              <b>{data.gmail_sync.label}</b> Cada BAJA registrada es permanente: no se borra, no se debilita y no existe la
              re-suscripción.
            </p>
            <Panel
              title="Bajas y supresiones de marketing"
              note={`${data.storage.table} · base ${data.storage.database} · sólo lectura`}
              bodyClassName="space-y-3 p-3"
            >
              <StatLine
                items={[
                  { label: "Direcciones con BAJA", value: fmtInt(data.summary.unsubscribed_addresses) },
                  { label: "Mensajes BAJA registrados", value: fmtInt(data.summary.baja_messages) },
                  { label: "Última registrada", value: data.summary.last_recorded_at ? fmtDate(data.summary.last_recorded_at) : "—" },
                  { label: "BAJAS en revisión", value: fmtInt(data.summary.pending_reviews ?? 0) },
                ]}
              />
              <p className="text-[11px] text-ink-muted" data-testid="baja-grammar">
                Regla ({data.grammar.version}): {data.grammar.rule} Se acepta: {data.grammar.accepted.map((a) => `«${a}»`).join(", ")}.
                {data.apply_enabled ? "" : " La aplicación de lotes está desactivada en esta API."}
                {data.sender_policy ? ` Remitente (${data.sender_policy.version}): ${data.sender_policy.rule}` : ""}
              </p>
              {data.entries.length === 0 ? (
                <EmptyState title="Sin BAJAS registradas">Ninguna respuesta BAJA se ha aplicado todavía en esta base.</EmptyState>
              ) : (
                <div className="divide-y divide-line rounded-md border border-line">
                  {data.entries.map((e) => (
                    <div key={e.contact_control_id} className="flex flex-wrap items-center gap-2 px-3 py-1.5 text-[13px]" data-testid="suppression-row">
                      <Badge tone="bad">BAJA</Badge>
                      <span className="text-ink">{e.address}</span>
                      <span className="text-[11px] text-ink-muted">
                        {SOURCE_LABEL[e.source] ?? e.source}
                        {e.reason !== "unsubscribe" ? ` · bloqueo previo: ${e.reason}` : ""} · registrada {fmtDate(e.recorded_at)}
                        {e.last_observed_at ? ` · recibida ${fmtDate(e.last_observed_at)}` : ""} · {fmtInt(e.baja_messages)} mensaje(s)
                      </span>
                    </div>
                  ))}
                </div>
              )}
              {data.truncated ? <p className="text-[11px] text-ink-muted">Se muestran las más recientes.</p> : null}
            </Panel>
            {data.pending_reviews && data.pending_reviews.length > 0 ? (
              <Panel title="BAJAS en revisión" note="remitente no comprobado · bloqueadas para marketing hasta confirmarlas" bodyClassName="p-3">
                <div className="divide-y divide-line rounded-md border border-line" data-testid="pending-reviews">
                  {data.pending_reviews.map((p) => (
                    <div key={p.assertion_id} className="flex flex-wrap items-center gap-2 px-3 py-1.5 text-[13px]" data-testid="pending-review-row">
                      <Badge tone="warn">En revisión</Badge>
                      <span className="text-ink">{p.address}</span>
                      <span className="text-[11px] text-ink-muted">
                        {p.review_reason_label} · registrada {fmtDate(p.recorded_at)}
                        {p.observed_at ? ` · recibida ${fmtDate(p.observed_at)}` : ""}
                      </span>
                      {data.apply_enabled && canConfirm ? (
                        <ReviewActions review={p} canDismiss={canDismiss} onDone={reload} />
                      ) : null}
                    </div>
                  ))}
                </div>
              </Panel>
            ) : null}
            <Panel title="Audiencias congeladas frente a las BAJAS de hoy" bodyClassName="p-3">
              {data.frozen_campaigns.length === 0 ? (
                <EmptyState title="Sin audiencias congeladas">No hay instantáneas que contrastar.</EmptyState>
              ) : (
                <ul className="space-y-1 text-xs" data-testid="frozen-vs-baja">
                  {data.frozen_campaigns.map((c) => (
                    <li key={c.campaign_id}>
                      <b className="text-ink">{c.name}</b>: {fmtInt(c.included_at_freeze)} incluidos al congelar ·{" "}
                      <span className={c.unsubscribed_since_freeze ? "text-bad" : "text-ink-muted"}>
                        {fmtInt(c.unsubscribed_since_freeze)} con BAJA posterior
                      </span>{" "}
                      {c.pending_review_since_freeze ? `· ${fmtInt(c.pending_review_since_freeze)} con BAJA en revisión ` : ""}
                      · {fmtInt(c.refused_since_freeze)} rechazados hoy por cualquier control
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
          </>
        )}
      </ResourceGate>
    </div>
  );
}
