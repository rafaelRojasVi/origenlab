import { useState } from "react";
import { Badge, Button } from "../ui";
import { preparationFileUrl, type Preparation, type PreparationOutput } from "./tenderApi";
import { DRIVE_ES, FICHA_ESTADO_ES, OUTPUT_KIND_ES, labelOf, refusalText } from "./tenderLabels";

/**
 * What the fill produced: the filled forms (with «N por completar» when blanks are left), the spec
 * sheets, and the package. Every file opens through a signed link asked for on the click and shown
 * as a real link (a window opened after an await is popup-blocked in Safari); only `https://` links.
 */

/** A button that asks for the file's link, then shows it as a link valid ten minutes. */
export function FileLink({
  tenderId,
  preparationId,
  fileKey,
  label,
  ready,
  name,
}: {
  tenderId: string;
  preparationId: string;
  fileKey: string;
  /** The button's text («Abrir», «Descargar paquete»). */
  label: string;
  /** The link's text once it is ready («Abrir ahora», «Descargar ahora»). */
  ready: string;
  /** The file's name, for the accessible names of both. */
  name?: string | null;
}) {
  const [busy, setBusy] = useState(false);
  const [link, setLink] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const ask = async () => {
    if (busy) return;
    setBusy(true);
    setProblem(null);
    try {
      setLink(await preparationFileUrl(tenderId, preparationId, fileKey));
    } catch (err) {
      setLink(null);
      setProblem(refusalText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <span className="inline-flex flex-col items-start gap-1">
      <Button
        className="min-h-[44px] px-3 text-[13px]"
        aria-label={name ? `${label} ${name}` : undefined}
        busy={busy}
        busyLabel="Preparando enlace…"
        onClick={() => void ask()}
      >
        {label}
      </Button>
      {link ? (
        <span className="text-[12.5px]">
          <a
            href={link}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={name ? `${ready} ${name}` : undefined}
            className="inline-flex min-h-[44px] items-center font-semibold text-brand-700 underline"
          >
            {ready}
          </a>
          <span className="ml-2 text-ink-muted">El enlace vale 10 minutos.</span>
        </span>
      ) : null}
      {problem ? (
        <span role="alert" className="text-[12.5px] text-bad">
          {problem}
        </span>
      ) : null}
    </span>
  );
}

const FORM_KINDS = new Set(["formulario", "formulario_generado", "oferta_economica", "documento_oferente"]);

export function FilledForms({ tenderId, fill }: { tenderId: string; fill: Preparation }) {
  const rows = fill.outputs.filter((o) => FORM_KINDS.has(o.kind));
  if (rows.length === 0) return <p className="text-[13px] text-ink-muted">No hay formularios llenados en esta preparación.</p>;
  return (
    <ul className="divide-y divide-line rounded-lg border border-line bg-canvas-raised">
      {rows.map((o) => (
        <OutputRow key={o.file_key} tenderId={tenderId} preparationId={fill.id} output={o} />
      ))}
    </ul>
  );
}

function OutputRow({ tenderId, preparationId, output: o }: { tenderId: string; preparationId: string; output: PreparationOutput }) {
  const pending = o.completar_count ?? 0;
  return (
    <li className="flex min-w-0 flex-wrap items-center justify-between gap-2 px-3 py-2">
      <span className="min-w-0 space-y-0.5">
        <span className="block break-all text-[13.5px] font-medium text-ink">{o.filename ?? o.file_key}</span>
        <span className="flex flex-wrap items-center gap-2 text-[12px] text-ink-muted">
          {labelOf(OUTPUT_KIND_ES, o.kind, "Archivo")}
          {pending > 0 ? <Badge tone="warn">{`${pending} por completar`}</Badge> : null}
        </span>
      </span>
      <FileLink tenderId={tenderId} preparationId={preparationId} fileKey={o.file_key} label="Abrir" ready="Abrir ahora" name={o.filename} />
    </li>
  );
}

export function SpecSheets({
  tenderId,
  fill,
  productNames,
}: {
  tenderId: string;
  fill: Preparation;
  productNames: ReadonlyMap<string, string>;
}) {
  const fichas = fill.result?.fichas ?? [];
  if (fichas.length === 0) return <p className="text-[13px] text-ink-muted">La oferta no tiene productos del catálogo con ficha.</p>;
  const byKey = new Map(fill.outputs.map((o) => [o.file_key, o]));
  return (
    <ul className="divide-y divide-line rounded-lg border border-line bg-canvas-raised">
      {fichas.map((f, i) => {
        const output = f.file_key ? byKey.get(f.file_key) : undefined;
        const name = productNames.get(f.product_id) ?? "Producto del catálogo";
        return (
          <li key={`${f.product_id}-${i}`} className="flex min-w-0 flex-wrap items-center justify-between gap-2 px-3 py-2">
            <span className="min-w-0 space-y-0.5">
              <span className="block break-words text-[13.5px] font-medium text-ink">{name}</span>
              <span className="block text-[12px] text-ink-muted">{labelOf(FICHA_ESTADO_ES, f.estado, "Sin información")}</span>
            </span>
            {output ? (
              <FileLink tenderId={tenderId} preparationId={fill.id} fileKey={output.file_key} label="Abrir" ready="Abrir ahora" name={output.filename ?? name} />
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

export function PackageBlock({ tenderId, fill }: { tenderId: string; fill: Preparation }) {
  const paquete = fill.outputs.find((o) => o.kind === "paquete");
  const drive = fill.result?.drive;
  if (!paquete) return <p className="text-[13px] text-ink-muted">Esta preparación no armó un paquete.</p>;
  return (
    <div className="flex min-w-0 flex-wrap items-start justify-between gap-2 rounded-lg border border-line bg-canvas-raised px-3 py-2">
      <span className="min-w-0 space-y-0.5">
        <span className="block break-all text-[13.5px] font-medium text-ink">{paquete.filename ?? "Paquete"}</span>
        {drive ? <span className="block text-[12px] text-ink-muted">{labelOf(DRIVE_ES, drive, "Drive: sin información")}</span> : null}
      </span>
      <FileLink tenderId={tenderId} preparationId={fill.id} fileKey={paquete.file_key} label="Descargar paquete" ready="Descargar ahora" />
    </div>
  );
}
