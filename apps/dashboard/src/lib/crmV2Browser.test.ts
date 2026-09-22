import { describe, expect, it } from "vitest";

import {
  CRM_V2_TABS,
  EVIDENCE_RESOLUTIONS,
  EVIDENCE_SOURCE_KINDS,
  NO_ORGANIZATION_EXPLANATION,
  cappedListNote,
  cardCount,
  confirmationLabel,
  noPersonExplanation,
  pageFooter,
  resolutionLabel,
  sourceKindLabel,
  usageLabel,
} from "./crmV2Browser";

describe("the four tabs", () => {
  it("names exactly the four initial cards", () => {
    expect(CRM_V2_TABS.map((tab) => tab.id)).toEqual([
      "contacts",
      "organizations",
      "prospects",
      "evidence",
    ]);
  });

  it("explains what each tab reads, so a zero is never mysterious", () => {
    for (const tab of CRM_V2_TABS) {
      expect(tab.description.length).toBeGreaterThan(40);
    }
  });
});

describe("labels never overstate what is known", () => {
  it("calls an unconfirmed row a machine proposal, not a customer", () => {
    expect(confirmationLabel("machine_proposed")).toBe("Propuesta de máquina");
    expect(confirmationLabel("confirmed")).toBe("Confirmado");
    // An unrecognised value is treated as unconfirmed, never as confirmed.
    expect(confirmationLabel("something_new")).toBe("Propuesta de máquina");
  });

  it("names an unattributed channel as such", () => {
    expect(usageLabel("unattributed")).toBe("Sin atribuir");
    expect(usageLabel("shared_mailbox")).toBe("Buzón compartido");
  });

  it("passes an unknown vocabulary value through rather than inventing one", () => {
    expect(resolutionLabel("unresolved")).toBe("Sin resolver");
    expect(resolutionLabel("brand_new")).toBe("brand_new");
    expect(sourceKindLabel("brand_new")).toBe("brand_new");
  });

  it("labels the two staged provenance kinds", () => {
    expect(sourceKindLabel("gmail_message")).toBe("Correo (Gmail)");
    expect(sourceKindLabel("drive_file")).toBe("Documento (Drive)");
  });
});

describe("the filter vocabularies match the closed lists the API accepts", () => {
  it("offers the five resolutions", () => {
    expect([...EVIDENCE_RESOLUTIONS].sort()).toEqual(
      ["ambiguous", "linked", "promoted", "rejected", "unresolved"].sort(),
    );
  });

  it("offers the nine source kinds, including the two staged ones", () => {
    expect(EVIDENCE_SOURCE_KINDS).toHaveLength(9);
    expect(EVIDENCE_SOURCE_KINDS).toContain("gmail_message");
    expect(EVIDENCE_SOURCE_KINDS).toContain("drive_file");
  });
});

describe("counting", () => {
  it("builds the footer from the server total, not from the page", () => {
    expect(pageFooter(9460, 50, 0)).toBe("1–50 de 9.460");
    expect(pageFooter(9460, 50, 9450)).toBe("9.451–9.460 de 9.460");
    expect(pageFooter(0, 50, 0)).toBe("Sin resultados");
  });

  it("does not run the last page past the total", () => {
    expect(pageFooter(12, 50, 0)).toBe("1–12 de 12");
  });

  it("notes a truncated card list only when something was actually hidden", () => {
    expect(cappedListNote(100, 4321)).toBe("Mostrando 100 de 4.321");
    expect(cappedListNote(7, 7)).toBeNull();
    expect(cappedListNote(7, 3)).toBeNull();
  });

  it("prefers the declared count but never reports fewer rows than are on screen", () => {
    expect(cardCount({ evidence: 4321 }, "evidence", 100)).toBe(4321);
    // A response shape we do not understand must not shrink a visible list.
    expect(cardCount({}, "evidence", 100)).toBe(100);
    expect(cardCount({ evidence: 3 }, "evidence", 100)).toBe(100);
  });
});

describe("absences are explained, not left blank", () => {
  it("says why a role mailbox has no person", () => {
    expect(noPersonExplanation("shared_mailbox")).toContain("buzón de rol");
  });

  it("says why a personal-looking address still has no person", () => {
    expect(noPersonExplanation("unattributed")).toContain("inferencia de identidad");
  });

  it("says why a domain did not attach a channel to an institution", () => {
    expect(NO_ORGANIZATION_EXPLANATION).toContain("pista de ruteo");
  });
});
