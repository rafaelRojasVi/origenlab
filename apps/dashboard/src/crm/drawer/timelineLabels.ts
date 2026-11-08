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
    default:
      return (e.event_type && FIXED[e.event_type]) || "Cambio registrado";
  }
}

export function timelineLabel(e: TimelineEntry): string {
  const actor = e.actor_display_name?.trim();
  return actor ? `${what(e)} · ${actor}` : what(e);
}
