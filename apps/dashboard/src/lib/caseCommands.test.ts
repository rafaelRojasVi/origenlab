import { describe, expect, it } from "vitest";

import { card, organization } from "./__fixtures__/commercialCase";
import {
  CASE_PREVIEW_ONLY_REASON,
  caseCommandPreviews,
  emptyCaseCommandContext,
  type CaseCommandContext,
  type CaseCommandId,
  type CaseCommandPreview,
} from "./caseCommands";

/*
 * These previews are computed, never sent. Every fixture is invented; no case exists in any
 * database this repository can reach. What is under test is what the dashboard *says* a
 * decision would record — which is the half of the command boundary that is cheapest to get
 * wrong and cheapest to check.
 */

function previewsOf(over: Partial<CaseCommandContext> = {}): CaseCommandPreview[] {
  return caseCommandPreviews({ ...emptyCaseCommandContext(card()), ...over });
}

function preview(id: CaseCommandId, over: Partial<CaseCommandContext> = {}): CaseCommandPreview {
  const found = previewsOf(over).find((candidate) => candidate.id === id);
  if (!found) {
    throw new Error(`no preview for ${id}`);
  }
  return found;
}

describe("the six previews", () => {
  it("returns all six, always in the same order", () => {
    expect(previewsOf().map((row) => row.id)).toEqual([
      "open_commercial_case",
      "link_case_evidence",
      "add_case_organization",
      "set_case_organization_role",
      "record_case_interest",
      "advance_case_stage",
    ]);
  });

  it("blocks every one of them when nothing has been chosen", () => {
    // The state the screen actually renders: an operator has chosen nothing, so no command
    // is available and each says what it is still missing.
    expect(previewsOf().every((row) => row.availability === "blocked")).toBe(true);
    expect(previewsOf().every((row) => row.request === null)).toBe(true);
  });

  it("shows a blocked command rather than hiding it", () => {
    expect(previewsOf().every((row) => row.blockers.length > 0)).toBe(true);
    expect(previewsOf().every((row) => row.label.length > 0)).toBe(true);
  });

  it("demands a motive from every command", () => {
    for (const row of previewsOf()) {
      expect(row.blockers.some((blocker) => blocker.includes("motivo"))).toBe(true);
    }
  });

  it("names the proxy as the reason nothing is sent", () => {
    expect(CASE_PREVIEW_ONLY_REASON).toContain("POST /v2/commands/*");
    expect(CASE_PREVIEW_ONLY_REASON).toContain("proxy");
  });
});

describe("open_commercial_case", () => {
  it("requires the document the case exists because of", () => {
    const row = preview("open_commercial_case", { title: "Caso", note: "Motivo" });
    expect(row.availability).toBe("blocked");
    expect(row.blockers.join(" ")).toContain("documento que causó el caso");
  });

  it("builds the exact request once title, origin and motive are present", () => {
    const row = preview("open_commercial_case", {
      title: "  Osmómetro para laboratorio ficticio  ",
      originSourceRecordId: "sr-1",
      note: "  El correo pide cotización  ",
    });
    expect(row.availability).toBe("available");
    expect(row.request).toEqual({
      title: "Osmómetro para laboratorio ficticio",
      origin_source_record_id: "sr-1",
      note: "El correo pide cotización",
    });
  });

  it("does not take a case, so it is available with no case open", () => {
    const row = caseCommandPreviews({
      ...emptyCaseCommandContext(null),
      title: "Caso",
      originSourceRecordId: "sr-1",
      note: "Motivo",
    })[0];
    expect(row.availability).toBe("available");
  });

  it("says out loud that it neither chooses the stage nor names who is asking", () => {
    const row = preview("open_commercial_case");
    expect(row.doesNot.join(" ")).toContain("No fija la institución solicitante");
    expect(row.doesNot.join(" ")).toContain("No permite elegir la etapa inicial");
  });
});

describe("commands about an existing case", () => {
  const withoutCase = { card: null, note: "Motivo" } satisfies Partial<CaseCommandContext>;

  it("refuses the other five when no case is open", () => {
    for (const id of [
      "link_case_evidence",
      "add_case_organization",
      "set_case_organization_role",
      "record_case_interest",
      "advance_case_stage",
    ] as const) {
      expect(preview(id, withoutCase).blockers.join(" ")).toContain("Abre o elige un caso");
    }
  });

  it("refuses all five on a closed case", () => {
    const closed = {
      card: card({ stage: "lost", closed_at: "2026-09-22T14:00:00Z" }),
      note: "Motivo",
    };
    for (const id of [
      "link_case_evidence",
      "add_case_organization",
      "record_case_interest",
      "advance_case_stage",
    ] as const) {
      expect(preview(id, closed).blockers.join(" ")).toContain("cerrado");
    }
  });

  it("carries the version the operator was shown, as a compare-and-set", () => {
    const row = preview("link_case_evidence", {
      card: card({ version: 7 }),
      note: "Motivo",
      selectedRelation: "mentions",
      evidenceSubject: { kind: "source_record_id", id: "sr-2" },
    });
    expect(row.request).toEqual({
      opportunity_id: "op-1",
      opportunity_version: 7,
      relation: "mentions",
      source_record_id: "sr-2",
      note: "Motivo",
    });
  });
});

describe("link_case_evidence", () => {
  it("names the one subject field the operator picked, and no other", () => {
    const row = preview("link_case_evidence", {
      note: "Motivo",
      selectedRelation: "contradicts",
      evidenceSubject: { kind: "assertion_id", id: "as-1" },
    });
    expect(row.request).toMatchObject({ assertion_id: "as-1", relation: "contradicts" });
    expect(row.request).not.toHaveProperty("source_record_id");
    expect(row.request).not.toHaveProperty("message_id");
    expect(row.request).not.toHaveProperty("notice_id");
  });

  it("requires a reading and offers none by default", () => {
    const row = preview("link_case_evidence", {
      note: "Motivo",
      evidenceSubject: { kind: "source_record_id", id: "sr-2" },
    });
    expect(row.blockers.join(" ")).toContain("No hay lectura por omisión");
  });
});

describe("add_case_organization", () => {
  const base = {
    note: "Motivo",
    selectedOrganizationId: "org-9",
    selectedOrganizationVersion: 3,
  } satisfies Partial<CaseCommandContext>;

  it("has no default role and says why", () => {
    const row = preview("add_case_organization", base);
    expect(row.blockers.join(" ")).toContain("No hay rol por omisión");
  });

  it("builds the request with the organization version the operator saw", () => {
    const row = preview("add_case_organization", { ...base, selectedRole: "funder" });
    expect(row.availability).toBe("available");
    expect(row.request).toEqual({
      opportunity_id: "op-1",
      opportunity_version: 1,
      organization_id: "org-9",
      organization_version: 3,
      role: "funder",
      note: "Motivo",
    });
  });

  it("refuses a second current requesting institution", () => {
    const row = preview("add_case_organization", {
      ...base,
      selectedRole: "requesting_institution",
      card: card({
        organizations: [
          organization({ role: "requesting_institution", name: "Universidad Ficticia" }),
        ],
      }),
    });
    expect(row.blockers.join(" ")).toContain("ya tiene institución solicitante");
  });

  it("refuses repeating a part the institution already holds", () => {
    const row = preview("add_case_organization", {
      ...base,
      selectedRole: "funder",
      card: card({
        organizations: [organization({ organization_id: "org-9", role: "funder" })],
      }),
    });
    expect(row.blockers.join(" ")).toContain("ya tiene ese rol vigente");
  });

  it("describes the supplier exception without predicting whether it applies", () => {
    const row = preview("add_case_organization", {
      ...base,
      selectedRole: "requesting_institution",
    });
    const cautions = row.cautions.join(" ");
    expect(cautions).toContain("crm.organization_relationship");
    expect(cautions).toContain("no lo adivina");
  });

  it("says nothing about the supplier exception for any other role", () => {
    const row = preview("add_case_organization", { ...base, selectedRole: "mentioned" });
    expect(row.cautions.join(" ")).not.toContain("crm.organization_relationship");
  });

  it("refuses to claim it records a permanent commercial role", () => {
    const row = preview("add_case_organization", base);
    expect(row.doesNot.join(" ")).toContain("crm.organization_relationship");
  });
});

describe("set_case_organization_role", () => {
  it("asks for the row, not the institution, and says why", () => {
    const row = preview("set_case_organization_role", { note: "Motivo" });
    expect(row.blockers.join(" ")).toContain("varios papeles");
  });

  it("refuses a closed row: history is not edited", () => {
    const row = preview("set_case_organization_role", {
      note: "Motivo",
      selectedCaseOrganizationId: "oo-old",
      selectedRole: "funder",
      card: card({
        organizations: [
          organization({
            opportunity_organization_id: "oo-old",
            is_current: false,
            valid_to: "2026-09-22",
          }),
        ],
      }),
    });
    expect(row.blockers.join(" ")).toContain("historia no se edita");
  });

  it("describes confirming a machine proposal as an in-place move", () => {
    const row = preview("set_case_organization_role", {
      note: "Motivo",
      selectedCaseOrganizationId: "oo-1",
      selectedRole: "mentioned",
      card: card({
        organizations: [
          organization({ role: "mentioned", confirmation: "machine_proposed" }),
        ],
      }),
    });
    expect(row.availability).toBe("available");
    expect(row.cautions.join(" ")).toContain("la lectura no cambia");
    expect(row.writes.join(" ")).toContain("Nada más cambia");
  });

  it("describes a real change as closing one row and opening another", () => {
    const row = preview("set_case_organization_role", {
      note: "Motivo",
      selectedCaseOrganizationId: "oo-1",
      selectedRole: "end_user_institution",
      card: card({ organizations: [organization({ role: "mentioned" })] }),
    });
    expect(row.cautions.join(" ")).toContain("cierra la fila actual y abre otra");
    expect(row.writes.join(" ")).toContain("valid_to = hoy");
    expect(row.doesNot.join(" ")).toContain("No borra ni reescribe");
  });
});

describe("record_case_interest", () => {
  it("needs at least one of the three subjects", () => {
    const row = preview("record_case_interest", { note: "Motivo" });
    expect(row.blockers.join(" ")).toContain("al menos una de tres cosas");
  });

  it("accepts a bare model string, which is how an interest usually begins", () => {
    const row = preview("record_case_interest", {
      note: "Motivo",
      interestModelText: "  Centrífuga CX-0  ",
    });
    expect(row.availability).toBe("available");
    expect(row.request).toEqual({
      opportunity_id: "op-1",
      opportunity_version: 1,
      model_text: "Centrífuga CX-0",
      note: "Motivo",
    });
  });

  it("refuses a quantity that is not a positive number", () => {
    for (const quantity of ["0", "-2", "dos"]) {
      const row = preview("record_case_interest", {
        note: "Motivo",
        interestModelText: "CX-0",
        interestQuantity: quantity,
      });
      expect(row.blockers.join(" ")).toContain("mayor que cero");
    }
  });

  it("refuses a unit with no quantity beside it", () => {
    const row = preview("record_case_interest", {
      note: "Motivo",
      interestModelText: "CX-0",
      interestQuantityUnit: "unidades",
    });
    expect(row.blockers.join(" ")).toContain("nombra también la cantidad");
  });

  it("carries no price field and says there is no room for one", () => {
    const row = preview("record_case_interest", {
      note: "Motivo",
      interestModelText: "CX-0",
      interestQuantity: "2",
      interestQuantityUnit: "unidades",
    });
    expect(Object.keys(row.request ?? {})).toEqual([
      "opportunity_id",
      "opportunity_version",
      "model_text",
      "quantity",
      "quantity_unit",
      "note",
    ]);
    expect(row.cautions.join(" ")).toContain("quote_revision");
  });
});

describe("advance_case_stage", () => {
  it("refuses a move the API's own table does not allow", () => {
    const row = preview("advance_case_stage", { note: "Motivo", targetStage: "quoting" });
    expect(row.blockers.join(" ")).toContain("no se puede ir a");
  });

  it("allows a move the table does allow", () => {
    const row = preview("advance_case_stage", { note: "Motivo", targetStage: "qualifying" });
    expect(row.availability).toBe("available");
    expect(row.request).toEqual({
      opportunity_id: "op-1",
      opportunity_version: 1,
      stage: "qualifying",
      note: "Motivo",
    });
  });

  it("refuses `won` by name rather than by a later constraint violation", () => {
    const negotiating = card({
      stage: "negotiating",
      stage_machine: {
        ...card().stage_machine,
        stage: "negotiating",
        allowed_next_stages: ["won", "lost", "quoting", "abandoned"],
      },
    });
    const row = preview("advance_case_stage", {
      card: negotiating,
      note: "Motivo",
      targetStage: "won",
    });
    expect(row.blockers.join(" ")).toContain("ningún comando V2 crea cotizaciones");
  });

  it("requires a motive for a closing stage, and refuses one for any other", () => {
    expect(
      preview("advance_case_stage", { note: "Motivo", targetStage: "abandoned" })
        .blockers.join(" "),
    ).toContain("exige un motivo");

    expect(
      preview("advance_case_stage", {
        note: "Motivo",
        targetStage: "qualifying",
        closeReason: "Ya no responden",
      }).blockers.join(" "),
    ).toContain("etapa que no cierra el caso");
  });

  it("closes a case with a motive", () => {
    const row = preview("advance_case_stage", {
      note: "Motivo",
      targetStage: "abandoned",
      closeReason: "  La institución compró por otra vía  ",
    });
    expect(row.availability).toBe("available");
    expect(row.request).toEqual({
      opportunity_id: "op-1",
      opportunity_version: 1,
      stage: "abandoned",
      close_reason: "La institución compró por otra vía",
      note: "Motivo",
    });
  });

  it("reports the requesting institution a later stage needs", () => {
    const qualifying = card({
      stage: "qualifying",
      stage_machine: {
        ...card().stage_machine,
        stage: "qualifying",
        allowed_next_stages: ["qualified", "lead", "abandoned", "lost"],
      },
    });
    const row = preview("advance_case_stage", {
      card: qualifying,
      note: "Motivo",
      targetStage: "qualified",
    });
    expect(row.blockers.join(" ")).toContain("institución solicitante");
  });

  it("says no timer or importer may write a stage", () => {
    const row = preview("advance_case_stage", { note: "Motivo" });
    expect(row.doesNot.join(" ")).toContain("temporizador");
  });
});
