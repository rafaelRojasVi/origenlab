import { describe, expect, it } from "vitest";

import type { V2EvidenceRecord } from "../api/v2Types";
import {
  addressesOf,
  domainCounts,
  identityHeadline,
  isConsumerDomain,
  isRoleMailbox,
  organizationHeadline,
  organizationNamesOf,
  reviewDate,
  reviewFlags,
  REVIEW_FLOW,
} from "./evidenceReview";

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
    assertion_total: 1,
    assertions: [
      {
        assertion_id: "a1",
        kind: "contact_address",
        value_norm: "p.morales@quimsur.example.cl",
        resolution: "unresolved",
        resolved_kind: null,
        resolved_id: null,
        ambiguity_note: null,
      },
    ],
    contact_matches: [],
    organization_matches: [],
    domain_organization: null,
    ...overrides,
  };
}

function match(overrides: Partial<V2EvidenceRecord["contact_matches"][number]> = {}) {
  return {
    value_norm: "p.morales@quimsur.example.cl",
    contact_point_id: "cp1",
    usage: "unattributed" as const,
    confirmation: "machine_proposed" as const,
    person_id: null,
    person_display_name: null,
    organization_id: null,
    organization_name: null,
    ...overrides,
  };
}

function kinds(row: V2EvidenceRecord, counts = domainCounts([row])) {
  return reviewFlags(row, counts).map((flag) => flag.kind);
}

describe("role mailboxes", () => {
  it("recognises a desk", () => {
    for (const address of [
      "secretaria@farmaisa.example.cl",
      "distribucion@quimsur.example.cl",
      "produccion@plantasur.example.cl",
      "CONTACTO@origenlab.cl",
    ]) {
      expect(isRoleMailbox(address)).toBe(true);
    }
  });

  it("does not read a person's initials as a role", () => {
    // `p.morales` splits to `p`, and `mcastro` to itself. Neither is a desk, and calling
    // them one would put a false warning on most of the queue.
    for (const address of [
      "p.morales@quimsur.example.cl",
      "mcastro@labnorte.example.cl",
      "luisamaya@universidadsur.example.cl",
      "carla.pinto@omegamar.example.cl",
    ]) {
      expect(isRoleMailbox(address)).toBe(false);
    }
  });

  it("sees through an explicit suffix", () => {
    expect(isRoleMailbox("ventas+equipos@example.cl")).toBe(true);
    expect(isRoleMailbox("contacto.sur@example.cl")).toBe(true);
  });
});

describe("consumer domains", () => {
  it("knows where a domain says nothing about an employer", () => {
    expect(isConsumerDomain("gmail.com")).toBe(true);
    expect(isConsumerDomain("live.cl")).toBe(true);
    expect(isConsumerDomain("universidadsur.example.cl")).toBe(false);
    expect(isConsumerDomain(null)).toBe(false);
  });
});

describe("reading a record", () => {
  it("separates the two assertion kinds", () => {
    const row = record({
      assertions: [
        ...record().assertions,
        {
          assertion_id: "a2",
          kind: "organization_name",
          value_norm: "agroinsumos andes",
          resolution: "unresolved",
          resolved_kind: null,
          resolved_id: null,
          ambiguity_note: null,
        },
      ],
    });
    expect(addressesOf(row)).toEqual(["p.morales@quimsur.example.cl"]);
    expect(organizationNamesOf(row)).toEqual(["agroinsumos andes"]);
  });

  it("counts how many pending records share a domain", () => {
    const counts = domainCounts([
      record({ from_domain: "universidadsur.example.cl" }),
      record({ from_domain: "universidadsur.example.cl" }),
      record({ from_domain: "instituto.example.cl" }),
      record({ from_domain: null }),
    ]);
    expect(counts.get("universidadsur.example.cl")).toBe(2);
    expect(counts.get("instituto.example.cl")).toBe(1);
    expect(counts.has("null")).toBe(false);
  });
});

describe("what is unresolved", () => {
  it("says an existing address has no registered holder", () => {
    const row = record({ contact_matches: [match()] });
    const flags = kinds(row);
    expect(flags).toContain("address_exists_without_person");
    expect(flags).toContain("address_exists_without_organization");
    expect(flags).not.toContain("address_is_new");
    expect(identityHeadline(row)).toBe("Dirección conocida, sin titular registrado");
  });

  it("says a new address is new", () => {
    const row = record();
    expect(kinds(row)).toContain("address_is_new");
    expect(identityHeadline(row)).toBe("Dirección nueva");
  });

  it("names a person only when the durable CRM already records one", () => {
    const row = record({
      contact_matches: [match({ person_id: "p1", person_display_name: "Paula Morales" })],
    });
    expect(kinds(row)).not.toContain("address_exists_without_person");
    expect(identityHeadline(row)).toBe("Dirección con persona registrada");
  });

  it("treats an unregistered domain as a hint, not as evidence", () => {
    expect(kinds(record())).toContain("domain_not_registered");
  });

  it("stops offering the domain hint on a consumer mailbox", () => {
    const row = record({ from_address: "usuariaparticular9@gmail.com", from_domain: "gmail.com" });
    const flags = kinds(row);
    expect(flags).toContain("consumer_domain");
    // The two are mutually exclusive on purpose: a consumer domain is not a weaker
    // institutional hint, it is not an institutional hint at all.
    expect(flags).not.toContain("domain_not_registered");
  });

  it("does not claim a domain organization the API never returned", () => {
    const row = record({
      domain_organization: { organization_id: "o1", name: "QuimSur", scope: "primary" },
    });
    expect(kinds(row)).not.toContain("domain_not_registered");
    expect(organizationHeadline(row)).toBe("Institución por dominio: QuimSur");
  });

  it("warns when several pending records share one domain", () => {
    const rows = [record({ source_record_id: "r1" }), record({ source_record_id: "r2" })];
    expect(kinds(rows[0], domainCounts(rows))).toContain("domain_shared_in_queue");
  });

  it("marks an exact name match as a coincidence of spelling, not an identity", () => {
    const row = record({
      assertions: [
        {
          assertion_id: "a2",
          kind: "organization_name",
          value_norm: "universidad del sur",
          resolution: "unresolved",
          resolved_kind: null,
          resolved_id: null,
          ambiguity_note: null,
        },
      ],
      organization_matches: [
        {
          value_norm: "universidad del sur",
          organization_id: "o1",
          name: "Universidad del Sur",
          confirmation: "machine_proposed",
        },
      ],
    });
    const flags = reviewFlags(row, domainCounts([row]));
    expect(flags.map((flag) => flag.kind)).toContain("organization_named_matches_existing");
    expect(flags.find((flag) => flag.kind === "organization_named_matches_existing")?.text).toContain(
      "no es ser la misma institución",
    );
  });

  it("flags a message that names two institutions", () => {
    const named = (value: string, id: string) => ({
      assertion_id: id,
      kind: "organization_name",
      value_norm: value,
      resolution: "unresolved",
      resolved_kind: null,
      resolved_id: null,
      ambiguity_note: null,
    });
    const row = record({
      assertions: [named("ultrasonidos delta", "a2"), named("universidad del sur", "a3")],
    });
    expect(kinds(row)).toContain("several_organizations_named");
  });

  it("says plainly when nothing names an institution", () => {
    const row = record();
    expect(kinds(row)).toContain("no_organization_named");
    expect(organizationHeadline(row)).toBe("Solo pista de dominio: quimsur.example.cl");
  });

  it("puts a quarantined record first", () => {
    const row = record({ is_quarantined: true });
    expect(kinds(row)[0]).toBe("quarantined");
  });
});

describe("the flow", () => {
  it("keeps the five steps in the order the decisions depend on each other", () => {
    expect(REVIEW_FLOW.map((step) => step.id)).toEqual([
      "evidence",
      "review",
      "prospect",
      "marketing",
      "quote",
    ]);
  });
});

describe("dates", () => {
  it("renders a missing date as a dash rather than as Invalid Date", () => {
    expect(reviewDate(null)).toBe("—");
    expect(reviewDate("not a date")).toBe("not a date");
    expect(reviewDate("2026-09-09T18:13:35Z")).toMatch(/2026/);
  });
});
