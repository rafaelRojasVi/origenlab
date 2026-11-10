/**
 * One line of the case drawer's «Línea de tiempo», from a `GET …/timeline` entry. The payload
 * carries only stage, origin and close keys (the API whitelists them), so nothing else is read.
 */
import { originLabel } from "../caseOrigin";
import type { TimelineEntry } from "../crmTypes";
import { STAGE_LABEL } from "../stage";

const FIXED: Record<string, string> = {
  "opportunity.created": "Caso creado",
  "opportunity.organization_set": "Institución asignada",
  "quote.created": "Cotización creada",
  "quote_revision.superseded": "Revisión reemplazada",
  "task.created": "Tarea creada",
  "task.completed": "Tarea hecha",
  "task.cancelled": "Tarea cancelada",
};

/** An event this build has no words for: the payload carries nothing more to say about it. */
const GENERIC = "Cambio registrado";

function what(e: TimelineEntry): string {
  if (e.entry_kind === "quotation_sent") {
    return `${e.quote_number ?? "Cotización"} rev. ${e.revision_no ?? "?"} enviada`;
  }
  const p = e.payload ?? {};
  switch (e.event_type) {
    case "opportunity.staged": {
      if (p.reopened) return "Reabierto";
      const to = p.to_stage ?? p.stage ?? null;
      return to ? `Pasó a ${STAGE_LABEL[to] ?? to}` : "Cambio de estado";
    }
    case "opportunity.closed":
      return p.close_reason ? `Cerrado: ${p.close_reason}` : "Cerrado";
    case "opportunity.origin_set":
      return `Origen: ${originLabel(p.to_origin ?? p.to ?? null)}`;
    case "opportunity.pause_set":
      return p.to_paused_until
        ? `En pausa hasta ${String(p.to_paused_until).slice(8, 10)}-${String(p.to_paused_until).slice(5, 7)}${p.to_reason ? ` · ${p.to_reason}` : ""}`
        : "Pausa retomada";
    default:
      return (e.event_type && FIXED[e.event_type]) || GENERIC;
  }
}

export function timelineLabel(e: TimelineEntry): string {
  const actor = e.actor_display_name?.trim();
  return actor ? `${what(e)} · ${actor}` : what(e);
}

/**
 * The lines the drawer shows, newest first: an event with no words of its own («Cambio registrado»)
 * is left out, and the same line twice on one day (two «Pasó a Solicitada») is shown once.
 */
export function timelineLines(entries: TimelineEntry[]): { at: string; label: string }[] {
  // The local calendar day: an event at 22:00 in Santiago is already the next day in UTC.
  const day = (iso: string) => new Date(iso).toDateString();
  const out: { at: string; label: string }[] = [];
  for (const e of [...entries].reverse()) {
    if (what(e) === GENERIC) continue;
    const line = { at: e.occurred_at, label: timelineLabel(e) };
    const prev = out[out.length - 1];
    if (prev && prev.label === line.label && day(prev.at) === day(line.at)) continue;
    out.push(line);
  }
  return out;
}
