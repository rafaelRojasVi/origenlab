import { useId, useRef, useState, type ChangeEvent } from "react";
import { Spinner } from "../ui";
import {
  MAX_ANNEX_FILES,
  MAX_ANNEX_TOTAL_BYTES,
  attachTenderAnnexes,
  newTenderCommandKey,
  type AnnexUploadResult,
} from "./tenderApi";
import { SKIP_GENERIC, SKIP_REASON_ES, definitive, labelOf, refusalText } from "./tenderLabels";

/** What the API accepts (it judges by the bytes; this only steers the file picker). */
export const ANNEX_ACCEPT = ".pdf,.docx,.xlsx,.doc,.xls,.jpg,.png,.zip";

/**
 * «Subir anexos»: several files, or a zip, in one request. The answer lists what was attached and
 * what was not, each with its reason in Spanish. The same selection retried keeps its key; a new
 * selection, or a definitive refusal, takes a fresh one.
 */
export function AnnexUpload({ tenderId, onUploaded }: { tenderId: string; onUploaded: () => void }) {
  const inputId = useId();
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [result, setResult] = useState<AnnexUploadResult | null>(null);
  const keyRef = useRef<{ sig: string; key: string } | null>(null);

  const upload = async (files: File[]) => {
    if (busy || files.length === 0) return;
    setResult(null);
    if (files.length > MAX_ANNEX_FILES) {
      setProblem("Sube hasta 20 archivos a la vez (o un .zip).");
      return;
    }
    if (files.reduce((n, f) => n + f.size, 0) > MAX_ANNEX_TOTAL_BYTES) {
      setProblem("Los archivos pesan más de 60 MB en total; súbelos en partes.");
      return;
    }
    const sig = JSON.stringify([tenderId, files.map((f) => [f.name, f.size, f.lastModified])]);
    if (keyRef.current?.sig !== sig) keyRef.current = { sig, key: newTenderCommandKey() };
    setBusy(true);
    setProblem(null);
    try {
      setResult(await attachTenderAnnexes(tenderId, files, keyRef.current.key));
      keyRef.current = null;
      onUploaded();
    } catch (err) {
      if (definitive(err)) keyRef.current = null;
      setProblem(refusalText(err));
    } finally {
      setBusy(false);
    }
  };

  const onChange = (e: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    e.target.value = "";
    void upload(files);
  };

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <input
          id={inputId}
          type="file"
          multiple
          accept={ANNEX_ACCEPT}
          disabled={busy}
          onChange={onChange}
          className="peer sr-only"
        />
        <label
          htmlFor={inputId}
          aria-disabled={busy || undefined}
          className={`inline-flex min-h-[44px] cursor-pointer items-center gap-2 rounded-md border border-line bg-canvas-raised px-4 text-[13px] font-medium text-ink hover:bg-canvas-sunken peer-focus-visible:ring-2 peer-focus-visible:ring-brand-600 ${
            busy ? "cursor-not-allowed opacity-60" : ""
          }`}
        >
          {busy ? <Spinner /> : null}
          {busy ? "Subiendo…" : "Subir anexos"}
        </label>
        <span className="text-[12px] text-ink-muted">PDF, Word, Excel, imágenes o un .zip · hasta 20 archivos, 60 MB en total</span>
      </div>
      {problem ? (
        <p role="alert" className="text-[12.5px] text-bad">
          {problem}
        </p>
      ) : null}
      {result ? <UploadResult result={result} /> : null}
    </div>
  );
}

function UploadResult({ result }: { result: AnnexUploadResult }) {
  return (
    <section aria-label="Resultado de la subida" className="rounded-lg border border-line bg-canvas-sunken/60 px-3 py-2 text-[13px]">
      {result.attached.length > 0 ? (
        <>
          <p className="font-medium text-good">
            {result.attached.length === 1 ? "Se subió 1 archivo:" : `Se subieron ${result.attached.length} archivos:`}
          </p>
          <ul className="mt-1 space-y-0.5">
            {result.attached.map((a) => (
              <li key={a.document_id} className="break-words text-ink">
                {a.filename}
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p className="font-medium text-ink">No se subió ningún archivo nuevo.</p>
      )}
      {result.skipped.length > 0 ? (
        <>
          <p className="mt-2 font-medium text-warn">No se subieron:</p>
          <ul className="mt-1 space-y-0.5">
            {result.skipped.map((s, i) =>
              s.reason === "not_listed" ? (
                <li key={`more-${i}`} className="text-ink-muted">
                  {s.filename.replace(/^\(|\)$/g, "")}
                </li>
              ) : (
                <li key={`${s.filename}-${i}`} className="flex min-w-0 flex-wrap gap-x-2">
                  <span className="break-all text-ink">{s.filename}</span>
                  <span className="text-ink-muted">{labelOf(SKIP_REASON_ES, s.reason, SKIP_GENERIC)}</span>
                </li>
              ),
            )}
          </ul>
        </>
      ) : null}
    </section>
  );
}
