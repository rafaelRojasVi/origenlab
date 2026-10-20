import { Badge, type Tone } from "../ui";
import type { ChecklistLine, Cita, ResumenField } from "./tenderApi";
import { CHECKLIST_ESTADO_ES, FALTA_ES, FALTA_GENERIC, RESUMEN_FIELDS, labelOf } from "./tenderLabels";

/**
 * What the machine read from the anexos: the summary, field by field with where each fact comes
 * from («archivo · p.3»; «sin fuente» when the model could not point to one), and the checklist of
 * what the offer must carry, each line with its state once the forms are filled.
 */
export type FileNames = ReadonlyMap<string, string>;

export function citaText(cita: Cita, names: FileNames): string {
  const file = names.get(cita.archivo_id) ?? "anexo";
  return cita.ubicacion ? `${file} · ${cita.ubicacion}` : file;
}

function Sources({ citas, sinCita, names }: { citas: Cita[]; sinCita: boolean; names: FileNames }) {
  if (sinCita || citas.length === 0) return <span className="text-[12px] italic text-ink-faint">sin fuente</span>;
  return (
    <span className="flex flex-wrap gap-x-2 gap-y-0.5">
      {citas.map((c, i) => (
        <span key={`${c.archivo_id}-${c.ubicacion}-${i}`} className="break-all text-[12px] text-ink-muted">
          {citaText(c, names)}
        </span>
      ))}
    </span>
  );
}

export function ResumenList({ resumen, names }: { resumen: Record<string, ResumenField>; names: FileNames }) {
  const fields = RESUMEN_FIELDS.filter((f) => resumen[f.key]);
  if (fields.length === 0) return <p className="text-[13px] text-ink-muted">La lectura no trae un resumen.</p>;
  return (
    <dl className="divide-y divide-line rounded-lg border border-line bg-canvas-raised">
      {fields.map(({ key, label }) => {
        const field = resumen[key];
        return (
          <div key={key} className="grid gap-1 px-3 py-2 sm:grid-cols-[12rem_minmax(0,1fr)] sm:gap-3">
            <dt className="text-[12.5px] font-medium text-ink-muted">{label}</dt>
            <dd className="min-w-0 space-y-0.5">
              <p className="whitespace-pre-line break-words text-[13.5px] text-ink">{field.texto || "No se indica"}</p>
              <Sources citas={field.citas ?? []} sinCita={field.sin_cita} names={names} />
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

const ESTADO_TONE: Record<string, Tone> = { listo: "good", falta: "warn", firmar: "info" };

export function ChecklistList({ lines, names }: { lines: ChecklistLine[]; names: FileNames }) {
  if (lines.length === 0) return <p className="text-[13px] text-ink-muted">La lectura no encontró documentos que entregar.</p>;
  return (
    <ul className="divide-y divide-line rounded-lg border border-line bg-canvas-raised">
      {lines.map((line, i) => {
        const estado = line.estado ?? (line.falta ? "falta" : null);
        return (
          <li key={`${line.documento}-${i}`} className="flex min-w-0 flex-col gap-1 px-3 py-2 sm:flex-row sm:items-start sm:gap-3">
            <span className="w-16 shrink-0">
              {estado ? <Badge tone={ESTADO_TONE[estado] ?? "neutral"}>{labelOf(CHECKLIST_ESTADO_ES, estado, "Revisar")}</Badge> : null}
            </span>
            <span className="min-w-0 flex-1 space-y-0.5">
              <span className="block break-words text-[13.5px] text-ink">{line.documento}</span>
              {estado === "falta" ? (
                <span className="block text-[12px] text-warn">{labelOf(FALTA_ES, line.falta, FALTA_GENERIC)}</span>
              ) : null}
              <span className="block">
                <Sources citas={line.fuente ? [line.fuente] : []} sinCita={line.sin_cita || !line.fuente} names={names} />
              </span>
            </span>
          </li>
        );
      })}
    </ul>
  );
}
