import { useMemo, useRef, useState } from "react";
import { useAuthSession } from "../../context/AuthSessionContext";
import type { CampaignSummary, TotalKey } from "../crmTypes";
import { Badge, Panel, ResourceGate, Segmented, Skeleton, fmtInt } from "../ui";
import { useResource } from "../useResource";
import { PLANNING_LABEL, fmtLongDay, relativeDay, santiagoTime, todayInSantiago } from "./calendar";
import { CampaignAudit } from "./CampaignAudit";
import { CampaignRecipients } from "./CampaignRecipients";
import { CampaignReplies } from "./CampaignReplies";
import { FILTER_TOTALS, NEVER_SENT_LABEL, TOTAL_HINT, TOTAL_LABEL, neverSent, repliesUnknown } from "./campaignTotals";
import { EmailFrame } from "./EmailFrame";
import { fetchCampaignArchive, newIdempotencyKey, refusalOf, setCampaignPlanning } from "./marketingApi";
import type { CampaignArchive, EquipmentTaxonomy } from "./marketingTypes";

export type DetailTab = "resumen" | "html" | "destinatarios" | "respuestas" | "auditoria";

const STATUS_LABEL: Record<string, string> = {
  archived: "Archivada",
  draft: "Borrador",
  audience_frozen: "Audiencia congelada",
  approved: "Aprobada",
  active: "Enviando",
  completed: "Completada",
  cancelled: "Cancelada",
};

const RECIPIENT_LABEL: Record<string, string> = {
  sent: "Enviado",
  bounced: "Rebotado",
  excluded: "Excluido",
  snapshotted: "En la audiencia, no enviado",
  failed: "Fallido",
  replied: "Respondió",
  unsubscribed: "Baja",
};

const HTML_STATE: Record<Exclude<CampaignArchive["html_state"], "archived_verified">, { title: string; body: string }> = {
  not_archived: {
    title: "HTML enviado no archivado",
    body: "El registro histórico de esta campaña no incluye el HTML que se envió. No se reconstruye ni se sustituye por un borrador.",
  },
  not_frozen: {
    title: "Aún no hay HTML archivado",
    body: "Es un borrador: su contenido todavía puede cambiar. Queda archivado, con su huella, al congelar la audiencia.",
  },
  no_html: {
    title: "Sin HTML",
    body: "El contenido congelado de esta campaña no tiene versión HTML; sólo texto.",
  },
  fingerprint_mismatch: {
    title: "HTML archivado no verificable",
    body: "El HTML guardado no coincide con la huella registrada al congelar. No se muestra.",
  },
};

type PreviewMode = "desktop" | "mobile" | "raw";

/**
 * One campaign: Resumen, HTML, Destinatarios, Respuestas and Auditoría, all read from PostgreSQL.
 * A historical (archived) campaign offers no edit, reopen, resend or planning action; its figures
 * are the recorded ones and every total opens the rows behind it.
 */
export function CampaignDetail({
  summary,
  planningEnabled,
  taxonomy = null,
  initialTab = "resumen",
  initialTotal = "audience",
  onBack,
  onEdit,
  onPlanned,
}: {
  summary: CampaignSummary;
  planningEnabled: boolean;
  taxonomy?: EquipmentTaxonomy | null;
  initialTab?: DetailTab;
  initialTotal?: TotalKey;
  onBack: () => void;
  onEdit: (campaignId: string) => void;
  onPlanned: () => void;
}) {
  const [state, reload] = useResource(() => fetchCampaignArchive(summary.campaign_id), [summary.campaign_id]);
  const [tab, setTab] = useState<DetailTab>(initialTab);
  const [total, setTotal] = useState<TotalKey>(initialTotal);
  const openTotal = (k: TotalKey) => {
    setTotal(k);
    setTab(k === "responses" && repliesUnknown(summary.replies, summary.totals) ? "respuestas" : "destinatarios");
  };
  const editable = summary.status === "draft" || summary.status === "audience_frozen";
  return (
    <div className="space-y-3" data-testid="campaign-detail">
      <button type="button" onClick={onBack} className="text-xs font-medium text-brand-700 hover:underline">
        ← Volver
      </button>
      <header className="flex flex-wrap items-start gap-3 rounded-lg border border-line bg-canvas-raised p-4">
        <div className="min-w-0 flex-1">
          <h2 className="text-lg font-semibold tracking-tight text-ink">{summary.name}</h2>
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            <Badge glyph={false} tone={summary.status === "draft" ? "info" : "neutral"}>
              {STATUS_LABEL[summary.status] ?? summary.status}
            </Badge>
            {summary.origin === "imported_v1" ? (
              <Badge tone="warn" title="Cargada desde el registro de envíos de V1.">
                Histórica importada (V1)
              </Badge>
            ) : (
              <Badge tone="brand" glyph={false}>
                Nativa del CRM
              </Badge>
            )}
            {summary.status === "archived" ? (
              <Badge tone="neutral" title="Una campaña histórica no se edita, reabre, reenvía ni planifica.">
                Cerrada · solo lectura
              </Badge>
            ) : null}
            {neverSent(summary) ? (
              <span data-testid="detail-never-sent">
                <Badge tone="neutral" title="Archivada sin ningún intento de envío registrado.">{NEVER_SENT_LABEL}</Badge>
              </span>
            ) : null}
          </div>
        </div>
        {editable ? (
          <button
            type="button"
            onClick={() => onEdit(summary.campaign_id)}
            className="h-7 rounded-md border border-line px-3 text-xs font-medium text-ink hover:bg-canvas-sunken"
          >
            {summary.status === "draft" ? "Editar borrador" : "Abrir campaña"}
          </button>
        ) : null}
      </header>
      <div className="max-w-full overflow-x-auto">
        <Segmented
          label="Pestañas de la campaña"
          value={tab}
          onChange={setTab}
          options={[
            { value: "resumen", label: "Resumen" },
            { value: "html", label: "HTML" },
            { value: "destinatarios", label: "Destinatarios", count: summary.totals?.audience },
            { value: "respuestas", label: "Respuestas" },
            { value: "auditoria", label: "Auditoría" },
          ]}
        />
      </div>
      {tab === "destinatarios" ? (
        <CampaignRecipients
          campaignId={summary.campaign_id}
          totals={summary.totals ?? undefined}
          initialTotal={total}
          taxonomy={taxonomy}
          repliesNotSynced={repliesUnknown(summary.replies, summary.totals)}
        />
      ) : tab === "respuestas" ? (
        <CampaignReplies campaignId={summary.campaign_id} />
      ) : tab === "auditoria" ? (
        <CampaignAudit campaignId={summary.campaign_id} />
      ) : (
        <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={4} />}>
          {(a) =>
            tab === "html" ? (
              <SentHtml archive={a} />
            ) : (
              <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1fr)_22rem]">
                <div className="min-w-0 space-y-3">
                  <TotalsPanel archive={a} onOpen={openTotal} />
                  <AttemptsPanel archive={a} />
                </div>
                <div className="min-w-0 space-y-3">
                  <SendRecord archive={a} />
                  {editable ? <PlanningEditor summary={summary} planningEnabled={planningEnabled} onSaved={onPlanned} /> : null}
                </div>
              </div>
            )
          }
        </ResourceGate>
      )}
    </div>
  );
}

/** Every recipient total, each a button to the rows behind it. */
function TotalsPanel({ archive: a, onOpen }: { archive: CampaignArchive; onOpen: (k: TotalKey) => void }) {
  const t = a.totals;
  if (!t) return null;
  const unknownReplies = repliesUnknown(a.replies, t);
  return (
    <Panel title="Destinatarios" note="cada cifra abre sus filas" bodyClassName="p-3">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3" data-testid="detail-totals">
        {FILTER_TOTALS.map((k) => (
          <button
            key={k}
            type="button"
            title={TOTAL_HINT[k]}
            onClick={() => onOpen(k)}
            className="rounded-md border border-line bg-canvas-raised px-3 py-2 text-left hover:border-line-strong hover:bg-canvas-sunken/60"
            data-testid={`detail-total-${k}`}
          >
            <span className="block text-[11px] text-ink-faint">{TOTAL_LABEL[k]}</span>
            <span className="block text-lg font-semibold tabular-nums text-ink">
              {k === "responses" && unknownReplies ? <span className="text-sm font-medium text-ink-faint">No sincronizadas</span> : fmtInt(t[k])}
            </span>
          </button>
        ))}
      </div>
      <p className="mt-2 text-[11px] text-ink-faint">
        Destinatarios, no intentos: una persona con dos intentos cuenta una vez. «Enviados» son destinatarios con un envío aceptado por
        Gmail; aceptado no es entregado.
      </p>
    </Panel>
  );
}

function AttemptsPanel({ archive: a }: { archive: CampaignArchive }) {
  const x = a.attempt_totals;
  if (!x) return null;
  return (
    <Panel title="Intentos de envío" note="outbound.send_attempt" bodyClassName="px-3 py-2">
      <dl className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-1 text-xs" data-testid="attempt-totals">
        <dt className="text-ink-muted">Intentos registrados</dt>
        <dd className="text-right font-semibold tabular-nums text-ink">{fmtInt(x.attempts)}</dd>
        <dt className="text-ink-muted">Aceptados por Gmail</dt>
        <dd className="text-right font-semibold tabular-nums text-ink">{fmtInt(x.accepted)}</dd>
        <dt className="text-ink-muted">Rechazados</dt>
        <dd className="text-right font-semibold tabular-nums text-ink">
          {fmtInt(x.rejected)}
          {x.rejected_undated ? <span className="ml-1 font-normal text-ink-faint">({fmtInt(x.rejected_undated)} sin fecha registrada)</span> : null}
        </dd>
        <dt className="text-ink-muted">Entrega confirmada</dt>
        <dd className="text-right tabular-nums text-ink">
          {x.delivery_confirmed ? fmtInt(x.delivery_confirmed) : <span className="text-ink-faint">Ninguna confirmación registrada</span>}
        </dd>
        <dt className="text-ink-muted">Rebotes en el intento</dt>
        <dd className="text-right tabular-nums text-ink">
          {x.delivery_bounced ? fmtInt(x.delivery_bounced) : <span className="text-ink-faint">Registrados sólo por destinatario</span>}
        </dd>
      </dl>
      <p className="mt-2 text-[11px] text-ink-faint">
        {x.delivery_confirmed === 0 && x.accepted > 0
          ? "El CRM no recibió confirmaciones de entrega: cada envío aceptado figura como pendiente de confirmación."
          : null}
      </p>
    </Panel>
  );
}

function SentHtml({ archive: a }: { archive: CampaignArchive }) {
  const [mode, setMode] = useState<PreviewMode>("desktop");
  const [blocked, setBlocked] = useState<{ images: number; removed: string[] }>({ images: 0, removed: [] });
  const onBlocked = useMemo(
    () => (images: string[], removed: string[]) => setBlocked({ images: images.length, removed }),
    [],
  );
  if (a.html === null) {
    const note = HTML_STATE[a.html_state as Exclude<CampaignArchive["html_state"], "archived_verified">];
    return (
      <Panel title="Contenido enviado">
        <div className="flex min-h-[16rem] flex-col items-center justify-center gap-1 px-6 py-10 text-center" data-testid="html-unavailable" data-state={a.html_state}>
          <p className="text-sm font-semibold text-ink">{note.title}</p>
          <p className="max-w-md text-xs text-ink-muted">{note.body}</p>
        </div>
      </Panel>
    );
  }
  return (
    <Panel
      title="Contenido enviado"
      note="Instantánea inmutable, verificada con su huella"
      aside={
        <Segmented
          label="Vista del correo"
          value={mode}
          onChange={setMode}
          options={[
            { value: "desktop", label: "Escritorio" },
            { value: "mobile", label: "Móvil" },
            { value: "raw", label: "HTML" },
          ]}
        />
      }
    >
      <div className="border-b border-line px-3 py-2 text-xs">
        <p className="font-semibold text-ink">{a.subject ?? "Sin asunto registrado"}</p>
        <p className="text-ink-muted">{a.preheader ?? <span className="text-ink-faint">Sin preheader</span>}</p>
      </div>
      {mode === "raw" ? (
        <pre
          className="max-h-[48rem] overflow-auto whitespace-pre-wrap break-all bg-canvas-sunken p-3 font-mono text-[11px] leading-relaxed text-ink"
          data-testid="raw-html"
          aria-label="HTML enviado, sólo lectura"
        >
          {a.html}
        </pre>
      ) : (
        <div className="flex justify-center overflow-x-auto bg-canvas-sunken p-3" data-testid={`preview-${mode}`}>
          <div className={mode === "mobile" ? "rounded-[1.5rem] border-[6px] border-ink/80 bg-white shadow-sm" : "bg-white shadow-sm"}>
            <EmailFrame
              html={a.html}
              width={mode === "mobile" ? 375 : 640}
              height={mode === "mobile" ? 700 : 860}
              title={`Correo enviado: ${a.name} (${mode === "mobile" ? "móvil" : "escritorio"})`}
              onBlocked={onBlocked}
            />
          </div>
        </div>
      )}
      <p className="border-t border-line px-3 py-1.5 text-[11px] text-ink-faint" data-testid="preview-safety">
        Vista aislada: sin scripts, formularios ni navegación; los enlaces no abren nada.
        {blocked.images ? ` ${blocked.images} imagen(es) remota(s) no cargada(s) (posible seguimiento).` : ""}
        {blocked.removed.length ? ` Eliminado: ${blocked.removed.join(", ")}.` : ""}
      </p>
    </Panel>
  );
}

function SendRecord({ archive: a }: { archive: CampaignArchive }) {
  const today = todayInSantiago();
  const accepted = a.send_attempts.filter((s) => s.submission_state === "accepted").reduce((n, s) => n + s.count, 0);
  const rejected = a.send_attempts.filter((s) => s.submission_state === "rejected").reduce((n, s) => n + s.count, 0);
  const attempts = a.send_attempts.reduce((n, s) => n + s.count, 0);
  const recipients = Object.entries(a.recipients_by_state).sort((x, y) => y[1] - x[1]);
  const unavailable = <span className="text-ink-faint">No disponible</span>;
  const notImported = <span className="text-ink-faint">No importado desde V1</span>;
  return (
    <Panel title="Registro de envío" bodyClassName="px-3 py-2">
      <dl className="grid grid-cols-[8.5rem_minmax(0,1fr)] gap-x-2 gap-y-1.5 text-xs" data-testid="send-record">
        <dt className="text-ink-faint">Fecha de envío</dt>
        <dd className="text-ink" data-testid="send-dates">
          {a.send_batches.length === 0 ? (
            <span className="text-ink-faint">{neverSent(a) ? NEVER_SENT_LABEL : "Sin envíos registrados"}</span>
          ) : (
            <ul className="space-y-0.5">
              {a.send_batches.map((b, i) => (
                <li key={b.day}>
                  {a.send_batches.length > 1 ? <span className="text-ink-faint">Lote {i + 1}: </span> : null}
                  {fmtLongDay(b.day)} · {santiagoTime(b.first_accepted_at)}–{santiagoTime(b.last_accepted_at)} ·{" "}
                  {fmtInt(b.accepted)} aceptados <span className="text-ink-faint">({relativeDay(b.day, today)})</span>
                </li>
              ))}
            </ul>
          )}
        </dd>
        <dt className="text-ink-faint">Remitente</dt>
        <dd className="truncate text-ink" title={a.sender_address ?? undefined}>
          {a.sender_address ? `${a.sender_name ? `${a.sender_name} · ` : ""}${a.sender_address}` : unavailable}
        </dd>
        <dt className="text-ink-faint">Asunto</dt>
        <dd className="text-ink" data-testid="subject-state">{a.subject ?? (a.subject_state === "not_imported" ? notImported : unavailable)}</dd>
        <dt className="text-ink-faint">Preheader</dt>
        <dd className="text-ink">{a.preheader ?? (a.preheader_state === "not_imported" ? notImported : unavailable)}</dd>
        <dt className="text-ink-faint">Versión de campaña</dt>
        <dd className="tabular-nums text-ink">v{a.version}</dd>
        <dt className="text-ink-faint">Huella del contenido</dt>
        <dd className="truncate font-mono text-[11px] text-ink" title={a.content_sha256 ?? undefined} data-testid="content-hash">
          {a.content_sha256 ? `sha256:${a.content_sha256.slice(0, 16)}…` : unavailable}
        </dd>
        <dt className="text-ink-faint">Intentos de envío</dt>
        <dd className="tabular-nums text-ink">
          {attempts ? `${fmtInt(attempts)} · ${fmtInt(accepted)} aceptados${rejected ? ` · ${fmtInt(rejected)} rechazados` : ""}` : "0"}
        </dd>
        <dt className="text-ink-faint">Destinatarios</dt>
        <dd className="text-ink">
          {recipients.length === 0
            ? "Sin audiencia registrada"
            : recipients.map(([s, n]) => `${RECIPIENT_LABEL[s] ?? s}: ${fmtInt(n)}`).join(" · ")}
        </dd>
        <dt className="text-ink-faint">Aperturas y clics</dt>
        <dd className="text-ink-faint">No registrados en el CRM</dd>
      </dl>
      <p className="mt-2 text-[11px] text-ink-faint">
        Fuente: <code>{a.storage.table}</code> en <code>{a.storage.database}</code>. Horas en Santiago.
      </p>
    </Panel>
  );
}

const PLANNERS = new Set(["sales", "admin"]);

function PlanningEditor({
  summary,
  planningEnabled,
  onSaved,
}: {
  summary: CampaignSummary;
  planningEnabled: boolean;
  onSaved: () => void;
}) {
  const { session } = useAuthSession();
  const role = session.kind === "signed_in" ? session.operator.role : null;
  const today = todayInSantiago();
  const storedTime = summary.planned_for_at ? santiagoTime(summary.planned_for_at) : "";
  const [day, setDay] = useState(summary.planned_for_date ?? "");
  const [time, setTime] = useState(storedTime);
  const [version, setVersion] = useState(summary.planning_version ?? 0);
  const [saved, setSaved] = useState({ day: summary.planned_for_date ?? "", time: storedTime });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "good" | "bad"; text: string } | null>(null);
  // One key per intended change: a double click or a retry of the same change replays.
  const key = useRef<{ for: string; key: string } | null>(null);

  const canEdit = planningEnabled && role !== null && PLANNERS.has(role);
  const dirty = day !== saved.day || time !== saved.time;

  const submit = async (nextDay: string, nextTime: string) => {
    const intent = `${version}|${nextDay}|${nextTime}`;
    if (!key.current || key.current.for !== intent) key.current = { for: intent, key: newIdempotencyKey() };
    setBusy(true);
    setMessage(null);
    try {
      const r = await setCampaignPlanning(
        {
          campaign_id: summary.campaign_id,
          expected_planning_version: version,
          planned_for_date: nextDay || null,
          planned_for_time: nextDay && nextTime ? nextTime : null,
        },
        key.current.key,
      );
      const t = r.planned_for_at ? santiagoTime(r.planned_for_at) : "";
      setVersion(r.planning_version);
      setDay(r.planned_for_date ?? "");
      setTime(t);
      setSaved({ day: r.planned_for_date ?? "", time: t });
      setMessage({ tone: "good", text: r.planned_for_date ? "Planificación guardada. No se programó ningún envío." : "Planificación eliminada." });
      onSaved();
    } catch (err) {
      const refusal = refusalOf(err);
      setMessage({ tone: "bad", text: refusal?.message ?? String(err) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel title="Planificación" bodyClassName="space-y-2 px-3 py-2.5">
      <p className="rounded-md border border-dashed border-warn/60 bg-warn-bg px-2 py-1 text-[11px] font-medium text-warn" data-testid="planning-label">
        {PLANNING_LABEL}
      </p>
      {saved.day ? (
        <p className="text-xs text-ink" data-testid="planning-current">
          Planificada para {fmtLongDay(saved.day)}
          {saved.time ? ` a las ${saved.time}` : ""} · <span className="text-ink-muted">{relativeDay(saved.day, today)}</span>
        </p>
      ) : (
        <p className="text-xs text-ink-muted" data-testid="planning-current">Sin fecha planificada.</p>
      )}
      {canEdit ? (
        <form
          className="space-y-2"
          onSubmit={(e) => {
            e.preventDefault();
            void submit(day, time);
          }}
        >
          <div className="flex flex-wrap items-end gap-2">
            <label className="text-[11px] text-ink-muted">
              Fecha (Santiago)
              <input
                type="date"
                value={day}
                min={today}
                onChange={(e) => setDay(e.target.value)}
                className="mt-0.5 block h-8 rounded-md border border-line bg-canvas-raised px-2 text-xs text-ink"
              />
            </label>
            <label className="text-[11px] text-ink-muted">
              Hora (opcional)
              <input
                type="time"
                value={time}
                disabled={!day}
                onChange={(e) => setTime(e.target.value)}
                className="mt-0.5 block h-8 rounded-md border border-line bg-canvas-raised px-2 text-xs text-ink disabled:opacity-50"
              />
            </label>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="submit"
              disabled={busy || !dirty || !day}
              className="h-7 rounded-md bg-ink px-3 text-xs font-medium text-white hover:bg-black disabled:opacity-40"
              data-testid="save-planning"
            >
              Guardar planificación
            </button>
            {saved.day ? (
              <button
                type="button"
                disabled={busy}
                onClick={() => void submit("", "")}
                className="h-7 rounded-md border border-line px-3 text-xs font-medium text-ink hover:bg-canvas-sunken disabled:opacity-40"
                data-testid="clear-planning"
              >
                Quitar fecha
              </button>
            ) : null}
          </div>
        </form>
      ) : (
        <p className="text-[11px] text-ink-faint" data-testid="planning-read-only">
          {!planningEnabled
            ? "La planificación no está habilitada en este entorno."
            : "Sólo lectura: planificar requiere el rol Ventas o Administración."}
        </p>
      )}
      {message ? (
        <p role={message.tone === "bad" ? "alert" : "status"} className={`text-[11px] ${message.tone === "bad" ? "text-bad" : "text-good"}`}>
          {message.text}
        </p>
      ) : null}
    </Panel>
  );
}
