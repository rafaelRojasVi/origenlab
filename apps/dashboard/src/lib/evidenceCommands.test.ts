import { describe, expect, it } from "vitest";

import {
  PREVIEW_ONLY_REASON,
  availableCount,
  commandPreviews,
  fold,
  type CommandContext,
} from "./evidenceCommands";
import type { V2EvidenceRecord } from "../api/v2Types";

const ADDRESS = "ventas@instituto.example.cl";

function assertion(overrides: Partial<V2EvidenceRecord["assertions"][number]> = {}) {
  return {
    assertion_id: "a1",
    kind: "contact_address",
    value_norm: ADDRESS,
    resolution: "unresolved",
    resolved_kind: null,
    resolved_id: null,
    ambiguity_note: null,
    ...overrides,
  };
}

function record(overrides: Partial<V2EvidenceRecord> = {}): V2EvidenceRecord {
  return {
    source_record_id: "r1",
    source_kind: "gmail_message",
    dedupe_key: "gmail_message:r1",
    source_uri: "gmail://msg/r1",
    acquired_at: "2026-09-21T20:05:00Z",
    review_status: "pending",
    is_quarantined: false,
    subject: "Cotización",
    from_address: ADDRESS,
    from_domain: "instituto.example.cl",
    message_date: "2026-09-20T12:00:00Z",
    thread_id: "t1",
    assertion_total: 1,
    assertions: [assertion()],
    contact_matches: [],
    organization_matches: [],
    domain_organization: null,
    ...overrides,
  };
}

function context(overrides: Partial<CommandContext> = {}): CommandContext {
  return {
    record: record(),
    domainShareCount: 1,
    selectedOrganizationId: null,
    note: "revisado",
    ...overrides,
  };
}

function preview(id: string, ctx: CommandContext) {
  const found = commandPreviews(ctx).find((candidate) => candidate.id === id);
  if (!found) throw new Error(`no preview for ${id}`);
  return found;
}

describe("the shape of the preview", () => {
  it("always offers the same four commands in the same order", () => {
    expect(commandPreviews(context()).map((p) => p.id)).toEqual([
      "keep_evidence_pending",
      "confirm_organization",
      "create_organization",
      "attach_contact_address",
    ]);
  });

  it("shows a blocked command instead of hiding it", () => {
    // An operator who cannot see why an action is unavailable has to guess.
    const previews = commandPreviews(context());
    const blocked = previews.filter((p) => p.availability === "blocked");
    expect(blocked.length).toBeGreaterThan(0);
    for (const item of blocked) {
      expect(item.blockers.length).toBeGreaterThan(0);
      expect(item.label).toBeTruthy();
    }
  });

  it("never marks one command as the recommended one", () => {
    const serialized = JSON.stringify(commandPreviews(context())).toLowerCase();
    for (const word of ["recomend", "sugerid", "mejor opción", "probablemente"]) {
      expect(serialized).not.toContain(word);
    }
  });
});

describe("the reason nothing is sent", () => {
  it("says the boundary exists and the browser's route to it does not", () => {
    expect(PREVIEW_ONLY_REASON).toContain("/v2/commands/");
    expect(PREVIEW_ONLY_REASON).toContain("proxy");
  });
});

describe("the reason is mandatory", () => {
  it("blocks every command while the note is blank", () => {
    const previews = commandPreviews(context({ note: "   " }));
    expect(availableCount(previews)).toBe(0);
    for (const item of previews) {
      expect(item.blockers.some((b) => b.includes("motivo"))).toBe(true);
    }
  });

  it("lets the simplest decision through once a reason exists", () => {
    expect(preview("keep_evidence_pending", context()).availability).toBe("available");
  });
});

describe("a record that cannot be decided", () => {
  it("refuses everything on a quarantined record", () => {
    const ctx = context({ record: record({ is_quarantined: true }) });
    expect(availableCount(commandPreviews(ctx))).toBe(0);
  });

  it("refuses a promotion on a record already reviewed", () => {
    const ctx = context({ record: record({ review_status: "reviewed" }) });
    expect(preview("create_organization", ctx).blockers).toContain(
      "Este registro ya fue revisado; no admite una decisión nueva.",
    );
  });

  it("still lets an already-reviewed record be noted as pending-by-choice", () => {
    // `keep_evidence_pending` is about the operator's reading, not the record's state, so a
    // reviewed record does not block it the way a promotion is blocked.
    const ctx = context({ record: record({ review_status: "reviewed" }) });
    expect(preview("keep_evidence_pending", ctx).availability).toBe("available");
  });
});

describe("creating an organization", () => {
  it("is impossible when the message names none", () => {
    const item = preview("create_organization", context());
    expect(item.availability).toBe("blocked");
    expect(item.blockers.some((b) => b.includes("pista de dominio"))).toBe(true);
  });

  it("is possible only from a name the message itself states", () => {
    const ctx = context({
      record: record({
        assertions: [assertion({ kind: "organization_name", value_norm: "instituto del sur" })],
      }),
    });
    const item = preview("create_organization", ctx);
    expect(item.availability).toBe("available");
    expect(item.cautions[0]).toContain("instituto del sur");
  });

  it("refuses when an organization of that exact name already exists", () => {
    const ctx = context({
      record: record({
        assertions: [assertion({ kind: "organization_name", value_norm: "instituto del sur" })],
        organization_matches: [
          {
            value_norm: "instituto del sur",
            organization_id: "o1",
            name: "Instituto del Sur",
            confirmation: "machine_proposed",
          },
        ],
      }),
    });
    expect(preview("create_organization", ctx).blockers.some((b) => b.includes("confírmala"))).toBe(
      true,
    );
  });

  it("refuses to promote when the message names two institutions", () => {
    const ctx = context({
      record: record({
        assertions: [
          assertion({ assertion_id: "a1", kind: "organization_name", value_norm: "uno" }),
          assertion({ assertion_id: "a2", kind: "organization_name", value_norm: "dos" }),
        ],
      }),
    });
    expect(preview("create_organization", ctx).availability).toBe("blocked");
  });

  it("says out loud that it creates no person and no marketing permission", () => {
    const item = preview("create_organization", context());
    expect(item.doesNot.some((d) => d.includes("persona"))).toBe(true);
    expect(item.doesNot.some((d) => d.includes("marketing"))).toBe(true);
  });
});

describe("confirming an existing organization", () => {
  const named = record({
    assertions: [assertion({ kind: "organization_name", value_norm: "instituto del sur" })],
    organization_matches: [
      {
        value_norm: "instituto del sur",
        organization_id: "o1",
        name: "Instituto del Sur",
        confirmation: "machine_proposed",
      },
    ],
  });

  it("needs an organization the operator actually chose", () => {
    expect(preview("confirm_organization", context({ record: named })).blockers).toContain(
      "Selecciona la institución existente que corresponde exactamente.",
    );
  });

  it("accepts an exact match, ignoring only letter case and spacing", () => {
    const ctx = context({ record: named, selectedOrganizationId: "o1" });
    const item = preview("confirm_organization", ctx);
    expect(item.availability).toBe("available");
    expect(item.cautions.some((c) => c.includes("propuesta por la máquina"))).toBe(true);
  });

  it("refuses an organization whose name merely resembles the asserted one", () => {
    const ctx = context({
      record: record({
        assertions: [assertion({ kind: "organization_name", value_norm: "instituto del sur" })],
        organization_matches: [
          {
            value_norm: "instituto del sur",
            organization_id: "o2",
            name: "Instituto del Norte",
            confirmation: "confirmed",
          },
        ],
      }),
      selectedOrganizationId: "o2",
    });
    expect(preview("confirm_organization", ctx).blockers.some((b) => b.includes("exactamente"))).toBe(
      true,
    );
  });

  it("never merges", () => {
    expect(preview("confirm_organization", context()).doesNot.some((d) => d.includes("fusiona"))).toBe(
      true,
    );
  });
});

describe("attaching an address", () => {
  const withOrg = () => context({ selectedOrganizationId: "o1" });

  it("needs an organization chosen first", () => {
    expect(preview("attach_contact_address", context()).blockers).toContain(
      "Selecciona primero la institución a la que pertenece el buzón.",
    );
  });

  it("is available for a role mailbox once an organization is chosen", () => {
    const item = preview("attach_contact_address", withOrg());
    expect(item.availability).toBe("available");
    expect(item.cautions.some((c) => c.includes("no parece un buzón de mesa"))).toBe(false);
  });

  it("cautions rather than refuses when the address looks personal", () => {
    // The distinction this module is built around: a local part is a spelling, not evidence.
    const ctx = context({
      record: record({
        assertions: [assertion({ value_norm: "p.morales@instituto.example.cl" })],
      }),
      selectedOrganizationId: "o1",
    });
    const item = preview("attach_contact_address", ctx);
    expect(item.availability).toBe("available");
    expect(item.cautions.some((c) => c.includes("no parece un buzón de mesa"))).toBe(true);
  });

  it("refuses an address already attributed to a person", () => {
    const ctx = context({
      record: record({
        contact_matches: [
          {
            value_norm: ADDRESS,
            contact_point_id: "cp1",
            usage: "personal",
            confirmation: "confirmed",
            person_id: "p1",
            person_display_name: "Alguien",
            organization_id: null,
            organization_name: null,
          },
        ],
      }),
      selectedOrganizationId: "o1",
    });
    expect(preview("attach_contact_address", ctx).availability).toBe("blocked");
  });

  it("refuses an address another organization already owns", () => {
    const ctx = context({
      record: record({
        contact_matches: [
          {
            value_norm: ADDRESS,
            contact_point_id: "cp1",
            usage: "shared_mailbox",
            confirmation: "confirmed",
            person_id: null,
            person_display_name: null,
            organization_id: "o9",
            organization_name: "Otra",
          },
        ],
      }),
      selectedOrganizationId: "o1",
    });
    expect(
      preview("attach_contact_address", ctx).blockers.some((b) => b.includes("otra institución")),
    ).toBe(true);
  });

  it("attaches an existing unattributed channel instead of duplicating it", () => {
    const ctx = context({
      record: record({
        contact_matches: [
          {
            value_norm: ADDRESS,
            contact_point_id: "cp1",
            usage: "unattributed",
            confirmation: "machine_proposed",
            person_id: null,
            person_display_name: null,
            organization_id: null,
            organization_name: null,
          },
        ],
      }),
      selectedOrganizationId: "o1",
    });
    const item = preview("attach_contact_address", ctx);
    expect(item.availability).toBe("available");
    expect(item.writes.some((w) => w.includes("ya existe"))).toBe(true);
  });

  it("warns when several pending records share the domain", () => {
    const ctx = context({ selectedOrganizationId: "o1", domainShareCount: 4 });
    expect(
      preview("attach_contact_address", ctx).cautions.some((c) => c.includes("3 registros")),
    ).toBe(true);
  });

  it("says out loud that receiving mail is not permission to send", () => {
    expect(
      preview("attach_contact_address", context()).doesNot.some((d) =>
        d.includes("no es autorización para enviar"),
      ),
    ).toBe(true);
  });
});

describe("folding", () => {
  it("ignores case and spacing and nothing else", () => {
    expect(fold("  Instituto   del Sur ")).toBe("instituto del sur");
    expect(fold("Instituto del Sur")).not.toBe(fold("Instituto del Norte"));
  });
});
