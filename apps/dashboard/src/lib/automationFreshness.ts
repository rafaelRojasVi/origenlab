import type { OperatorAutomationStatus } from "../api/operatorTypes";

export type FreshnessTone = "fresh" | "warning" | "stale" | "unknown";

export type MirrorSourceLabel = "Espejo Postgres" | "Loop auto-mirror";

export type MirrorProofKind = "postgres_success" | "parity_check" | "material";

export type AutomationFreshnessSummary = {
  tone: FreshnessTone;
  title: string;
  detail: string;
  /** Age of the latest successful Gmail check (or useful refresh fallback). */
  gmailAgeLabel: string;
  /** Age of the strongest mirror proof-of-currentness. */
  mirrorAgeLabel: string;
  mirrorSourceLabel: MirrorSourceLabel;
  /** Selected mirror freshness proof kind (internal rendering/tests). */
  mirrorProofKind: MirrorProofKind | null;
  snapshotAgeLabel: string;
  /** Informational: last material Gmail useful refresh, may be older than the check. */
  gmailMaterialAgeLabel: string;
  /** Informational: newest successful material mirror publication. */
  mirrorMaterialAgeLabel: string;
  warning: string | null;
  loopWarning: string | null;
};

export const AUTOMATION_FRESHNESS_TONE_CLASS: Record<FreshnessTone, string> = {
  fresh: "border-emerald-200 bg-emerald-50/90 text-emerald-950",
  warning: "border-amber-200 bg-amber-50/90 text-amber-950",
  stale: "border-red-200 bg-red-50/90 text-red-950",
  unknown: "border-slate-200 bg-slate-50/90 text-slate-800",
};

const GMAIL_FRESH_MS = 10 * 60 * 1000;
const MIRROR_FRESH_MS = 20 * 60 * 1000;
const SNAPSHOT_FRESH_MS = 30 * 60 * 1000;
const MS_PER_MINUTE = 60 * 1000;

const GMAIL_HEALTHY_CHECK_RESULTS = new Set(["no_change", "success", "refreshed"]);
const MIRROR_HEALTHY_CHECK_RESULTS = new Set(["already_mirrored", "success"]);
const POSTGRES_TERMINAL_FAILURE = new Set(["failed", "error", "mirror_failed"]);

function parseTimestamp(value: string | null | undefined): number | null {
  const trimmed = value?.trim();
  if (!trimmed) {
    return null;
  }
  const parsed = Date.parse(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

export function formatAutomationFreshnessAgeLabel(ageMs: number | null): string {
  if (ageMs == null || ageMs < 0) {
    return "sin dato";
  }
  const minutes = Math.floor(ageMs / MS_PER_MINUTE);
  if (minutes < 60) {
    return `hace ${minutes} min`;
  }
  const hours = Math.floor(minutes / 60);
  return `hace ${hours} h`;
}

function resolveSnapshotTimestamp(status: OperatorAutomationStatus): number | null {
  return (
    parseTimestamp(status.snapshot_updated_at) ?? parseTimestamp(status.generated_at_utc)
  );
}

function normalizeResult(value: string | null | undefined): string {
  return (value ?? "").trim().toLowerCase();
}

/** Recent successful poll that confirmed Gmail/SQLite currentness without requiring new mail. */
function resolveGmailCheckTimestamp(status: OperatorAutomationStatus): number | null {
  const mail = status.mail_auto_refresh;
  if (mail.consecutive_failures > 0) {
    return null;
  }
  if (mail.dirty || mail.pending || mail.paused || mail.lock_live) {
    return null;
  }
  if (!GMAIL_HEALTHY_CHECK_RESULTS.has(normalizeResult(mail.last_result))) {
    return null;
  }
  return parseTimestamp(mail.last_run_finished_at);
}

function resolveGmailMaterialTimestamp(status: OperatorAutomationStatus): number | null {
  return parseTimestamp(status.mail_auto_refresh.last_successful_refresh_at);
}

/**
 * Strongest Gmail freshness proof: recent healthy check, else material useful refresh.
 */
function resolveGmailFreshnessTimestamp(status: OperatorAutomationStatus): number | null {
  return resolveGmailCheckTimestamp(status) ?? resolveGmailMaterialTimestamp(status);
}

type MirrorProof = {
  ts: number;
  source: MirrorSourceLabel;
  kind: MirrorProofKind;
};

/**
 * Newest successful material publication among Postgres success and local applied mirror.
 * Failed/running Postgres timestamps and parity-check finished_at never count.
 */
export function resolveMirrorMaterialTimestamp(
  status: OperatorAutomationStatus,
): number | null {
  const candidates: number[] = [];
  if (normalizeResult(status.dashboard_mirror_sync?.status) === "success") {
    const finishedAt = parseTimestamp(status.dashboard_mirror_sync?.finished_at);
    if (finishedAt != null) {
      candidates.push(finishedAt);
    }
  }
  const localApplied = parseTimestamp(status.dashboard_auto_mirror.last_successful_mirror_at);
  if (localApplied != null) {
    candidates.push(localApplied);
  }
  if (!candidates.length) {
    return null;
  }
  return Math.max(...candidates);
}

function collectMirrorProofs(status: OperatorAutomationStatus): MirrorProof[] {
  const proofs: MirrorProof[] = [];
  const sync = status.dashboard_mirror_sync;
  // Only terminal success counts as a freshness proof candidate.
  if (normalizeResult(sync?.status) === "success") {
    const finishedAt = parseTimestamp(sync?.finished_at);
    if (finishedAt != null) {
      proofs.push({
        ts: finishedAt,
        source: "Espejo Postgres",
        kind: "postgres_success",
      });
    }
  }

  const loop = status.dashboard_auto_mirror;
  const canUseParityCheck =
    loop.consecutive_failures === 0 &&
    !loop.paused &&
    !loop.lock_live &&
    loop.mirror_matches_daily_core === true &&
    MIRROR_HEALTHY_CHECK_RESULTS.has(normalizeResult(loop.last_result));

  if (canUseParityCheck) {
    const finishedAt = parseTimestamp(loop.last_run_finished_at);
    if (finishedAt != null) {
      proofs.push({
        ts: finishedAt,
        source: "Loop auto-mirror",
        kind: "parity_check",
      });
    }
  }

  const materialTs = resolveMirrorMaterialTimestamp(status);
  if (
    materialTs != null &&
    loop.consecutive_failures === 0 &&
    !loop.paused &&
    !loop.lock_live
  ) {
    proofs.push({
      ts: materialTs,
      source:
        normalizeResult(sync?.status) === "success" &&
        parseTimestamp(sync?.finished_at) === materialTs
          ? "Espejo Postgres"
          : "Loop auto-mirror",
      kind: "material",
    });
  }

  return proofs;
}

function resolveMirrorFreshness(status: OperatorAutomationStatus): {
  freshnessTs: number | null;
  materialTs: number | null;
  source: MirrorSourceLabel;
  proofKind: MirrorProofKind | null;
} {
  const materialTs = resolveMirrorMaterialTimestamp(status);
  const proofs = collectMirrorProofs(status);
  if (!proofs.length) {
    return {
      freshnessTs: null,
      materialTs,
      source: "Loop auto-mirror",
      proofKind: null,
    };
  }
  const best = proofs.reduce((a, b) => (a.ts >= b.ts ? a : b));
  return {
    freshnessTs: best.ts,
    materialTs,
    source: best.source,
    proofKind: best.kind,
  };
}

function buildLoopWarning(
  status: OperatorAutomationStatus,
  mirrorSource: MirrorSourceLabel,
  mirrorFresh: boolean,
): string | null {
  if (mirrorSource !== "Espejo Postgres" || !mirrorFresh) {
    return null;
  }
  const loop = status.dashboard_auto_mirror;
  const hints: string[] = [];
  if (loop.last_result === "mail_dirty") {
    hints.push("El loop auto-mirror está esperando cambios de correo");
  }
  if (loop.mirror_matches_daily_core === false) {
    hints.push("el loop auto-mirror no coincide con daily-core");
  }
  if (!hints.length) {
    return null;
  }
  return `${hints.join("; ")}; el espejo Postgres ya fue actualizado manualmente.`;
}

function freshDetailForProof(proofKind: MirrorProofKind): string {
  if (proofKind === "parity_check") {
    return "No hay cambios pendientes; las comprobaciones recientes confirmaron que el espejo coincide con daily-core.";
  }
  if (proofKind === "postgres_success") {
    return "La última publicación exitosa del espejo Postgres y el snapshot API están dentro de los umbrales esperados.";
  }
  return "La publicación del espejo y el snapshot API están dentro de los umbrales esperados.";
}

export function buildAutomationFreshnessSummary(
  status: OperatorAutomationStatus,
  options?: { now?: Date },
): AutomationFreshnessSummary {
  const nowMs = (options?.now ?? new Date()).getTime();
  const postgresStatus = normalizeResult(status.dashboard_mirror_sync?.status);

  const gmailMaterialTs = resolveGmailMaterialTimestamp(status);
  const gmailTs = resolveGmailFreshnessTimestamp(status);
  const {
    freshnessTs: mirrorTs,
    materialTs: mirrorMaterialTs,
    source: mirrorSourceLabel,
    proofKind: mirrorProofKind,
  } = resolveMirrorFreshness(status);
  const snapshotTs = resolveSnapshotTimestamp(status);

  const gmailAgeMs = gmailTs != null ? nowMs - gmailTs : null;
  const mirrorAgeMs = mirrorTs != null ? nowMs - mirrorTs : null;
  const snapshotAgeMs = snapshotTs != null ? nowMs - snapshotTs : null;
  const gmailMaterialAgeMs = gmailMaterialTs != null ? nowMs - gmailMaterialTs : null;
  const mirrorMaterialAgeMs = mirrorMaterialTs != null ? nowMs - mirrorMaterialTs : null;

  const base = {
    gmailAgeLabel: formatAutomationFreshnessAgeLabel(gmailAgeMs),
    mirrorAgeLabel: formatAutomationFreshnessAgeLabel(mirrorAgeMs),
    mirrorSourceLabel,
    mirrorProofKind,
    snapshotAgeLabel: formatAutomationFreshnessAgeLabel(snapshotAgeMs),
    gmailMaterialAgeLabel: formatAutomationFreshnessAgeLabel(gmailMaterialAgeMs),
    mirrorMaterialAgeLabel: formatAutomationFreshnessAgeLabel(mirrorMaterialAgeMs),
  };

  // Precedence: unknown core → snapshot_stale → consecutive failures → dirty/pending →
  // parity false → pause/lock → Postgres failed → Postgres running → age thresholds → fresh.
  const coreMissing = gmailTs == null || mirrorTs == null || snapshotTs == null;
  if (coreMissing) {
    return {
      ...base,
      tone: "unknown",
      title: "Frescura desconocida",
      detail: "Faltan marcas de tiempo para confirmar si los datos están al día.",
      warning: "No se pudo confirmar frescura completa.",
      loopWarning: null,
    };
  }

  const gmailFresh = gmailAgeMs! <= GMAIL_FRESH_MS;
  const mirrorFresh = mirrorAgeMs! <= MIRROR_FRESH_MS;
  const snapshotFresh = snapshotAgeMs! <= SNAPSHOT_FRESH_MS;
  const loopWarning = buildLoopWarning(status, mirrorSourceLabel, mirrorFresh);
  const mail = status.mail_auto_refresh;
  const mirror = status.dashboard_auto_mirror;

  if (status.snapshot_stale === true) {
    return {
      ...base,
      tone: "stale",
      title: "Datos desactualizados",
      detail: "El snapshot del API indica datos viejos para el dashboard.",
      warning: "Dashboard puede estar desactualizado.",
      loopWarning,
    };
  }

  if (mail.consecutive_failures > 0 || mirror.consecutive_failures > 0) {
    return {
      ...base,
      tone: "stale",
      title: "Automatización con fallos",
      detail: "Hay fallos consecutivos; una comprobación reciente no basta para marcar frescura.",
      warning: "Revisar logs de automatización.",
      loopWarning: null,
    };
  }

  if (mail.dirty || mail.pending) {
    return {
      ...base,
      tone: "warning",
      title: "Correo pendiente de procesar",
      detail:
        "Hay correo dirty/pending; las comprobaciones no confirman un espejo al día.",
      warning: null,
      loopWarning,
    };
  }

  if (mirror.mirror_matches_daily_core === false) {
    return {
      ...base,
      tone: "stale",
      title: "Espejo desalineado con daily-core",
      detail:
        "El loop auto-mirror no coincide con daily-core; se requiere atención aunque haya corridas recientes.",
      warning: null,
      loopWarning: null,
    };
  }

  if (mail.paused || mail.lock_live || mirror.paused || mirror.lock_live) {
    return {
      ...base,
      tone: "warning",
      title: "Automatización en pausa o con lock",
      detail: "Hay pausa o lock activo; no se puede tratar el estado como al día.",
      warning: null,
      loopWarning,
    };
  }

  // Latest Postgres lifecycle remains visible even when a newer parity check exists.
  // `missing_table` is intentionally not treated as a failed publication.
  if (POSTGRES_TERMINAL_FAILURE.has(postgresStatus)) {
    return {
      ...base,
      tone: "stale",
      title: "Último sync Postgres falló",
      detail: "El último intento de publicar el espejo Postgres terminó con error.",
      warning: "Revisar el sync Postgres antes de considerar el dashboard al día.",
      loopWarning: null,
    };
  }

  if (postgresStatus === "running") {
    return {
      ...base,
      tone: "warning",
      title: "Sync Postgres en curso",
      detail: "La publicación del espejo Postgres todavía no ha terminado.",
      warning: null,
      loopWarning,
    };
  }

  if (!mirrorFresh) {
    const staleTitle =
      mirrorSourceLabel === "Espejo Postgres"
        ? "Espejo Postgres desactualizado"
        : "Loop auto-mirror desactualizado";
    const staleDetail =
      mirrorSourceLabel === "Espejo Postgres"
        ? "No hay una comprobación exitosa reciente del espejo Postgres ni del loop auto-mirror."
        : "No hay una comprobación exitosa reciente del loop SQLite → Dashboard.";
    return {
      ...base,
      tone: "stale",
      title: staleTitle,
      detail: staleDetail,
      warning: null,
      loopWarning: null,
    };
  }

  if (!gmailFresh) {
    return {
      ...base,
      tone: "warning",
      title: "Gmail/SQLite con retraso",
      detail:
        mirrorSourceLabel === "Espejo Postgres"
          ? "Gmail → SQLite no tiene una comprobación exitosa reciente, aunque el espejo Postgres sigue reciente."
          : "Gmail → SQLite no tiene una comprobación exitosa reciente, aunque el loop auto-mirror sigue reciente.",
      warning: null,
      loopWarning,
    };
  }

  if (!snapshotFresh) {
    return {
      ...base,
      tone: "stale",
      title: "Snapshot API con retraso",
      detail: "El snapshot del API supera el umbral de frescura esperado.",
      warning: "Dashboard puede estar desactualizado.",
      loopWarning,
    };
  }

  return {
    ...base,
    tone: "fresh",
    title: "Automatización al día",
    detail: freshDetailForProof(mirrorProofKind ?? "material"),
    warning: null,
    loopWarning,
  };
}
