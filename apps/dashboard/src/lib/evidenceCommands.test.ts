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
  it("always offers the same five commands in the same order", () => {
    expect(commandPreviews(context()).map((p) => p.id)).toEqual([
      "keep_evidence_pending",
      "attribute_sender_organization",
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
  // The relationship is spelled out in every one of these, because the preview will not
  // supply one. The tests that are *about* that omission are in their own block below.
  const withOrg = () =>
    context({ selectedOrganizationId: "o1", addressRelationship: "shared_mailbox" });

  it("needs an organization chosen first", () => {
    expect(preview("attach_contact_address", context()).blockers).toContain(
      "Selecciona primero la institución a la que pertenece el buzón.",
    );
  });

  it("is available for a role mailbox once an organization and a relationship are chosen", () => {
    const item = preview("attach_contact_address", withOrg());
    expect(item.availability).toBe("available");
    expect(item.cautions.some((c) => c.includes("nombre de función reconocido"))).toBe(false);
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
      addressRelationship: "shared_mailbox",
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

// ---------------------------------------------- attributing the sender to one institution
//
// Modelled on the real pair in the queue: a manufacturer writes about an order for a
// university, and each of the two messages names both institutions while having exactly one
// sender. The values are reserved; the shape is the one that exists.

const SUPPLIER = "ventas@proveedor.example";
const UNIVERSITY = "compras@universidad.example";

function twoNamedInstitutions(sender: string): V2EvidenceRecord {
  return record({
    from_address: sender,
    from_domain: sender.split("@")[1],
    assertion_total: 3,
    assertions: [
      assertion({ assertion_id: "org-new", kind: "organization_name", value_norm: "ultrasonics ejemplo" }),
      assertion({
        assertion_id: "org-existing",
        kind: "organization_name",
        value_norm: "universidad ejemplo",
      }),
      assertion({ assertion_id: "addr", kind: "contact_address", value_norm: sender }),
    ],
    organization_matches: [
      {
        value_norm: "universidad ejemplo",
        organization_id: "org-uuid-existing",
        name: "Universidad Ejemplo",
        confirmation: "machine_proposed",
      },
    ],
  });
}

function attribution(sender: string, chosen: string | null, extra: Partial<CommandContext> = {}) {
  return preview("attribute_sender_organization", {
    record: twoNamedInstitutions(sender),
    domainShareCount: 1,
    selectedOrganizationId: null,
    selectedOrganizationAssertionId: chosen,
    addressRelationship: "shared_mailbox",
    note: "el remitente es de esta institución",
    ...extra,
  });
}

describe("attributing the sender to one of the institutions a message names", () => {
  it("refuses to proceed until the operator says which institution is the sender's", () => {
    const blocked = attribution(SUPPLIER, null);
    expect(blocked.availability).toBe("blocked");
    expect(blocked.blockers.join(" ")).toContain("2 instituciones");
    expect(blocked.request).toBeNull();
  });

  it("does not choose for the operator even when a message names only one", () => {
    const single = preview("attribute_sender_organization", {
      record: record({
        assertion_total: 2,
        assertions: [
          assertion({ assertion_id: "org", kind: "organization_name", value_norm: "agro ejemplo" }),
          assertion({ assertion_id: "addr", kind: "contact_address" }),
        ],
      }),
      domainShareCount: 1,
      selectedOrganizationId: null,
      selectedOrganizationAssertionId: null,
      note: "revisado",
    });
    expect(single.availability).toBe("blocked");
    expect(single.blockers.join(" ")).toContain("Elige la institución");
  });

  it("creates the institution the CRM does not have, with the asserted text", () => {
    const chosen = attribution(SUPPLIER, "org-new");
    expect(chosen.availability).toBe("available");
    expect(chosen.request).toEqual({
      source_record_id: "r1",
      note: "el remitente es de esta institución",
      organization_assertion_id: "org-new",
      address_assertion_id: "addr",
      usage: "shared_mailbox",
      target: "new",
      kind: "unknown",
    });
    expect(chosen.cautions.join(" ")).toContain("texto exacto");
  });

  it("confirms the institution the CRM already has, by exact name", () => {
    const chosen = attribution(UNIVERSITY, "org-existing");
    expect(chosen.availability).toBe("available");
    expect(chosen.request).toEqual({
      source_record_id: "r1",
      note: "el remitente es de esta institución",
      organization_assertion_id: "org-existing",
      address_assertion_id: "addr",
      usage: "shared_mailbox",
      target: "existing",
      organization_id: "org-uuid-existing",
    });
    expect(chosen.cautions.join(" ")).toContain("propuesta por la máquina");
  });

  it("names the institution it is leaving unresolved, before the decision is taken", () => {
    expect(attribution(SUPPLIER, "org-new").leavesUnresolved).toEqual([
      "«universidad ejemplo» queda sin resolver, y el registro pendiente.",
    ]);
    expect(attribution(UNIVERSITY, "org-existing").leavesUnresolved).toEqual([
      "«ultrasonics ejemplo» queda sin resolver, y el registro pendiente.",
    ]);
  });

  it("never writes a person, a domain, a prospect, a permission or a quote", () => {
    const said = attribution(SUPPLIER, "org-new");
    const words = [...said.writes, ...said.doesNot].join(" ").toLowerCase();
    expect(said.writes.join(" ")).not.toMatch(/persona|dominio|prospecto|permiso|cotizaci/i);
    expect(words).toContain("no crea persona");
    expect(words).toContain("no infiere nada del dominio");
    expect(words).toContain("una sola transacción");
  });

  it("refuses when the address already belongs to a different institution", () => {
    const taken = attribution(UNIVERSITY, "org-existing", {
      record: {
        ...twoNamedInstitutions(UNIVERSITY),
        contact_matches: [
          {
            value_norm: UNIVERSITY,
            contact_point_id: "cp1",
            usage: "shared_mailbox",
            confirmation: "confirmed",
            person_id: null,
            person_display_name: null,
            organization_id: "someone-else",
            organization_name: "Otra Institución",
          },
        ],
      } as V2EvidenceRecord,
    });
    expect(taken.availability).toBe("blocked");
    expect(taken.blockers.join(" ")).toContain("ya pertenece a otra institución");
  });

  it("refuses when the address is already attributed to a person", () => {
    const owned = attribution(UNIVERSITY, "org-existing", {
      record: {
        ...twoNamedInstitutions(UNIVERSITY),
        contact_matches: [
          {
            value_norm: UNIVERSITY,
            contact_point_id: "cp1",
            usage: "work",
            confirmation: "confirmed",
            person_id: "p1",
            person_display_name: "Alguien",
            organization_id: null,
            organization_name: null,
          },
        ],
      } as V2EvidenceRecord,
    });
    expect(owned.availability).toBe("blocked");
    expect(owned.blockers.join(" ")).toContain("atribuida a una persona");
  });

  it("still needs a reason, like every other decision", () => {
    expect(attribution(SUPPLIER, "org-new", { note: "  " }).availability).toBe("blocked");
  });

  it("is blocked on a record that is not pending", () => {
    const reviewed = attribution(SUPPLIER, "org-new", {
      record: { ...twoNamedInstitutions(SUPPLIER), review_status: "reviewed" } as V2EvidenceRecord,
    });
    expect(reviewed.availability).toBe("blocked");
  });
});

// ------------------------------------------------------------------------------------------
// The relationship an address has to an institution.
//
// `shared_mailbox` used to be the only value the schema could hold for an address attached to
// an institution with no person, so the preview wrote it into every request — including on a
// named sender's address, where it asserts that several people read that person's mailbox.
// These tests are about the two things that replaced it: a value that claims only what is
// known, and a refusal to make the shared-mailbox claim without saying it out loud.

describe("what the operator says an address is to the institution", () => {
  const NAMED = "p.morales@instituto.example.cl";

  function attach(extra: Partial<CommandContext> = {}) {
    return preview(
      "attach_contact_address",
      context({ selectedOrganizationId: "o1", ...extra }),
    );
  }

  function named(extra: Partial<CommandContext> = {}) {
    return attach({
      record: record({ assertions: [assertion({ value_norm: NAMED })] }),
      ...extra,
    });
  }

  it("has no default: nothing is chosen until the operator chooses", () => {
    const item = attach();
    expect(item.availability).toBe("blocked");
    expect(item.blockers).toContain(
      "Elige qué es esta dirección para la institución: no hay opción por omisión.",
    );
    expect(item.request).toBeNull();
  });

  it("blocks the attribution command too, for the same reason", () => {
    const item = attribution(SUPPLIER, "org-new", { addressRelationship: null });
    expect(item.availability).toBe("blocked");
    expect(item.blockers.join(" ")).toContain("no hay opción por omisión");
    expect(item.request).toBeNull();
  });

  it("refuses to call a named address a shared mailbox", () => {
    const item = named({ addressRelationship: "shared_mailbox" });
    expect(item.availability).toBe("blocked");
    expect(item.blockers.join(" ")).toContain("varias personas la leen");
    expect(item.request).toBeNull();
  });

  it("lets the operator make that claim anyway, once they say how they know", () => {
    const item = named({
      addressRelationship: "shared_mailbox",
      sharedMailboxOverrideNote: "la secretaria y el jefe de laboratorio la responden",
    });
    expect(item.availability).toBe("available");
    expect(item.request).toMatchObject({
      usage: "shared_mailbox",
      shared_mailbox_override_note: "la secretaria y el jefe de laboratorio la responden",
    });
  });

  it("needs no justification for the neutral relationship, and sends none", () => {
    const item = named({ addressRelationship: "individual_owner_unknown" });
    expect(item.availability).toBe("available");
    expect(item.request).toMatchObject({ usage: "individual_owner_unknown" });
    expect(item.request).not.toHaveProperty("shared_mailbox_override_note");
  });

  it("refuses a justification written for a claim the request does not make", () => {
    const item = named({
      addressRelationship: "individual_owner_unknown",
      sharedMailboxOverrideNote: "la leen varias personas",
    });
    expect(item.availability).toBe("blocked");
    expect(item.blockers.join(" ")).toContain("bórrala o cambia la relación");
  });

  it("says that the neutral relationship creates nobody and claims nothing about the owner", () => {
    const item = named({ addressRelationship: "individual_owner_unknown" });
    const said = item.doesNot.join(" ").toLowerCase();
    expect(said).toContain("no crea persona");
    expect(said).toContain("ni afirma de quién es la dirección");
    expect(item.writes.join(" ")).toContain("individual_owner_unknown");
  });

  it("asks for no justification when the local part is a recognised desk", () => {
    const item = attach({ addressRelationship: "shared_mailbox" });
    expect(item.availability).toBe("available");
    expect(item.request).toMatchObject({ usage: "shared_mailbox" });
  });

  it("carries the chosen relationship into the attribution request", () => {
    const item = attribution(SUPPLIER, "org-new", {
      addressRelationship: "individual_owner_unknown",
    });
    expect(item.request).toMatchObject({ usage: "individual_owner_unknown" });
  });
});
