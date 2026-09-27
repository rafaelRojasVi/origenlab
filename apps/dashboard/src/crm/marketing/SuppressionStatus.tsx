import { useResource } from "../useResource";
import { Badge, EmptyState, Panel, ResourceGate, Skeleton, StatLine, fmtDate, fmtInt } from "../ui";
import { fetchSuppressions } from "./marketingApi";
import type { SuppressionsResponse } from "./marketingTypes";

const SOURCE_LABEL: Record<string, string> = {
  unsubscribe_handler: "Respuesta BAJA aplicada",
  wave1a_suppression: "Supresión heredada (V1)",
  operator_command: "Bloqueo de un operador",
  ndr_handler: "Rebote",
  complaint_handler: "Queja",
};

/**
 * W10 — who asked not to receive marketing, since when, and what that refuses today. Read-only:
 * there is no unsubscribe, re-subscribe or Gmail action here, and no Send button anywhere. For a
 * viewer the API masks every address (`***@dominio`); this component shows what it is given.
 */
export function SuppressionStatus() {
  const [state, reload] = useResource(fetchSuppressions);
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
