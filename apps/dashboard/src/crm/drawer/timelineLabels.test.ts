import { describe, expect, it } from "vitest";
import type { TimelineEntry } from "../crmTypes";
import { timelineLabel } from "./timelineLabels";

function entry(over: Partial<TimelineEntry>): TimelineEntry {
  return {
    entry_kind: "domain_event",
    occurred_at: "2026-10-24T12:00:00Z",
    event_type: null,
    aggregate_kind: "opportunity",
    aggregate_id: "o1",
    actor_operator_id: null,
    actor_display_name: null,
    payload: null,
    quote_id: null,
    quote_number: null,
    revision_no: null,
    pdf_sha256: null,
    ...over,
  };
}

describe("timelineLabel", () => {
  it("names each event the drawer knows", () => {
    expect(timelineLabel(entry({ event_type: "opportunity.created" }))).toBe("Caso creado");
    expect(timelineLabel(entry({ event_type: "opportunity.staged", payload: { from_stage: "qualified", to_stage: "quoting" } }))).toBe("Pasó a Enviada");
    expect(timelineLabel(entry({ event_type: "opportunity.staged", payload: { from_stage: "lost", to_stage: "quoting", reopened: true } }))).toBe("Reabierto");
    expect(timelineLabel(entry({ event_type: "opportunity.closed", payload: { stage: "lost", close_reason: "Sin respuesta" } }))).toBe("Cerrado: Sin respuesta");
    expect(timelineLabel(entry({ event_type: "opportunity.origin_set", payload: { from_origin: null, to_origin: "web" } }))).toBe("Origen: Web");
    expect(timelineLabel(entry({ event_type: "opportunity.organization_set" }))).toBe("Institución asignada");
    expect(timelineLabel(entry({ event_type: "quote.created", aggregate_kind: "quote" }))).toBe("Cotización creada");
    expect(
      timelineLabel(entry({ entry_kind: "quotation_sent", quote_number: "01318-26", revision_no: 2 })),
    ).toBe("01318-26 rev. 2 enviada");
    expect(timelineLabel(entry({ event_type: "quote_revision.superseded" }))).toBe("Revisión reemplazada");
    expect(timelineLabel(entry({ event_type: "task.created" }))).toBe("Tarea creada");
    expect(timelineLabel(entry({ event_type: "task.completed" }))).toBe("Tarea hecha");
    expect(timelineLabel(entry({ event_type: "task.cancelled" }))).toBe("Tarea cancelada");
  });

  it("reads anything else as «Cambio registrado»", () => {
    expect(timelineLabel(entry({ event_type: "opportunity.document_attached" }))).toBe("Cambio registrado");
  });

  it("ends with who did it, when the event names a person", () => {
    expect(timelineLabel(entry({ event_type: "opportunity.created", actor_display_name: "Vendedora Ficticia" }))).toBe(
      "Caso creado · Vendedora Ficticia",
    );
  });
});
