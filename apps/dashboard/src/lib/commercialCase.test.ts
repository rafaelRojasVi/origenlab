import { describe, expect, it } from "vitest";

import {
  card,
  evidence,
  interest,
  organization,
} from "./__fixtures__/commercialCase";
import {
  caseGaps,
  caseSummary,
  caseRelationLabel,
  caseRoleLabel,
  caseStageLabel,
  contradictingEvidence,
  currentOrganizations,
  historicalOrganizations,
  interestHeadline,
  interestQuantity,
  isCaseClosed,
  linkedEvidence,
  openInterests,
  requestingInstitution,
  stageRequirements,
} from "./commercialCase";

describe("case vocabulary", () => {
  it("names every stage, part and reading in Spanish", () => {
    expect(caseStageLabel("qualifying")).toBe("Calificando");
    expect(caseRoleLabel("requesting_institution")).toBe("Institución que pide");
    expect(caseRelationLabel("contradicts")).toBe("Lo contradice");
  });
});

describe("what a case currently says", () => {
  it("separates current parts from closed ones instead of hiding the closed ones", () => {
    const rows = [
      organization({ opportunity_organization_id: "oo-open", is_current: true }),
      organization({
        opportunity_organization_id: "oo-closed",
        is_current: false,
        valid_to: "2026-09-22",
      }),
    ];
    expect(currentOrganizations(rows).map((r) => r.opportunity_organization_id)).toEqual([
      "oo-open",
    ]);
    expect(historicalOrganizations(rows).map((r) => r.opportunity_organization_id)).toEqual([
      "oo-closed",
    ]);
  });

  it("reports no requesting institution rather than picking one", () => {
    // Two institutions on the case, neither of them the one that is asking. A helper that
    // returned the first row here would invent the single fact this table exists to record
    // deliberately.
    const rows = [
      organization({ opportunity_organization_id: "oo-1", role: "mentioned" }),
      organization({ opportunity_organization_id: "oo-2", role: "funder" }),
    ];
    expect(requestingInstitution(rows)).toBeNull();
  });

  it("ignores a closed requesting-institution row", () => {
    const rows = [
      organization({
        role: "requesting_institution",
        is_current: false,
        valid_to: "2026-09-22",
      }),
    ];
    expect(requestingInstitution(rows)).toBeNull();
  });

  it("finds the current requesting institution when there is one", () => {
    const rows = [
      organization({ opportunity_organization_id: "oo-1", role: "mentioned" }),
      organization({
        opportunity_organization_id: "oo-2",
        role: "requesting_institution",
        name: "Universidad Ficticia del Sur",
      }),
    ];
    expect(requestingInstitution(rows)?.name).toBe("Universidad Ficticia del Sur");
  });

  it("counts open interests and linked evidence without dropping the rest", () => {
    const interests = [
      interest({ opportunity_interest_id: "oi-1" }),
      interest({
        opportunity_interest_id: "oi-2",
        withdrawn_at: "2026-09-22T13:00:00Z",
        withdraw_reason: "El correo se refería a otro equipo",
      }),
    ];
    expect(openInterests(interests)).toHaveLength(1);

    const links = [
      evidence({ opportunity_evidence_id: "oe-1" }),
      evidence({
        opportunity_evidence_id: "oe-2",
        unlinked_at: "2026-09-22T13:00:00Z",
        unlink_reason: "Documento equivocado",
      }),
    ];
    expect(linkedEvidence(links)).toHaveLength(1);
  });

  it("surfaces contradicting evidence, and only while it is still linked", () => {
    const links = [
      evidence({ opportunity_evidence_id: "oe-1", relation: "contradicts" }),
      evidence({
        opportunity_evidence_id: "oe-2",
        relation: "contradicts",
        unlinked_at: "2026-09-22T13:00:00Z",
        unlink_reason: "Se vinculó al caso equivocado",
      }),
    ];
    expect(contradictingEvidence(links).map((r) => r.opportunity_evidence_id)).toEqual([
      "oe-1",
    ]);
  });

  it("reads an interest from whichever of its three subjects exist", () => {
    expect(interestHeadline(interest({ model_text: "Modelo CX-0" }))).toBe("Modelo CX-0");
    expect(
      interestHeadline(
        interest({
          product_name: "Centrífuga de sobremesa",
          model_text: "CX-0",
          manufacturer_organization_name: "Fabricante Ficticio S.A.",
        }),
      ),
    ).toBe("Centrífuga de sobremesa · CX-0 · Fabricante Ficticio S.A.");
  });

  it("renders a quantity only when one was recorded", () => {
    expect(interestQuantity(interest())).toBeNull();
    expect(interestQuantity(interest({ quantity: 2, quantity_unit: "unidades" }))).toBe(
      "2 unidades",
    );
    expect(interestQuantity(interest({ quantity: 3 }))).toBe("3");
  });

  it("calls a case closed from closed_at, which the schema keeps equal to the stage", () => {
    expect(isCaseClosed(card())).toBe(false);
    expect(isCaseClosed(card({ stage: "lost", closed_at: "2026-09-22T14:00:00Z" }))).toBe(
      true,
    );
  });
});

describe("what a case cannot yet say", () => {
  it("names a missing requesting institution as a legitimate state, not an error", () => {
    const gaps = caseGaps(card());
    expect(gaps.some((gap) => gap.includes("Nadie ha dicho quién pide"))).toBe(true);
    expect(gaps.some((gap) => gap.includes("estado legítimo"))).toBe(true);
  });

  it("says nothing about the requesting institution once there is one", () => {
    const gaps = caseGaps(
      card({ organizations: [organization({ role: "requesting_institution" })] }),
    );
    expect(gaps.some((gap) => gap.includes("quién pide"))).toBe(false);
  });

  it("treats a case with no evidence as a sign something was unlinked", () => {
    const gaps = caseGaps(card({ evidence: [] }));
    expect(gaps.some((gap) => gap.includes("se desvinculó"))).toBe(true);
  });

  it("tells the operator to read the contradictions before moving the case", () => {
    const gaps = caseGaps(
      card({ evidence: [evidence({ relation: "contradicts" })] }),
    );
    expect(gaps.some((gap) => gap.includes("lo contradice"))).toBe(true);
  });
});

describe("stage requirements", () => {
  it("reports the institution a qualifying-onward stage needs, from the API's own lists", () => {
    const requirements = stageRequirements(card(), "qualified");
    expect(requirements).toHaveLength(1);
    expect(requirements[0]).toContain("institución solicitante");
  });

  it("drops that requirement once the case has a requesting institution", () => {
    const withInstitution = card({
      organizations: [organization({ role: "requesting_institution" })],
    });
    expect(stageRequirements(withInstitution, "qualified")).toEqual([]);
  });

  it("reports the motive a closing stage needs", () => {
    expect(stageRequirements(card(), "abandoned")[0]).toContain("motivo");
  });

  it("reports nothing for a stage on neither list", () => {
    expect(stageRequirements(card(), "qualifying")).toEqual([]);
  });

  it("reads the lists from the response rather than restating the rule", () => {
    // The API is the single source of the stage machine. A card whose machine says a stage
    // needs nothing must produce no requirement here, even for `qualified` — otherwise this
    // module is a second statement of the rule and can disagree with the database.
    const quiet = card({
      stage_machine: {
        ...card().stage_machine,
        stages_requiring_a_requesting_institution: [],
        stages_requiring_a_close_reason: [],
      },
    });
    expect(stageRequirements(quiet, "qualified")).toEqual([]);
    expect(stageRequirements(quiet, "lost")).toEqual([]);
  });
});

describe("the six lines an operator reads before opening a case", () => {
  it("says nobody has named the institution, rather than picking one", () => {
    const summary = caseSummary(
      card({
        organizations: [organization({ role: "mentioned", name: "Alguna Institución" })],
      }),
    );
    expect(summary.requesting).toBeNull();
  });

  it("leaves withdrawn interests out of what the case is seeking", () => {
    const summary = caseSummary(
      card({
        interests: [
          interest({ opportunity_interest_id: "oi-open", model_text: "Centrífuga CX-0" }),
          interest({
            opportunity_interest_id: "oi-gone",
            model_text: "Agitador AX-1",
            withdrawn_at: "2026-09-22T00:00:00Z",
            withdraw_reason: "El cliente lo descartó",
          }),
        ],
      }),
    );
    expect(summary.interests).toEqual(["Centrífuga CX-0"]);
  });

  it("reads supplier and manufacturer from the current parts only", () => {
    const summary = caseSummary(
      card({
        organizations: [
          organization({
            opportunity_organization_id: "oo-1",
            role: "supplier",
            name: "Proveedor Ficticio Ltda.",
          }),
          organization({
            opportunity_organization_id: "oo-2",
            role: "manufacturer",
            name: "Fabricante Antiguo S.A.",
            is_current: false,
            valid_to: "2026-09-01",
          }),
        ],
      }),
    );
    expect(summary.suppliers).toEqual(["Proveedor Ficticio Ltda."]);
    expect(summary.manufacturers).toEqual([]);
  });

  it("also counts a manufacturer named on an open interest, without duplicating it", () => {
    const summary = caseSummary(
      card({
        organizations: [
          organization({ role: "manufacturer", name: "Fabricante Ficticio S.A." }),
        ],
        interests: [
          interest({ manufacturer_organization_name: "Fabricante Ficticio S.A." }),
        ],
      }),
    );
    expect(summary.manufacturers).toEqual(["Fabricante Ficticio S.A."]);
  });

  it("prefers the newest evidence link as the last thing that happened", () => {
    const summary = caseSummary(
      card({
        updated_at: "2026-09-10T00:00:00Z",
        evidence: [evidence({ linked_at: "2026-09-20T00:00:00Z" })],
      }),
    );
    expect(summary.lastActivityAt).toBe("2026-09-20T00:00:00Z");
    expect(summary.lastActivityLabel).toBe("documento vinculado");
  });

  it("falls back to the case's own write when the evidence is older", () => {
    const summary = caseSummary(
      card({
        updated_at: "2026-09-21T00:00:00Z",
        evidence: [evidence({ linked_at: "2026-09-02T00:00:00Z" })],
      }),
    );
    expect(summary.lastActivityAt).toBe("2026-09-21T00:00:00Z");
    expect(summary.lastActivityLabel).toBe("última escritura del caso");
  });
});
