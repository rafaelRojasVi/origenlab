import { OperatorApiError } from "../../api/operatorClient";
import type { CustomerQuote } from "../../api/customerQuoteTypes";

export const NUMBERING_NOT_CONFIGURED_MESSAGE =
  "La numeración de cotizaciones aún no está activada. Avisa al administrador del sistema.";

const CREATE_ERROR_MESSAGE = "No pudimos crear la cotización. Reintenta.";

export const FAILURE_CATEGORY_MESSAGES: Record<string, string> = {
  drive_not_configured: "Google Drive aún no está configurado.",
  drive_credentials_not_configured: "Google Drive aún no está configurado.",
  drive_timeout: "Google Drive tardó demasiado en responder.",
  drive_unavailable: "Google Drive no está disponible en este momento.",
  drive_permission_denied:
    "La cuenta de Drive no tiene permisos suficientes.",
  drive_not_found:
    "No se encontró la carpeta o plantilla configurada en Drive.",
  drive_credentials_invalid:
    "Las credenciales de Google Drive no son válidas. Avisa al administrador del sistema.",
};

export function failureCategoryMessage(category: string | null): string {
  if (category && FAILURE_CATEGORY_MESSAGES[category]) {
    return FAILURE_CATEGORY_MESSAGES[category];
  }
  return "Ocurrió un problema con Google Drive.";
}

export function createErrorMessage(reason: unknown): string {
  if (
    reason instanceof OperatorApiError &&
    reason.status === 503 &&
    reason.message.includes("quote_numbering_not_configured")
  ) {
    return NUMBERING_NOT_CONFIGURED_MESSAGE;
  }
  return CREATE_ERROR_MESSAGE;
}

export function DriveLink({ href, label }: { href: string; label: string }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm font-medium text-brand-700 hover:bg-brand-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-600"
    >
      {label}
    </a>
  );
}

export function QuoteWorkspaceStatus({
  quote,
  retryPending,
  retryError,
  onRetry,
}: {
  quote: CustomerQuote;
  retryPending: boolean;
  retryError: string | null;
  onRetry: () => void;
}) {
  const workspace = quote.drive_workspace;

  if (
    workspace.provisioning_status === "ready" ||
    workspace.provisioning_status === "folder_ready"
  ) {
    return (
      <div className="flex flex-wrap gap-2">
        {workspace.folder_web_url ? (
          <DriveLink
            href={workspace.folder_web_url}
            label="Abrir carpeta en Drive"
          />
        ) : null}
        {workspace.sheet_web_url ? (
          <DriveLink
            href={workspace.sheet_web_url}
            label="Abrir plantilla de cotización"
          />
        ) : null}
      </div>
    );
  }

  if (workspace.provisioning_status === "pending") {
    // A process crash (or a lost response) between the durable commit and
    // Drive completion can leave the workspace pending indefinitely with no
    // automatic recovery: an explicit retry action must always be reachable
    // here, not only once the workspace has moved to "failed" -- but only
    // once the server confirms no attempt actively owns it (retryable):
    // offering a retry while an attempt is in flight would only conflict.
    if (!workspace.retryable) {
      return (
        <p className="text-sm text-slate-600" role="status">
          Preparando carpeta en Drive… ya hay un intento en curso, intenta
          de nuevo en unos minutos.
        </p>
      );
    }

    return (
      <div className="space-y-2">
        <p className="text-sm text-slate-600" role="status">
          Preparando carpeta en Drive…
        </p>
        {retryError ? (
          <p className="text-sm text-amber-900" role="alert">
            {retryError}
          </p>
        ) : null}
        <button
          type="button"
          onClick={onRetry}
          disabled={retryPending}
          className="rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-600 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {retryPending
            ? "Reintentando…"
            : "Reintentar creación en Drive"}
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2">
      <p className="text-sm font-medium text-amber-900" role="alert">
        La cotización se guardó, pero la carpeta en Drive no se pudo crear.
      </p>
      <p className="text-sm text-amber-800">
        {failureCategoryMessage(workspace.failure_category)}
      </p>
      {workspace.folder_web_url ? (
        <DriveLink
          href={workspace.folder_web_url}
          label="Abrir carpeta en Drive"
        />
      ) : null}
      {retryError ? (
        <p className="text-sm text-amber-900" role="alert">
          {retryError}
        </p>
      ) : null}
      <button
        type="button"
        onClick={onRetry}
        disabled={retryPending}
        className="rounded-md bg-amber-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-amber-700 disabled:cursor-not-allowed disabled:opacity-60"
      >
        {retryPending ? "Reintentando…" : "Reintentar creación en Drive"}
      </button>
    </div>
  );
}
