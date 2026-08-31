import { useEffect, useState } from "react";
import {
  createCustomerQuote,
  fetchCustomerQuotes,
  retryCustomerQuoteDriveWorkspace,
} from "../../api/customerQuoteClient";
import { OperatorApiError } from "../../api/operatorClient";
import type { CustomerQuote } from "../../api/customerQuoteTypes";

const NUMBERING_NOT_CONFIGURED_MESSAGE =
  "La numeración de cotizaciones aún no está activada. Avisa al administrador del sistema.";

const CREATE_ERROR_MESSAGE = "No pudimos crear la cotización. Reintenta.";

const RETRY_CONFLICT_MESSAGE =
  "La cotización cambió en otra sesión. Actualizamos el estado con la versión más reciente.";

const FAILURE_CATEGORY_MESSAGES: Record<string, string> = {
  drive_not_configured: "Google Drive aún no está configurado.",
  drive_credentials_not_configured: "Google Drive aún no está configurado.",
  drive_timeout: "Google Drive tardó demasiado en responder.",
  drive_unavailable: "Google Drive no está disponible en este momento.",
  drive_permission_denied:
    "La cuenta de Drive no tiene permisos suficientes.",
  drive_not_found:
    "No se encontró la carpeta o plantilla configurada en Drive.",
};

function failureCategoryMessage(category: string | null): string {
  if (category && FAILURE_CATEGORY_MESSAGES[category]) {
    return FAILURE_CATEGORY_MESSAGES[category];
  }
  return "Ocurrió un problema con Google Drive.";
}

function newIdempotencyKey(): string {
  const cryptoApi = globalThis.crypto;
  if (!cryptoApi) {
    throw new Error("No se pudo generar una clave segura para la operación.");
  }
  if (typeof cryptoApi.randomUUID === "function") {
    return `quote:${cryptoApi.randomUUID()}`;
  }
  const bytes = new Uint8Array(16);
  cryptoApi.getRandomValues(bytes);
  return `quote:${Array.from(bytes, (value) =>
    value.toString(16).padStart(2, "0"),
  ).join("")}`;
}

function createErrorMessage(reason: unknown): string {
  if (
    reason instanceof OperatorApiError &&
    reason.status === 503 &&
    reason.message.includes("quote_numbering_not_configured")
  ) {
    return NUMBERING_NOT_CONFIGURED_MESSAGE;
  }
  return CREATE_ERROR_MESSAGE;
}

function DriveLink({ href, label }: { href: string; label: string }) {
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

function QuoteWorkspaceStatus({
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

  if (workspace.provisioning_status === "ready") {
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
    // here, not only once the workspace has moved to "failed".
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

export function QuoteWorkspaceSection({
  salesOpportunityId,
}: {
  salesOpportunityId: string;
}) {
  const [quotes, setQuotes] = useState<CustomerQuote[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  // The idempotency key survives a failed HTTP attempt so a manual retry of
  // the same logical create replays instead of allocating a second number.
  const [pendingCreateKey, setPendingCreateKey] = useState<string | null>(null);

  const [retryingQuoteId, setRetryingQuoteId] = useState<string | null>(null);
  const [retryError, setRetryError] = useState<{
    quoteId: string;
    message: string;
  } | null>(null);

  useEffect(() => {
    let cancelled = false;

    setLoaded(false);
    setLoadError(null);

    void fetchCustomerQuotes(salesOpportunityId)
      .then((response) => {
        if (cancelled) return;
        setQuotes(response.items);
        setLoaded(true);
      })
      .catch(() => {
        if (cancelled) return;
        setLoadError("No pudimos cargar las cotizaciones.");
        setLoaded(true);
      });

    return () => {
      cancelled = true;
    };
  }, [salesOpportunityId]);

  function upsertQuote(next: CustomerQuote) {
    setQuotes((current) => {
      const index = current.findIndex(
        (item) => item.quote_id === next.quote_id,
      );
      if (index === -1) return [next, ...current];
      const copy = current.slice();
      copy[index] = next;
      return copy;
    });
  }

  async function handleCreate() {
    if (creating) return;

    setCreating(true);
    setCreateError(null);

    const key = pendingCreateKey ?? newIdempotencyKey();
    setPendingCreateKey(key);

    try {
      const created = await createCustomerQuote(salesOpportunityId, key);
      upsertQuote(created);
      setPendingCreateKey(null);
    } catch (reason: unknown) {
      setCreateError(createErrorMessage(reason));
    } finally {
      setCreating(false);
    }
  }

  async function handleRetry(quote: CustomerQuote) {
    if (retryingQuoteId) return;

    setRetryingQuoteId(quote.quote_id);
    setRetryError(null);

    try {
      const updated = await retryCustomerQuoteDriveWorkspace(quote.quote_id, {
        expected_version: quote.drive_workspace.version,
      });
      upsertQuote(updated);
    } catch (reason: unknown) {
      if (reason instanceof OperatorApiError && reason.status === 409) {
        setRetryError({
          quoteId: quote.quote_id,
          message: RETRY_CONFLICT_MESSAGE,
        });
        try {
          const refreshed = await fetchCustomerQuotes(salesOpportunityId);
          setQuotes(refreshed.items);
        } catch {
          // Keep the current list; the conflict message stands.
        }
      } else {
        setRetryError({
          quoteId: quote.quote_id,
          message:
            "No pudimos reintentar la creación en Drive. Vuelve a intentarlo.",
        });
      }
    } finally {
      setRetryingQuoteId(null);
    }
  }

  return (
    <section className="space-y-3" aria-label="Cotizaciones">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold text-slate-800">Cotizaciones</h3>
        <button
          type="button"
          onClick={() => void handleCreate()}
          disabled={creating}
          className="rounded-md bg-brand-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-brand-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-600 disabled:cursor-not-allowed disabled:opacity-60"
        >
          Nueva cotización
        </button>
      </div>

      <div aria-live="polite">
        {creating ? (
          <p className="text-sm text-slate-600">Creando cotización…</p>
        ) : null}
        {createError ? (
          <p
            role="alert"
            className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900"
          >
            {createError}
          </p>
        ) : null}
      </div>

      {loadError ? (
        <p role="alert" className="text-sm text-amber-900">
          {loadError}
        </p>
      ) : null}

      {loaded && !loadError && quotes.length === 0 && !creating ? (
        <p className="text-sm text-slate-600">
          Aún no hay cotizaciones para esta oportunidad.
        </p>
      ) : null}

      <ul className="space-y-3">
        {quotes.map((quote) => (
          <li
            key={quote.quote_id}
            className="space-y-2 rounded-lg border border-[var(--color-border)] bg-white px-3 py-3"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-sm font-semibold text-slate-900">
                {quote.quote_number}
              </span>
              <span className="text-xs uppercase tracking-wide text-slate-500">
                Borrador · Rev. {quote.latest_revision_number}
              </span>
            </div>

            <QuoteWorkspaceStatus
              quote={quote}
              retryPending={retryingQuoteId === quote.quote_id}
              retryError={
                retryError?.quoteId === quote.quote_id
                  ? retryError.message
                  : null
              }
              onRetry={() => void handleRetry(quote)}
            />

            <details className="text-xs text-slate-500">
              <summary className="cursor-pointer select-none">
                Detalles técnicos
              </summary>
              <dl className="mt-1 space-y-0.5 break-all">
                <div>ID: {quote.quote_id}</div>
                {quote.drive_workspace.folder_id ? (
                  <div>Carpeta: {quote.drive_workspace.folder_id}</div>
                ) : null}
                {quote.drive_workspace.sheet_file_id ? (
                  <div>Planilla: {quote.drive_workspace.sheet_file_id}</div>
                ) : null}
                {quote.drive_workspace.failure_category ? (
                  <div>
                    Categoría de error:{" "}
                    {quote.drive_workspace.failure_category}
                  </div>
                ) : null}
                <div>
                  Intentos de Drive: {quote.drive_workspace.attempt_count}
                </div>
              </dl>
            </details>
          </li>
        ))}
      </ul>
    </section>
  );
}
