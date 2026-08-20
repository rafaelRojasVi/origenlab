import { useRef, useState } from "react";
import type { TenderAnnexPreview, TermFact } from "../../api/institutionIntel/types";
import { institutionIntelAdapter } from "../../api/institutionIntel/adapter";
import { OperatorApiError } from "../../api/operatorClient";
import { LicitacionIntelBody } from "../institutionIntel/LicitacionIntelCard";

const MAX_UPLOAD_BYTES = 64 * 1024 * 1024;

const LOCAL_TENDER_TICKET_CONTRACT_VERSION =
  "local_tender_processing_ticket_v1";

export interface LocalTenderProcessingTicket {
  contract_version: typeof LOCAL_TENDER_TICKET_CONTRACT_VERSION;
  ticket_id: string;
  tender_code: string;
  operator_declared_complete: boolean;
  created_at_utc: string;
}

export function buildLocalTenderProcessingTicket(
  tenderCode: string,
  operatorDeclaredComplete: boolean,
  overrides: {
    ticketId?: string;
    createdAtUtc?: string;
  } = {},
): LocalTenderProcessingTicket {
  const ticketId =
    overrides.ticketId ??
    `local_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;

  return {
    contract_version: LOCAL_TENDER_TICKET_CONTRACT_VERSION,
    ticket_id: ticketId,
    tender_code: tenderCode,
    operator_declared_complete: operatorDeclaredComplete,
    created_at_utc: overrides.createdAtUtc ?? new Date().toISOString(),
  };
}

function downloadLocalProcessingTicket(
  tenderCode: string,
  operatorDeclaredComplete: boolean,
): LocalTenderProcessingTicket {
  const ticket = buildLocalTenderProcessingTicket(
    tenderCode,
    operatorDeclaredComplete,
  );

  const blob = new Blob(
    [`${JSON.stringify(ticket, null, 2)}\n`],
    { type: "application/json" },
  );
  let objectUrl: string;

  try {
    objectUrl = URL.createObjectURL(blob);
  } catch {
    throw new Error("local-ticket-download-failed");
  }

  const anchor = document.createElement("a");

  anchor.href = objectUrl;
  anchor.download = `origenlab-tender-${ticket.ticket_id}.json`;
  anchor.style.display = "none";
  document.body.appendChild(anchor);

  try {
    anchor.click();
  } finally {
    anchor.remove();
    URL.revokeObjectURL(objectUrl);
  }

  return ticket;
}

type PanelPhase = "idle" | "processing" | "error" | "success";
type SavePhase = "idle" | "saving" | "saved" | "error";

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function errorMessageFor(err: unknown): string {
  if (err instanceof OperatorApiError) {
    switch (err.status) {
      case 404:
        return "Esta licitación ya no está entre las oportunidades accionables — no se puede procesar el expediente.";
      case 413:
        return "El archivo ZIP supera el tamaño máximo permitido.";
      case 415:
        return "El archivo debe ser un ZIP válido (application/zip).";
      case 422:
        return "El ZIP fue rechazado: está dañado, vacío, o no es un archivo ZIP válido.";
      case 503:
        return "El sistema de licitaciones accionables no está disponible en este momento — intenta de nuevo en unos minutos.";
      default:
        return "No se pudo procesar el ZIP. Intenta de nuevo.";
    }
  }
  return "No se pudo comunicar con el servidor. Intenta de nuevo.";
}

function navigationErrorMessageFor(err: unknown): string {
  if (err instanceof OperatorApiError) {
    switch (err.status) {
      case 404:
        return "Esta licitación ya no está entre las oportunidades accionables.";
      case 503:
        return "El sistema de licitaciones accionables no está disponible en este momento.";
      default:
        return "No se pudo abrir Mercado Público. Intenta de nuevo.";
    }
  }
  return "No se pudo comunicar con el servidor para abrir Mercado Público.";
}

function factStateCounts(terms: TenderAnnexPreview["licitacionIntel"]["terms"]): Record<TermFact["state"], number> {
  const facts: readonly TermFact[] =
    terms.status === "available" ? terms.data : terms.status === "available_incomplete" ? (terms.partial ?? []) : [];
  const counts: Record<TermFact["state"], number> = {
    explicit: 0,
    derived: 0,
    not_explicitly_found: 0,
    unknown: 0,
    conflicting: 0,
  };
  for (const fact of facts) {
    counts[fact.state] += 1;
  }
  return counts;
}

function ExpedienteSummary({ preview }: { preview: TenderAnnexPreview }) {
  const counts = factStateCounts(preview.licitacionIntel.terms);
  const explicitOrDerived = counts.explicit + counts.derived;
  const coberturaLabel =
    preview.acquisition.completenessState === "complete"
      ? "Completa"
      : preview.acquisition.completenessState === "incomplete"
        ? "Incompleta"
        : "No confirmada";
  const coberturaDetail = preview.acquisition.operatorDeclaredComplete
    ? "Declarada por operador"
    : "Sin declaración de completitud";

  return (
    <div
      className="rounded-lg border border-[var(--color-border)] bg-slate-50/60 px-4 py-3 text-sm"
      data-testid="annex-preview-summary"
    >
      <p className="text-xs font-bold uppercase tracking-wide text-brand-700">Expediente procesado</p>
      <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
        <dt className="text-[var(--color-muted)]">Fuente</dt>
        <dd className="font-medium text-slate-900">Importado por operador</dd>

        <dt className="text-[var(--color-muted)]">Documentos</dt>
        <dd className="font-medium text-slate-900">
          {preview.archive.attachmentsDiscovered} detectados · {preview.archive.attachmentsDownloaded} procesados ·{" "}
          {preview.archive.rejectedEntries.length} rechazados
        </dd>

        <dt className="text-[var(--color-muted)]">Cobertura</dt>
        <dd className="font-medium text-slate-900" data-testid="annex-preview-coverage-label">
          {coberturaLabel} — {coberturaDetail}
        </dd>

        <dt className="text-[var(--color-muted)]">Condiciones detectadas</dt>
        <dd className="font-medium text-slate-900">
          {explicitOrDerived} explícitas · {counts.not_explicitly_found} no encontradas · {counts.conflicting}{" "}
          conflictivas
        </dd>
      </dl>
      {preview.archive.rejectedEntries.length > 0 ? (
        <div className="mt-2" data-testid="annex-preview-rejected-entries">
          <p className="text-[11px] font-semibold text-amber-900">Entradas rechazadas del ZIP:</p>
          <ul className="mt-1 list-disc pl-4 text-[11px] text-amber-900">
            {preview.archive.rejectedEntries.map((entry) => (
              <li key={entry}>{entry}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

export function TenderAnnexUploadPanel({
  tenderCode,
  persistedOperatorImport = false,
  onPersisted,
}: {
  tenderCode: string;
  persistedOperatorImport?: boolean;
  onPersisted?: () => void;
}) {
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [declareComplete, setDeclareComplete] = useState(false);
  const [phase, setPhase] = useState<PanelPhase>("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [preview, setPreview] = useState<TenderAnnexPreview | null>(null);
  const [savePhase, setSavePhase] = useState<SavePhase>("idle");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [clientError, setClientError] = useState<string | null>(null);
  const [isDragOver, setIsDragOver] = useState(false);
  const [isResolvingNavigation, setIsResolvingNavigation] = useState(false);
  const [navigationError, setNavigationError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  function acceptFile(file: File) {
    if (!file.name.toLowerCase().endsWith(".zip")) {
      setClientError("Solo se aceptan archivos .zip.");
      setSelectedFile(null);
      return;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      setClientError(`El archivo supera el tamaño máximo permitido (${formatBytes(MAX_UPLOAD_BYTES)}).`);
      setSelectedFile(null);
      return;
    }
    setClientError(null);
    setSelectedFile(file);
    setPhase("idle");
    setPreview(null);
    setErrorMessage(null);
    setSavePhase("idle");
    setSaveError(null);
  }

  function handleDrop(event: React.DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setIsDragOver(false);
    const file = event.dataTransfer.files?.[0];
    if (file) acceptFile(file);
  }

  function handleFileInputChange(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (file) acceptFile(file);
  }

  function removeSelection() {
    setSelectedFile(null);
    setClientError(null);
    setPhase("idle");
    setPreview(null);
    setErrorMessage(null);
    setSavePhase("idle");
    setSaveError(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  async function startLocalProcessing() {
    const popup = window.open("about:blank", "_blank");

    if (!popup) {
      setNavigationError(
        "El navegador bloqueó la nueva pestaña. Permite ventanas emergentes para OrigenLab e intenta de nuevo.",
      );
      return;
    }

    popup.opener = null;
    setIsResolvingNavigation(true);
    setNavigationError(null);

    try {
      const destination =
        await institutionIntelAdapter.getTenderAttachmentNavigation(tenderCode);

      // Resolve the gated destination before creating the local-worker ticket.
      // This prevents a failed navigation request from leaving an orphan ticket
      // that could later claim an unrelated Licitaciones ZIP.
      downloadLocalProcessingTicket(tenderCode, declareComplete);

      popup.location.replace(destination.url);
    } catch (err) {
      popup.close();

      if (err instanceof Error && err.message === "local-ticket-download-failed") {
        setNavigationError(
          "No se pudo descargar el ticket para OriginLab Local. Intenta de nuevo.",
        );
      } else {
        setNavigationError(navigationErrorMessageFor(err));
      }
    } finally {
      setIsResolvingNavigation(false);
    }
  }

  async function openMercadoPublicoAttachments() {
    const popup = window.open("about:blank", "_blank");
    if (!popup) {
      setNavigationError(
        "El navegador bloqueó la nueva pestaña. Permite ventanas emergentes para OrigenLab e intenta de nuevo.",
      );
      return;
    }

    popup.opener = null;
    setIsResolvingNavigation(true);
    setNavigationError(null);

    try {
      const destination =
        await institutionIntelAdapter.getTenderAttachmentNavigation(tenderCode);
      popup.location.replace(destination.url);
    } catch (err) {
      popup.close();
      setNavigationError(navigationErrorMessageFor(err));
    } finally {
      setIsResolvingNavigation(false);
    }
  }

  async function processDocuments() {
    if (!selectedFile) return;
    setPhase("processing");
    setErrorMessage(null);
    try {
      const result = await institutionIntelAdapter.previewTenderAnnexBundle(tenderCode, selectedFile, {
        declareComplete,
      });
      setPreview(result);
      setPhase("success");
    } catch (err) {
      setErrorMessage(errorMessageFor(err));
      setPhase("error");
    }
  }

  async function saveDocuments() {
    if (!selectedFile || !preview) return;

    setSavePhase("saving");
    setSaveError(null);

    try {
      await institutionIntelAdapter.importTenderAnnexBundle(
        tenderCode,
        selectedFile,
        { declareComplete },
      );
      setSavePhase("saved");
      onPersisted?.();
    } catch (err) {
      setSaveError(errorMessageFor(err));
      setSavePhase("error");
    }
  }

  return (
    <section
      className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] px-5 py-4"
      data-testid="tender-annex-upload-panel"
    >
      <div data-testid="attachment-navigation-section">
        <h3 className="text-sm font-semibold text-slate-900">
          Procesar expediente en mi PC
        </h3>

        <p className="mt-1 text-xs text-[var(--color-muted)]">
          OrigenLab descargará un pequeño ticket de trabajo y abrirá Mercado Público.
          Después sólo tienes que descargar el ZIP de la licitación.
        </p>

        <label className="mt-3 flex items-start gap-2 text-xs text-slate-800">
          <input
            type="checkbox"
            checked={declareComplete}
            onChange={(e) => setDeclareComplete(e.target.checked)}
            data-testid="annex-declare-complete-checkbox"
            className="mt-0.5"
          />
          <span>
            Declaro que descargaré el ZIP con todos los documentos disponibles para esta
            licitación en Mercado Público.
          </span>
        </label>

        <p className="mt-1 pl-6 text-[11px] text-[var(--color-muted)]">
          Si no marcas esta opción, OrigenLab procesará el expediente pero mantendrá la
          cobertura como no confirmada.
        </p>

        <button
          type="button"
          className="mt-3 rounded-md bg-brand-700 px-4 py-2 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50"
          onClick={startLocalProcessing}
          disabled={isResolvingNavigation}
          data-testid="local-processing-button"
        >
          {isResolvingNavigation
            ? "Preparando…"
            : "Procesar en mi PC"}
        </button>

        <p
          className="mt-2 text-[11px] text-[var(--color-muted)]"
          data-testid="local-processing-safety-note"
        >
          El ZIP queda en tu computador. OCR y extracción se ejecutan localmente;
          sólo el resultado estructurado se envía a OrigenLab.
        </p>

        {navigationError ? (
          <p
            className="mt-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-[11px] font-medium text-red-900"
            role="alert"
            data-testid="attachment-navigation-error"
          >
            {navigationError}
          </p>
        ) : null}

        <ol className="mt-3 list-decimal space-y-1 pl-4 text-[11px] text-[var(--color-muted)]">
          <li>Haz clic en “Procesar en mi PC”.</li>
          <li>En Mercado Público descarga el archivo Licitaciones_*.zip.</li>
          <li>OriginLab Local lo detectará, procesará y guardará el resultado automáticamente.</li>
        </ol>

        <div className="mt-3 border-t border-[var(--color-border)] pt-3">
          <p className="text-[11px] text-[var(--color-muted)]">
            Alternativa manual:
          </p>

          <button
            type="button"
            className="mt-2 rounded-md border border-brand-700 bg-white px-3 py-1.5 text-[11px] font-semibold text-brand-700 disabled:cursor-not-allowed disabled:opacity-50"
            onClick={openMercadoPublicoAttachments}
            disabled={isResolvingNavigation}
            data-testid="attachment-navigation-button"
          >
            ↗ Sólo abrir Mercado Público
          </button>
        </div>
      </div>

      <div className="my-4 border-t border-[var(--color-border)]" />

      <h3 className="text-sm font-semibold text-slate-900">
        Importación manual de respaldo
      </h3>
      <p className="mt-0.5 text-xs text-[var(--color-muted)]">
        Usa esta opción sólo si OriginLab Local no está disponible.
      </p>

      {persistedOperatorImport ? (
        <div
          className="mt-3 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2"
          data-testid="annex-persisted-banner"
        >
          <p className="text-xs font-bold uppercase tracking-wide text-emerald-900">
            Expediente guardado
          </p>
          <p className="mt-1 text-[11px] text-emerald-900">
            OrigenLab está leyendo los términos de esta licitación desde el expediente
            documental guardado por el operador. Puedes cargar un ZIP nuevo para reemplazarlo.
          </p>
        </div>
      ) : null}

      <p className="mt-2 text-xs text-[var(--color-muted)]">
        Carga el ZIP descargado desde Mercado Público para este mismo código de licitación.
      </p>

      <div
        className={`mt-3 rounded-lg border-2 border-dashed px-4 py-6 text-center text-xs transition-colors ${
          isDragOver ? "border-brand-500 bg-brand-50" : "border-[var(--color-border)]"
        }`}
        data-testid="annex-drop-zone"
        onDragOver={(e) => {
          e.preventDefault();
          setIsDragOver(true);
        }}
        onDragLeave={() => setIsDragOver(false)}
        onDrop={handleDrop}
      >
        {selectedFile ? (
          <div data-testid="annex-selected-file">
            <p className="font-medium text-slate-900">{selectedFile.name}</p>
            <p className="mt-0.5 text-[var(--color-muted)]">{formatBytes(selectedFile.size)}</p>
            <button
              type="button"
              className="mt-2 text-[11px] font-semibold text-brand-700 underline"
              onClick={removeSelection}
              data-testid="annex-remove-file"
            >
              Quitar
            </button>
          </div>
        ) : (
          <div>
            <p className="text-[var(--color-muted)]">Arrastra aquí el ZIP descargado desde Mercado Público</p>
            <button
              type="button"
              className="mt-2 rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-[11px] font-semibold text-slate-700"
              onClick={() => fileInputRef.current?.click()}
              data-testid="annex-choose-file"
            >
              Elegir archivo
            </button>
          </div>
        )}
        <input
          ref={fileInputRef}
          type="file"
          accept=".zip"
          className="hidden"
          onChange={handleFileInputChange}
          data-testid="annex-file-input"
        />
      </div>

      {clientError ? (
        <p className="mt-2 text-[11px] font-medium text-red-700" role="alert" data-testid="annex-client-error">
          {clientError}
        </p>
      ) : null}

      <button
        type="button"
        className="mt-3 rounded-md bg-brand-700 px-4 py-2 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40"
        disabled={!selectedFile || phase === "processing"}
        onClick={processDocuments}
        data-testid="annex-process-button"
      >
        {phase === "processing" ? "Procesando…" : "Procesar documentos"}
      </button>

      {phase === "error" && errorMessage ? (
        <p
          className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-[11px] font-medium text-red-900"
          role="alert"
          data-testid="annex-server-error"
        >
          {errorMessage}
        </p>
      ) : null}

      {phase === "success" && preview ? (
        <div className="mt-4 space-y-3" data-testid="annex-preview-result">
          <div
            className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2"
            data-testid="annex-preview-banner"
          >
            <p className="text-xs font-bold uppercase tracking-wide text-amber-900">
              Vista previa — aún no guardada
            </p>
            <p className="mt-1 text-[11px] text-amber-900">
              Los documentos fueron procesados para revisión. Guarda el expediente
              explícitamente para que permanezca disponible después de actualizar la página.
            </p>
          </div>

          <button
            type="button"
            className="rounded-md bg-emerald-700 px-4 py-2 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40"
            disabled={savePhase === "saving"}
            onClick={saveDocuments}
            data-testid="annex-save-button"
          >
            {savePhase === "saving"
              ? "Guardando…"
              : savePhase === "saved"
                ? "Expediente guardado"
                : "Guardar expediente"}
          </button>

          {savePhase === "error" && saveError ? (
            <p
              className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-[11px] font-medium text-red-900"
              role="alert"
              data-testid="annex-save-error"
            >
              {saveError}
            </p>
          ) : null}

          <ExpedienteSummary preview={preview} />
          <LicitacionIntelBody intel={preview.licitacionIntel} />
        </div>
      ) : null}
    </section>
  );
}
