import type { DailyCoreRunStatus } from "../../api/operatorTypes";

const EMPTY_DAILY_CORE_RUN: DailyCoreRunStatus = { exists: false };

export function DailyCoreRunNote({
  dailyCoreRun,
  showSafetyNote = true,
}: {
  dailyCoreRun?: DailyCoreRunStatus | null;
  showSafetyNote?: boolean;
}) {
  const run = dailyCoreRun ?? EMPTY_DAILY_CORE_RUN;
  return (
    <div
      className="mt-4 rounded-lg border border-slate-200 bg-slate-50/80 px-4 py-3"
      data-testid="daily-core-run-note"
    >
      <h3 className="text-sm font-semibold text-slate-900">Última ejecución daily-core</h3>
      {showSafetyNote ? (
        <p className="mt-1 text-xs text-[var(--color-muted)]">
          Solo visibilidad operativa. No aprueba envíos.
        </p>
      ) : null}
      {!run.exists ? (
        <p className="mt-2 text-sm text-slate-800">Sin ejecución registrada todavía.</p>
      ) : run.parse_error ? (
        <p className="mt-2 text-sm text-amber-900">
          Manifest no legible; revisar status en CLI.
        </p>
      ) : run.loaded ? (
        <ul className="mt-2 space-y-1 text-sm text-slate-800">
          <li>
            <span className="text-[var(--color-muted)]">Estado:</span>{" "}
            <span className="font-medium">{run.status ?? "n/a"}</span>
          </li>
          <li>
            <span className="text-[var(--color-muted)]">Pasos:</span>{" "}
            <span className="font-medium">{run.step_count ?? "n/a"}</span>
          </li>
          <li>
            <span className="text-[var(--color-muted)]">Código:</span>{" "}
            <span className="font-medium">{run.returncode ?? "n/a"}</span>
          </li>
          <li>
            <span className="text-[var(--color-muted)]">Generado:</span>{" "}
            <span className="font-medium">{run.generated_at_utc ?? "n/a"}</span>
          </li>
          <li>
            <span className="text-[var(--color-muted)]">Espejo Postgres:</span>{" "}
            <span className="font-medium">{run.postgres_mirror ?? "n/a"}</span>
          </li>
          <li>
            <span className="text-[var(--color-muted)]">Aprobación de envío:</span>{" "}
            <span className="font-medium">{run.send_approval ? "Sí" : "No"}</span>
          </li>
        </ul>
      ) : (
        <p className="mt-2 text-sm text-slate-800">Sin ejecución registrada todavía.</p>
      )}
    </div>
  );
}
