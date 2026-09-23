import { describe, expect, it } from "vitest";

import {
  activityFromEvidence,
  caseRoleLabels,
  contactChannels,
  contactIdentity,
  institutionRoles,
  marketingStance,
  missingChannelKinds,
  quotesForCases,
  relationCoverageNote,
  splitInstitutionChannels,
} from "./crm360";
import {
  addressControl,
  cardEvidence,
  contactCard,
  listedCaseFor,
  listedQuote,
  organizationCard,
  organizationCaseWithRoles,
} from "./__fixtures__/crm360";

const ORG = "11111111-2222-4333-8444-555555555555";

describe("who a contact card is about", () => {
  it("reads an address with no recorded owner as a pending channel, not as a person", () => {
    const identity = contactIdentity(contactCard());
    expect(identity.kind).toBe("pending_channel");
    expect(identity.title).toBe("compras@instituto.invalid");
    expect(identity.note).toContain("buzón de rol");
  });

  it("names the person when there is one, and stops explaining", () => {
    const identity = contactIdentity(
      contactCard({
        person_id: "p-1",
        person_display_name: "María Ficticia",
        usage: "work",
      }),
    );
    expect(identity.kind).toBe("person");
    expect(identity.title).toBe("María Ficticia");
    expect(identity.note).toBeNull();
  });

  it("distinguishes a role mailbox from an address whose owner is simply unknown", () => {
    // The two look identical on screen if the wording is careless, and they say opposite
    // things: one has no owner, the other has one the CRM has not recorded.
    expect(contactIdentity(contactCard({ usage: "shared_mailbox" })).note).toContain(
      "no una persona",
    );
    expect(
      contactIdentity(contactCard({ usage: "individual_owner_unknown" })).note,
    ).toContain("todavía no se ha registrado cuál");
  });
});

describe("how to reach them", () => {
  it("lists the opened channel first, then the person's other ones", () => {
    const channels = contactChannels(
      contactCard({
        sibling_contact_points: [
          {
            contact_point_id: "cp-2",
            address: "+56 0 0000 0000",
            channel_kind: "phone",
            usage: "work",
            confirmation: "confirmed",
          },
        ],
      }),
    );
    expect(channels.map((row) => row.kindLabel)).toEqual(["Correo", "Teléfono"]);
    expect(channels[0].isPrimary).toBe(true);
  });

  it("names the channel kinds that are absent, so a blank is not read as unknown", () => {
    expect(missingChannelKinds(contactChannels(contactCard()))).toEqual(["Teléfono"]);
  });
});

describe("what may be sent to an address", () => {
  it("never reports permission, because nothing records one", () => {
    const stance = marketingStance([]);
    expect(stance.tone).toBe("neutral");
    expect(stance.headline).toBe("Sin permiso registrado");
    expect(stance.detail).toContain("no es consentimiento");
  });

  it("does not let prior contact read as consent", () => {
    const stance = marketingStance([addressControl({ control_kind: "prior_contact" })]);
    expect(stance.headline).toContain("Sin permiso registrado");
    expect(stance.detail).toContain("no un consentimiento");
  });

  it("puts a block ahead of everything else", () => {
    const stance = marketingStance([
      addressControl({ control_kind: "prior_contact" }),
      addressControl({ control_kind: "block", purpose: "all", source: "unsubscribe_handler" }),
    ]);
    expect(stance.tone).toBe("danger");
    expect(stance.headline).toBe("No contactar por ningún motivo");
  });

  it("reports a cooldown only while it is still running", () => {
    const now = new Date("2026-09-23T00:00:00Z");
    const future = marketingStance(
      [
        addressControl({
          control_kind: "cooldown",
          until_at: "2026-10-01T00:00:00Z",
          reason: "Enviado hace poco",
        }),
      ],
      now,
    );
    expect(future.tone).toBe("warn");
    expect(future.headline).toContain("En espera hasta");

    const expired = marketingStance(
      [addressControl({ control_kind: "cooldown", until_at: "2026-09-01T00:00:00Z" })],
      now,
    );
    expect(expired.headline).toBe("Sin permiso registrado");
  });
});

describe("activity", () => {
  it("reads the evidence trail newest first and flags what is unreviewed", () => {
    const rows = activityFromEvidence([
      cardEvidence({ assertion_id: "a-old", observed_at: "2026-09-01T00:00:00Z" }),
      cardEvidence({
        assertion_id: "a-new",
        observed_at: "2026-09-20T00:00:00Z",
        source_review_status: "reviewed",
      }),
    ]);
    expect(rows.map((row) => row.key)).toEqual(["a-new", "a-old"]);
    expect(rows[0].pending).toBe(false);
    expect(rows[1].pending).toBe(true);
  });
});

describe("what a contact or an institution is involved in", () => {
  it("names every part an institution holds on one case, not just the first", () => {
    const row = organizationCaseWithRoles([{ role: "supplier" }, { role: "manufacturer" }]);
    expect(caseRoleLabels(row).current).toEqual(["Proveedor", "Fabricante"]);
    expect(caseRoleLabels(row).ended).toEqual([]);
  });

  it("keeps a part that has ended apart from one that is current", () => {
    const row = organizationCaseWithRoles([
      { role: "supplier" },
      { role: "manufacturer", valid_to: "2026-06-01", is_current: false },
    ]);
    expect(caseRoleLabels(row).current).toEqual(["Proveedor"]);
    expect(caseRoleLabels(row).ended).toEqual(["Fabricante"]);
  });

  it("attaches quotes through the case id, never through a matching institution name", () => {
    const cases = [listedCaseFor(ORG)];
    const quotes = [
      listedQuote(),
      listedQuote({ quote_id: "q-2", opportunity_id: "op-999" }),
      // Same institution name, no case link. Matching on the name would claim this one.
      listedQuote({ quote_id: "q-3", opportunity_id: null }),
    ];
    expect(quotesForCases(quotes, cases).map((row) => row.quote_id)).toEqual(["q-1"]);
  });

  it("says out loud when the browser-side join could not see everything", () => {
    expect(relationCoverageNote(100, 100)).toBeNull();
    expect(relationCoverageNote(100, 250)).toContain("puede haber más");
  });
});

describe("what an institution is to OrigenLab", () => {
  it("keeps a recorded relationship and a case role as two separate readings", () => {
    const roles = institutionRoles(
      organizationCard({
        relationships: [
          {
            organization_relationship_id: "r-1",
            role: "supplier",
            valid_from: "2026-01-01",
            valid_to: null,
            note: null,
          },
        ],
      }),
      [organizationCaseWithRoles([{ role: "requesting_institution" }])],
    );
    expect(roles.recorded).toEqual(["Proveedor"]);
    expect(roles.onCases).toEqual([
      { role: "requesting_institution", label: "Institución que pide", caseCount: 1 },
    ]);
    expect(roles.empty).toBe(false);
  });

  it("counts every part it holds across its cases, not only the ones it asks in", () => {
    const roles = institutionRoles(organizationCard({ relationships: [] }), [
      organizationCaseWithRoles([{ role: "supplier" }, { role: "manufacturer" }], {
        opportunity_id: "op-1",
      }),
      organizationCaseWithRoles([{ role: "supplier" }], { opportunity_id: "op-2" }),
    ]);
    expect(roles.recorded).toEqual([]);
    // Ordered by CASE_ROLE_ORDER, and counted over distinct cases — never doubled by a
    // case where the institution holds two parts at once.
    expect(roles.onCases).toEqual([
      { role: "supplier", label: "Proveedor", caseCount: 2 },
      { role: "manufacturer", label: "Fabricante", caseCount: 1 },
    ]);
    expect(roles.empty).toBe(false);
  });

  it("does not count a part whose row has closed as one the institution still holds", () => {
    const roles = institutionRoles(organizationCard({ relationships: [] }), [
      organizationCaseWithRoles([
        { role: "supplier", valid_to: "2026-06-01", is_current: false },
      ]),
    ]);
    expect(roles.onCases).toEqual([]);
    expect(roles.empty).toBe(true);
  });

  it("ignores a relationship that has been closed", () => {
    const roles = institutionRoles(
      organizationCard({
        relationships: [
          {
            organization_relationship_id: "r-1",
            role: "supplier",
            valid_from: "2026-01-01",
            valid_to: "2026-06-01",
            note: null,
          },
        ],
      }),
      [],
    );
    expect(roles.recorded).toEqual([]);
    expect(roles.empty).toBe(true);
  });

  it("separates the channels that have a named owner from the ones that do not", () => {
    const split = splitInstitutionChannels(
      organizationCard({
        contact_points: [
          {
            contact_point_id: "cp-1",
            address: "maria@instituto.invalid",
            channel_kind: "email",
            usage: "work",
            confirmation: "confirmed",
            person_display_name: "María Ficticia",
          },
          {
            contact_point_id: "cp-2",
            address: "compras@instituto.invalid",
            channel_kind: "email",
            usage: "shared_mailbox",
            confirmation: "machine_proposed",
            person_display_name: null,
          },
        ],
      }),
    );
    expect(split.named.map((row) => row.contact_point_id)).toEqual(["cp-1"]);
    expect(split.pending.map((row) => row.contact_point_id)).toEqual(["cp-2"]);
  });
});
