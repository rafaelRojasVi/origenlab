import { useCallback, useEffect, useRef, useState } from "react";
import { fetchOperatorAutomationStatus } from "../../api/operatorClient";
import type { OperatorAutomationStatus } from "../../api/operatorTypes";
import {
  AUTOMATION_MISSING_STATE_HELP,
  AUTOMATION_MISSING_STATE_PRIMARY,
  buildAutomationSnapshotSummary,
  buildChilecompraAutomationSummary,
  automationRecommendedActionLabel,
  automationVerdictLabel,
  automationVerdictTone,
  chilecompraAutomationResultLabel,
  formatAutomationRecommendedActionForPanel,
  formatAutomationBool,
  formatChilecompraApiDetailSummary,
  formatAutomationTimestamp,
  mailLoopStatusLabel,
  mirrorLoopStatusLabel,
  operatorAutomationStatePartiallyMissing,
} from "../../lib/automationHealthLabels";
import {
  formatOperatorPathDisplay,
  formatSectionPathDisplay,
} from "../../lib/operatorPathDisplay";
import {
  AUTOMATION_FRESHNESS_TONE_CLASS,
  buildAutomationFreshnessSummary,
} from "../../lib/automationFreshness";
import {
  AUTOMATION_RUN_TONE_CLASS,
  buildAutomationRunSummary,
} from "../../lib/automationRunSummary";

export interface AutomationHealthCardProps {
  variant?: "summary" | "detailed";
  onRefresh?: () => void;
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[var(--color-muted)]">{label}</dt>
      <dd className="font-medium break-words">{value}</dd>
    </div>
  );
}

export function AutomationHealthCard({
  variant = "summary",
  onRefresh,
}: AutomationHealthCardProps) {
  const [status, setStatus] = useState<OperatorAutomationStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mountedRef = useRef(false);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const loadStatus = useCallback(
    async (isRefresh: boolean) => {
      if (!mountedRef.current) {
        return;
      }
      if (isRefresh) {
        setRefreshing(true);
      } else {
        setLoading(true);
      }
      setError(null);
      try {
        const data = await fetchOperatorAutomationStatus();
        if (!mountedRef.current) {
          return;
        }
        setStatus(data);
        if (isRefresh) {
          onRefresh?.();
        }
      } catch (e: unknown) {
        if (!mountedRef.current) {
          return;
        }
        setError(e instanceof Error ? e.message : "Error desconocido");
        if (!isRefresh) {
          setStatus(null);
        }
      } finally {
        if (!mountedRef.current) {
          return;
        }
        setLoading(false);
        setRefreshing(false);
      }
    },
    [onRefresh],
  );

  useEffect(() => {
    void loadStatus(false);
  }, [loadStatus]);

  const refreshButton = (
    <button
      type="button"
      className="rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-xs font-medium text-brand-900 shadow-sm hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
      onClick={() => void loadStatus(true)}
      disabled={loading || refreshing}
      data-testid="automation-refresh-button"
    >
      {refreshing ? "Actualizando…" : "Actualizar estado"}
    </button>
  );

  if (loading && !status) {
    return (
      <section
        className="rounded-xl border border-slate-200 bg-slate-50/60 px-5 py-4"
        data-testid="automation-health-card"
        aria-busy="true"
      >
        <div className="h-16 animate-pulse rounded-md bg-slate-200/70" />
      </section>
    );
  }

  if ((error && !status) || !status) {
    return (
      <section
        className="rounded-xl border border-amber-200 bg-amber-50/80 px-5 py-4 text-sm text-amber-950"
        data-testid="automation-health-card"
        role="status"
      >
        <div className="flex flex-wrap items-start justify-between gap-3">
          <p className="font-medium">No se pudo leer estado de automatización</p>
          {refreshButton}
        </div>
        {error ? <p className="mt-1 break-words text-xs">{error}</p> : null}
      </section>
    );
  }

  const tone = automationVerdictTone(status.verdict);
  const mirrorSynced = mirrorLoopStatusLabel(status);
  const chilecompra = status.chilecompra_equipment_auto_refresh ?? {
    state_exists: false,
    lock_live: false,
    lock_age_seconds: null,
    freshness_age_seconds: null,
    next_run_due: null,
    consecutive_failures: 0,
  };
  const showLockOrPause =
    status.mail_auto_refresh.lock_live ||
    status.dashboard_auto_mirror.lock_live ||
    chilecompra.lock_live ||
    status.mail_auto_refresh.paused ||
    status.dashboard_auto_mirror.paused;
  const missingState = operatorAutomationStatePartiallyMissing(status);
  const snapshotSummary = buildAutomationSnapshotSummary(status);
  const chilecompraSummary = buildChilecompraAutomationSummary(status);
  const cronNote = status.cron.note?.trim();
  const activeCurrentDisplay = formatOperatorPathDisplay(
    status.active_current_dir,
    status.active_current_dir_info,
  );
  const publishedQueueDisplay = formatSectionPathDisplay(
    "published_queue",
    chilecompra.published_queue,
    chilecompra.path_info,
  );
  const candidateAuditDisplay = formatSectionPathDisplay(
    "candidate_audit",
    chilecompra.candidate_audit,
    chilecompra.path_info,
  );
  const freshnessSummary = buildAutomationFreshnessSummary(status);
  const runSummary = buildAutomationRunSummary(status);

  return (
    <section
      className={`rounded-xl border px-5 py-5 shadow-sm ${tone.banner}`}
      data-testid="automation-health-card"
      aria-labelledby="automation-health-heading"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          {variant === "summary" ? (
            <>
              <h2 id="automation-health-heading" className="text-lg font-semibold text-brand-900">
                Automatización operador
              </h2>
              <p className="mt-1 text-xs text-[var(--color-muted)]">
                Solo lectura: Gmail → SQLite y SQLite → espejo dashboard.
              </p>
            </>
          ) : (
            <h3 id="automation-health-heading" className="text-base font-semibold text-brand-900">
              Estado de automatización
            </h3>
          )}
        </div>
        {refreshButton}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-3">
        <span
          className={`rounded-md px-3 py-1 text-sm font-bold uppercase tracking-wide ${tone.badge}`}
        >
          {automationVerdictLabel(status.verdict)}
        </span>
        <span className="text-sm text-[var(--color-muted)]">
          {formatAutomationRecommendedActionForPanel(status.recommended_action)}
        </span>
      </div>

      {missingState ? (
        <div
          className="mt-3 space-y-2 rounded-md border border-amber-200 bg-amber-50/90 px-3 py-2 text-xs text-amber-950"
          data-testid="automation-missing-state-help"
        >
          <p className="text-sm font-semibold text-amber-950" data-testid="automation-missing-state-primary">
            {AUTOMATION_MISSING_STATE_PRIMARY}
          </p>
          <p>{AUTOMATION_MISSING_STATE_HELP}</p>
        </div>
      ) : status.source === "postgres_snapshot" && snapshotSummary ? (
        <div
          className="mt-3 space-y-1 rounded-md border border-emerald-200 bg-emerald-50/90 px-3 py-2 text-xs text-emerald-950"
          data-testid="automation-postgres-snapshot"
        >
          <p className="text-sm font-semibold text-emerald-950" data-testid="automation-snapshot-summary">
            {snapshotSummary}
          </p>
        </div>
      ) : snapshotSummary ? (
        <p
          className="mt-3 rounded-md border border-emerald-200 bg-emerald-50/90 px-3 py-2 text-xs text-emerald-950"
          data-testid="automation-snapshot-summary"
        >
          {snapshotSummary}
        </p>
      ) : null}

      {variant === "summary" ? (
        <div
          className={`mt-3 rounded-md border px-3 py-2 ${AUTOMATION_FRESHNESS_TONE_CLASS[freshnessSummary.tone]}`}
          data-testid="automation-freshness-panel"
        >
          <p className="text-sm font-semibold" data-testid="automation-freshness-title">
            {freshnessSummary.title}
          </p>
          <p className="mt-1 text-xs">{freshnessSummary.detail}</p>
          <div className="mt-2 flex flex-wrap gap-2 text-xs">
            <span className="rounded-full border border-current/20 bg-white/50 px-2 py-0.5">
              Gmail comprobación: {freshnessSummary.gmailAgeLabel}
            </span>
            <span className="rounded-full border border-current/20 bg-white/50 px-2 py-0.5">
              {freshnessSummary.mirrorSourceLabel} comprobación:{" "}
              {freshnessSummary.mirrorAgeLabel}
            </span>
            <span className="rounded-full border border-current/20 bg-white/50 px-2 py-0.5">
              Snapshot API: {freshnessSummary.snapshotAgeLabel}
            </span>
          </div>
          <div className="mt-2 flex flex-wrap gap-2 text-xs opacity-90">
            <span className="rounded-full border border-current/15 bg-white/40 px-2 py-0.5">
              Último refresh útil Gmail: {freshnessSummary.gmailMaterialAgeLabel}
            </span>
            <span className="rounded-full border border-current/15 bg-white/40 px-2 py-0.5">
              Última publicación material: {freshnessSummary.mirrorMaterialAgeLabel}
            </span>
          </div>
          {freshnessSummary.loopWarning ? (
            <p
              className="mt-2 text-xs text-amber-900"
              data-testid="automation-freshness-loop-warning"
            >
              {freshnessSummary.loopWarning}
            </p>
          ) : null}
          {freshnessSummary.warning ? (
            <p className="mt-2 text-xs font-medium" data-testid="automation-freshness-warning">
              {freshnessSummary.warning}
            </p>
          ) : null}
        </div>
      ) : null}

      {variant === "summary" ? (
        <div
          className="mt-3 rounded-md border border-[var(--color-border)] bg-white/70 px-3 py-2"
          data-testid="automation-run-summary"
        >
          <p className="text-sm font-semibold text-brand-900">Últimas ejecuciones</p>
          <ul className="mt-2 space-y-2">
            {runSummary.map((row) => (
              <li
                key={row.id}
                className={`rounded-md border px-2.5 py-2 text-xs ${AUTOMATION_RUN_TONE_CLASS[row.tone]}`}
                data-testid={`automation-run-row-${row.id}`}
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-medium">{row.label}</span>
                  <span className="rounded-full border border-current/20 bg-white/60 px-2 py-0.5 font-semibold uppercase tracking-wide">
                    {row.primary}
                  </span>
                </div>
                {row.secondary ? (
                  <p className="mt-1 text-[11px] opacity-90">{row.secondary}</p>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {variant === "summary" ? (
        <ul className="mt-4 grid gap-2 text-sm sm:grid-cols-2">
          <li>
            <span className="text-[var(--color-muted)]">Gmail → SQLite:</span>{" "}
            <span className="font-medium">{mailLoopStatusLabel(status)}</span>
          </li>
          <li>
            <span className="text-[var(--color-muted)]">SQLite → Dashboard:</span>{" "}
            <span className="font-medium">
              {mirrorSynced === true
                ? "sincronizado"
                : mirrorSynced === false
                  ? "atrás"
                  : "desconocido"}
            </span>
          </li>
          <li>
            <span className="text-[var(--color-muted)]">Último refresh Gmail:</span>{" "}
            <span className="font-medium">
              {formatAutomationTimestamp(status.mail_auto_refresh.last_successful_refresh_at)}
            </span>
          </li>
          <li>
            <span className="text-[var(--color-muted)]">Último espejo:</span>{" "}
            <span className="font-medium">
              {formatAutomationTimestamp(status.dashboard_auto_mirror.last_successful_mirror_at)}
            </span>
          </li>
          <li>
            <span className="text-[var(--color-muted)]">INBOX / Sent:</span>{" "}
            <span className="font-medium">
              {status.mail_auto_refresh.last_seen_inbox_total ?? "—"} /{" "}
              {status.mail_auto_refresh.last_seen_sent_total ?? "—"}
            </span>
          </li>
          <li>
            <span className="text-[var(--color-muted)]">Daily-core:</span>{" "}
            <span className="font-medium">
              {formatAutomationTimestamp(status.daily_core.generated_at_utc)}
              {status.daily_core.age_seconds != null
                ? ` (${status.daily_core.age_seconds}s)`
                : ""}
            </span>
          </li>
          {status.dashboard_auto_mirror.cooldown_remaining_seconds > 0 ? (
            <li>
              <span className="text-[var(--color-muted)]">Cooldown espejo:</span>{" "}
              <span className="font-medium">
                {status.dashboard_auto_mirror.cooldown_remaining_seconds}s
              </span>
            </li>
          ) : null}
          {chilecompraSummary ? (
            <li>
              <span className="font-medium">{chilecompraSummary}</span>
            </li>
          ) : null}
        </ul>
      ) : (
        <div className="mt-4 space-y-4 text-sm">
          <dl className="grid gap-2 sm:grid-cols-2">
            <DetailRow
              label="Generado"
              value={formatAutomationTimestamp(status.generated_at_utc)}
            />
            <DetailRow label="Veredicto" value={automationVerdictLabel(status.verdict)} />
            <DetailRow
              label="Acción recomendada"
              value={automationRecommendedActionLabel(status.recommended_action)}
            />
            {activeCurrentDisplay ? (
              <DetailRow label="Directorio activo" value={activeCurrentDisplay} />
            ) : null}
          </dl>

          <div className="rounded-lg border border-[var(--color-border)] bg-white/60 px-3 py-3">
            <h4 className="font-semibold text-brand-900">Daily-core</h4>
            <dl className="mt-2 grid gap-2 sm:grid-cols-2">
              <DetailRow
                label="Manifiesto presente"
                value={formatAutomationBool(status.daily_core.exists)}
              />
              <DetailRow label="Estado" value={status.daily_core.status ?? "—"} />
              <DetailRow
                label="Generado (UTC)"
                value={formatAutomationTimestamp(status.daily_core.generated_at_utc)}
              />
              <DetailRow
                label="Returncode"
                value={
                  status.daily_core.returncode != null ? String(status.daily_core.returncode) : "—"
                }
              />
            </dl>
          </div>

          <div className="rounded-lg border border-[var(--color-border)] bg-white/60 px-3 py-3">
            <h4 className="font-semibold text-brand-900">Mail auto-refresh</h4>
            <dl className="mt-2 grid gap-2 sm:grid-cols-2">
              <DetailRow
                label="Estado presente"
                value={formatAutomationBool(status.mail_auto_refresh.state_exists)}
              />
              <DetailRow
                label="Dirty / pending"
                value={`${formatAutomationBool(status.mail_auto_refresh.dirty)} / ${formatAutomationBool(status.mail_auto_refresh.pending)}`}
              />
              <DetailRow
                label="Último resultado"
                value={status.mail_auto_refresh.last_result ?? "—"}
              />
              <DetailRow
                label="Último refresh exitoso"
                value={formatAutomationTimestamp(
                  status.mail_auto_refresh.last_successful_refresh_at,
                )}
              />
              <DetailRow
                label="INBOX / Sent vistos"
                value={`${status.mail_auto_refresh.last_seen_inbox_total ?? "—"} / ${status.mail_auto_refresh.last_seen_sent_total ?? "—"}`}
              />
            </dl>
          </div>

          <div className="rounded-lg border border-[var(--color-border)] bg-white/60 px-3 py-3">
            <h4 className="font-semibold text-brand-900">Dashboard auto-mirror</h4>
            <dl className="mt-2 grid gap-2 sm:grid-cols-2">
              <DetailRow
                label="Estado presente"
                value={formatAutomationBool(status.dashboard_auto_mirror.state_exists)}
              />
              <DetailRow
                label="Último resultado"
                value={status.dashboard_auto_mirror.last_result ?? "—"}
              />
              <DetailRow
                label="Último espejo exitoso"
                value={formatAutomationTimestamp(
                  status.dashboard_auto_mirror.last_successful_mirror_at,
                )}
              />
              <DetailRow
                label="Espejo al día con daily-core"
                value={formatAutomationBool(status.dashboard_auto_mirror.mirror_matches_daily_core)}
              />
              <DetailRow
                label="Cooldown restante"
                value={`${status.dashboard_auto_mirror.cooldown_remaining_seconds}s`}
              />
            </dl>
          </div>

          <div
            className="rounded-lg border border-[var(--color-border)] bg-white/60 px-3 py-3"
            data-testid="chilecompra-automation-section"
          >
            <h4 className="font-semibold text-brand-900">ChileCompra equipment auto-refresh</h4>
            <dl className="mt-2 grid gap-2 sm:grid-cols-2">
              <DetailRow
                label="Estado presente"
                value={formatAutomationBool(chilecompra.state_exists)}
              />
              <DetailRow
                label="Último resultado"
                value={chilecompraAutomationResultLabel(chilecompra.last_result)}
              />
              <DetailRow
                label="Último refresh exitoso"
                value={formatAutomationTimestamp(chilecompra.last_successful_refresh_at)}
              />
              <DetailRow
                label="Última publicación exitosa"
                value={formatAutomationTimestamp(chilecompra.last_successful_publish_at)}
              />
              <DetailRow
                label="Próxima revisión recomendada"
                value={formatAutomationTimestamp(chilecompra.next_recommended_run_at)}
              />
              <DetailRow
                label="Próxima revisión vencida"
                value={formatAutomationBool(chilecompra.next_run_due)}
              />
              <DetailRow
                label="Filas publicadas"
                value={
                  chilecompra.published_rows != null ? String(chilecompra.published_rows) : "—"
                }
              />
              <DetailRow
                label="Detalles API / cache hits / errores"
                value={formatChilecompraApiDetailSummary(status)}
              />
              <DetailRow
                label="Fallas consecutivas"
                value={String(chilecompra.consecutive_failures)}
              />
              {publishedQueueDisplay ? (
                <DetailRow label="Cola publicada" value={publishedQueueDisplay} />
              ) : null}
              {candidateAuditDisplay ? (
                <DetailRow label="Auditoría de candidatos" value={candidateAuditDisplay} />
              ) : null}
              {status.cron.chilecompra_entry_present !== undefined ? (
                <DetailRow
                  label="Cron instalado"
                  value={formatAutomationBool(status.cron.chilecompra_entry_present)}
                />
              ) : null}
              {status.cron.chilecompra_uses_tracked_script !== undefined ? (
                <DetailRow
                  label="Wrapper correcto"
                  value={formatAutomationBool(status.cron.chilecompra_uses_tracked_script)}
                />
              ) : null}
            </dl>
          </div>

          {cronNote ? (
            <p className="text-xs text-[var(--color-muted)]">
              <span className="font-medium text-brand-900">Cron:</span> {cronNote}
            </p>
          ) : null}
        </div>
      )}

      {showLockOrPause ? (
        <p className="mt-3 text-xs text-amber-900" data-testid="automation-lock-pause-hint">
          {status.mail_auto_refresh.paused || status.dashboard_auto_mirror.paused
            ? "Pausa activa. "
            : ""}
          {status.mail_auto_refresh.lock_live ? "Refresh Gmail en curso. " : ""}
          {status.dashboard_auto_mirror.lock_live ? "Publicación espejo en curso. " : ""}
          {status.chilecompra_equipment_auto_refresh?.lock_live
            ? "Refresh ChileCompra en curso."
            : ""}
        </p>
      ) : null}
    </section>
  );
}
