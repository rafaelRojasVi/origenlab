import { Badge, type Tone } from "../ui";
import type { ChecklistLine, Cita, ResumenField } from "./tenderApi";
import { CHECKLIST_ESTADO_ES, FALTA_ES, FALTA_GENERIC, RESUMEN_FIELDS, labelOf } from "./tenderLabels";

/**
 * What the machine read from the anexos: the summary, field by field with where each fact comes
 * from («archivo · p.3», folded under «Fuentes»; «sin fuente» when the model could not point to
 * one), and the checklist of what the offer must carry — grouped by kind, each line with «Firma»
 * when it must be signed and its state once the forms are filled.
 */
export type FileNames = ReadonlyMap<string, string>;

export function citaText(cita: Cita, names: FileNames): string {
  const file = names.get(cita.archivo_id) ?? "anexo";
  return cita.ubicacion ? `${file} · ${cita.ubicacion}` : file;
}

/** Where a fact comes from, folded: «Fuentes (3)» opens the list, so the facts read without it. */
function Sources({ citas, sinCita, names }: { citas: Cita[]; sinCita: boolean; names: FileNames }) {
  if (sinCita || citas.length === 0) return <span className="text-[12px] italic text-ink-faint">sin fuente</span>;
  return (
    <details className="group text-[12px]">
      <summary className="inline-flex min-h-[28px] cursor-pointer list-none items-center gap-1 text-ink-faint hover:text-ink-muted focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 [&::-webkit-details-marker]:hidden">
        <span aria-hidden="true" className="transition-transform group-open:rotate-90 motion-reduce:transition-none">›</span>
        Fuentes ({citas.length})
      </summary>
      <span className="mt-0.5 flex flex-col gap-0.5 pl-3">
        {citas.map((c, i) => (
          <span key={`${c.archivo_id}-${c.ubicacion}-${i}`} className="break-all text-ink-muted">
            {citaText(c, names)}
          </span>
        ))}
      </span>
    </details>
  );
}

/**
 * A summary text as the model wrote it, with its lists as lists: «Lead. - A - B - C» (the read's
 * habit for criteria, causes, guarantees) becomes the lead and three bullets. A hyphen only splits
 * when spaced and followed by a capital or digit, and only when it yields two or more bullets.
 * Without those, «A: 1; B: 2; C: 3» (quantities) becomes three bullets when it has three or more.
 */
export function splitList(text: string): { lead: string; bullets: string[] } {
  const parts = text.split(/(?:^|\s+)-\s+(?=[A-ZÁÉÍÓÚÑ0-9])/);
  const lead = parts[0].trim();
  const bullets = parts.slice(1).map((b) => b.trim()).filter(Boolean);
  if (bullets.length >= 2) return { lead, bullets };
  const items = text.split(/;\s+/).map((b) => b.trim().replace(/\.$/, "")).filter(Boolean);
  return items.length >= 3 ? { lead: "", bullets: items } : { lead: text, bullets: [] };
}

function FieldText({ text }: { text: string }) {
  const { lead, bullets } = splitList(text);
  return (
    <div className="max-w-prose space-y-1 text-[13.5px] leading-relaxed text-ink">
      {lead ? <p className="whitespace-pre-line break-words">{lead}</p> : null}
      {bullets.length ? (
        <ul className="list-disc space-y-0.5 pl-5 marker:text-ink-faint">
          {bullets.map((b, i) => (
            <li key={i} className="break-words">
              {b}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
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
          <div key={key} className="grid gap-1 px-4 py-3 sm:grid-cols-[11rem_minmax(0,1fr)] sm:gap-4">
            <dt className="pt-px text-[12.5px] font-semibold text-ink-muted">{label}</dt>
            <dd className="min-w-0 space-y-1">
              <FieldText text={field.texto || "No se indica"} />
              <Sources citas={field.citas ?? []} sinCita={field.sin_cita} names={names} />
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

const ESTADO_TONE: Record<string, Tone> = { listo: "good", falta: "warn", firmar: "info" };

/** The checklist's kinds, in the order a bidder gathers them; any other kind goes last, as «Otros». */
const KIND_ORDER: readonly { key: string; label: string }[] = [
  { key: "formulario", label: "Formularios" },
  { key: "declaracion", label: "Declaraciones juradas" },
  { key: "documento", label: "Documentos" },
  { key: "certificado", label: "Certificados" },
  { key: "garantia", label: "Garantías" },
  { key: "otro", label: "Otros" },
];

function groups(lines: ChecklistLine[]): { key: string; label: string; lines: ChecklistLine[] }[] {
  const known = new Set(KIND_ORDER.map((k) => k.key));
  return KIND_ORDER.map(({ key, label }) => ({
    key,
    label,
    lines: lines.filter((l) => (known.has(l.tipo ?? "") ? l.tipo : "otro") === key),
  })).filter((g) => g.lines.length > 0);
}

export function ChecklistList({ lines, names }: { lines: ChecklistLine[]; names: FileNames }) {
  if (lines.length === 0) return <p className="text-[13px] text-ink-muted">La lectura no encontró documentos que entregar.</p>;
  const signed = lines.filter((l) => l.requiere_firma).length;
  return (
    <div className="space-y-3">
      <p className="text-[12.5px] text-ink-muted">
        {lines.length === 1 ? "1 documento" : `${lines.length} documentos`}
        {signed ? ` · ${signed} con firma` : ""}
      </p>
      {groups(lines).map((g) => (
        <section key={g.key} aria-label={g.label} className="rounded-lg border border-line bg-canvas-raised">
          <h4 className="flex items-center justify-between border-b border-line px-4 py-2 text-[12.5px] font-semibold text-ink">
            {g.label}
            <span className="text-[12px] font-normal tabular-nums text-ink-muted">{g.lines.length}</span>
          </h4>
          <ul className="divide-y divide-line">
            {g.lines.map((line, i) => (
              <ChecklistRow key={`${line.documento}-${i}`} line={line} names={names} />
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

function ChecklistRow({ line, names }: { line: ChecklistLine; names: FileNames }) {
  const estado = line.estado ?? (line.falta ? "falta" : null);
  return (
    <li className="flex min-w-0 flex-col gap-1 px-4 py-2.5">
      <span className="flex min-w-0 flex-wrap items-start gap-x-2 gap-y-1">
        <span className="min-w-0 flex-1 basis-60 break-words text-[13.5px] leading-snug text-ink">{line.documento}</span>
        <span className="flex shrink-0 flex-wrap gap-1">
          {line.requiere_firma ? (
            <Badge tone="info" glyph={false}>
              Firma
            </Badge>
          ) : null}
          {estado ? <Badge tone={ESTADO_TONE[estado] ?? "neutral"}>{labelOf(CHECKLIST_ESTADO_ES, estado, "Revisar")}</Badge> : null}
        </span>
      </span>
      {estado === "falta" ? <span className="block text-[12px] text-warn">{labelOf(FALTA_ES, line.falta, FALTA_GENERIC)}</span> : null}
      <Sources citas={line.fuente ? [line.fuente] : []} sinCita={line.sin_cita || !line.fuente} names={names} />
    </li>
  );
}
