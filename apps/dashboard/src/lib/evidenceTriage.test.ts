import { describe, expect, it } from "vitest";

import type { V2EvidenceRecord } from "../api/v2Types";
import {
  TRIAGE_CATEGORIES,
  triageCategoryLabel,
  triageOf,
  triageCounts,
  recordsInCategory,
} from "./evidenceTriage";

function record(overrides: Partial<V2EvidenceRecord> = {}): V2EvidenceRecord {
  return {
    source_record_id: "r1",
    source_kind: "gmail_message",
    dedupe_key: "gmail_message:abc",
    source_uri: "gmail://msg/abc",
    acquired_at: "2026-09-21T20:05:00Z",
    review_status: "pending",
    is_quarantined: false,
    subject: "Cotización",
    from_address: "p.morales@quimsur.example.cl",
    from_domain: "quimsur.example.cl",
    message_date: "2026-09-09T18:13:35Z",
    thread_id: "t1",
    assertion_total: 0,
    assertions: [],
    contact_matches: [],
    organization_matches: [],
    domain_organization: null,
    ...overrides,
  };
}

describe("triage categories", () => {
  it("names exactly the four buckets the operator asked for", () => {
    expect(TRIAGE_CATEGORIES).toEqual([
      "commercial",
      "counterparty_auto_reply",
      "vendor_notice",
      "unclassified",
    ]);
  });

  it("gives every category a Spanish label", () => {
    for (const category of TRIAGE_CATEGORIES) {
      expect(triageCategoryLabel(category)).not.toEqual("");
    }
  });
});

describe("every verdict carries its reason", () => {
  it("never returns a category without at least one reason", () => {
    const samples = [
      record(),
      record({ subject: "Automatic reply: OrigenLab", from_address: "x@sec.example.cl" }),
      record({ from_address: "no-reply@accounts.google.com", from_domain: "accounts.google.com" }),
      record({ subject: null, from_address: null, from_domain: null }),
    ];
    for (const sample of samples) {
      const verdict = triageOf(sample);
      expect(verdict.reasons.length).toBeGreaterThan(0);
      for (const reason of verdict.reasons) {
        expect(reason.text.length).toBeGreaterThan(0);
      }
    }
  });
});

describe("counterparty auto-replies", () => {
  it.each([
    "Automatic reply: OrigenLab · Equipos para laboratorio",
    "Respuesta automática: su mensaje",
    "Out of Office Re: Cotización",
    "Feriado Legal Re: Presentación de OrigenLab",
    "Permiso Administrativo Re: OrigenLab | Centrífugas",
    "Ausencia laboral Re: propuesta",
  ])("reads %s as an auto-reply", (subject) => {
    expect(triageOf(record({ subject })).category).toBe("counterparty_auto_reply");
  });

  it("beats the commercial reading: an away note is an away note even on a quote thread", () => {
    const verdict = triageOf(record({ subject: "Feriado Legal Re: Cotización 123553" }));
    expect(verdict.category).toBe("counterparty_auto_reply");
    expect(verdict.reasons.some((reason) => reason.kind === "auto_reply_subject")).toBe(true);
  });

  it("treats an unanswerable desk at a counterparty as an auto-reply, not a SaaS notice", () => {
    const verdict = triageOf(
      record({
        from_address: "proveedores+noreply@saludohiggins.example.cl",
        from_domain: "saludohiggins.example.cl",
        subject: "Re: Presentación de OrigenLab",
      }),
    );
    expect(verdict.category).toBe("counterparty_auto_reply");
    expect(verdict.reasons.some((reason) => reason.kind === "no_reply_mailbox")).toBe(true);
  });
});

describe("the operator's own vendor and security notices", () => {
  it.each([
    ["workspace-noreply@google.com", "google.com", "¿Todo listo para tu semana más productiva?"],
    ["no-reply@accounts.google.com", "accounts.google.com", "Alerta de seguridad"],
    ["no-reply@account.tidio.com", "account.tidio.com", "[Tidio] Verifica tu dirección"],
    ["olek@tidio.net", "tidio.net", "Your first ticket is here!"],
  ])("reads %s as a notice about our own tooling", (from_address, from_domain, subject) => {
    const verdict = triageOf(record({ from_address, from_domain, subject }));
    expect(verdict.category).toBe("vendor_notice");
  });

  it("names the platform in the reason rather than asserting it silently", () => {
    const verdict = triageOf(
      record({
        from_address: "no-reply@accounts.google.com",
        from_domain: "accounts.google.com",
        subject: "Alerta de seguridad",
      }),
    );
    expect(verdict.reasons).toContainEqual({
      kind: "platform_domain",
      text: "El remitente está en accounts.google.com, bajo google.com — una plataforma que nosotros contratamos: el mensaje habla de nuestra propia herramienta, no de un laboratorio.",
    });
  });

  it("does not turn a counterparty on a consumer mailbox into a notice", () => {
    const verdict = triageOf(
      record({
        from_address: "javiera.p@gmail.com",
        from_domain: "gmail.com",
        subject: "Solicitud de cotización — Especial septiembre",
      }),
    );
    expect(verdict.category).toBe("commercial");
  });
});

describe("commercial correspondence", () => {
  it.each([
    "Re: Corteva Agriscience Solicitud de cotización de insumos",
    "Cotización 123553",
    "RE: solicitud de cotizacion",
    "Orden de compra - Origenlab- Estándar",
    "Pedido 166.619",
    "URGENTE Cotización std 100 mOs",
    "RE: Solicitud de compra de equipos mediante Excedentes",
    "OC 5500019026 ( GD 01 )",
  ])("reads %s as commercial", (subject) => {
    expect(triageOf(record({ subject })).category).toBe("commercial");
  });

  it("quotes the marker it matched, so the operator can disagree with it", () => {
    const verdict = triageOf(record({ subject: "Cotización 123553" }));
    const reason = verdict.reasons.find((row) => row.kind === "commercial_subject");
    expect(reason?.text).toContain("cotiz");
  });
});

describe("what the triage refuses to decide", () => {
  it("leaves a reply with no commercial marker unclassified rather than guessing", () => {
    const verdict = triageOf(record({ subject: "Re: tu mensaje" }));
    expect(verdict.category).toBe("unclassified");
  });

  it("leaves a message with no subject and no sender unclassified", () => {
    const verdict = triageOf(record({ subject: null, from_address: null, from_domain: null }));
    expect(verdict.category).toBe("unclassified");
    expect(verdict.reasons[0].kind).toBe("no_signal");
  });

  it("is a visual aid and says so: it never reports a durable effect", () => {
    const verdict = triageOf(record());
    expect(Object.keys(verdict)).toEqual(["category", "reasons"]);
  });
});

describe("the queue as a whole", () => {
  const queue = [
    record({ source_record_id: "a", subject: "Cotización 1" }),
    record({ source_record_id: "b", subject: "Cotización 2" }),
    record({ source_record_id: "c", subject: "Automatic reply: x" }),
    record({
      source_record_id: "d",
      subject: "Alerta de seguridad",
      from_address: "no-reply@accounts.google.com",
      from_domain: "accounts.google.com",
    }),
    record({ source_record_id: "e", subject: "Re: tu mensaje" }),
  ];

  it("counts every category, including the empty ones", () => {
    const counts = triageCounts(queue);
    expect(counts).toEqual({
      commercial: 2,
      counterparty_auto_reply: 1,
      vendor_notice: 1,
      unclassified: 1,
    });
  });

  it("partitions the queue: nothing is dropped and nothing is counted twice", () => {
    const total = TRIAGE_CATEGORIES.reduce(
      (sum, category) => sum + recordsInCategory(queue, category).length,
      0,
    );
    expect(total).toBe(queue.length);
  });

  it("keeps the queue's order inside a category", () => {
    expect(recordsInCategory(queue, "commercial").map((row) => row.source_record_id)).toEqual([
      "a",
      "b",
    ]);
  });
});
