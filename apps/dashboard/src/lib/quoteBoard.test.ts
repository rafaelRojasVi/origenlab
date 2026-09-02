import { describe, expect, it } from "vitest";
import {
  BOARD_TRANSITIONS,
  legalActionsForStage,
  resolveBoardMove,
} from "./quoteBoard";

describe("resolveBoardMove", () => {
  it("resolves preparation -> review as submit_for_review, no confirmation", () => {
    const decision = resolveBoardMove("preparation", "review");
    expect(decision).toEqual({
      allowed: true,
      command: "submit_for_review",
      requiresConfirmation: false,
      label: "Enviar a revisión",
    });
  });

  it("resolves review -> approved_to_send as approve, no confirmation", () => {
    const decision = resolveBoardMove("review", "approved_to_send");
    expect(decision).toEqual({
      allowed: true,
      command: "approve",
      requiresConfirmation: false,
      label: "Aprobar",
    });
  });

  it("resolves review -> preparation as request_adjustments, requires confirmation", () => {
    const decision = resolveBoardMove("review", "preparation");
    expect(decision).toEqual({
      allowed: true,
      command: "request_adjustments",
      requiresConfirmation: true,
      label: "Solicitar ajustes",
    });
  });

  it("resolves approved_to_send -> sent_follow_up as confirm_send, requires confirmation", () => {
    const decision = resolveBoardMove("approved_to_send", "sent_follow_up");
    expect(decision).toEqual({
      allowed: true,
      command: "confirm_send",
      requiresConfirmation: true,
      label: "Confirmar envío",
    });
  });

  it("refuses skipping stages forward", () => {
    expect(resolveBoardMove("preparation", "approved_to_send").allowed).toBe(false);
    expect(resolveBoardMove("preparation", "sent_follow_up").allowed).toBe(false);
    expect(resolveBoardMove("review", "sent_follow_up").allowed).toBe(false);
  });

  it("refuses moving a sent revision backwards to any lane", () => {
    expect(resolveBoardMove("sent_follow_up", "approved_to_send").allowed).toBe(false);
    expect(resolveBoardMove("sent_follow_up", "review").allowed).toBe(false);
    expect(resolveBoardMove("sent_follow_up", "preparation").allowed).toBe(false);
  });

  it("refuses approving a draft directly (approved_to_send from preparation)", () => {
    expect(resolveBoardMove("preparation", "approved_to_send").allowed).toBe(false);
  });

  it("refuses backmoves that skip a stage", () => {
    expect(resolveBoardMove("approved_to_send", "preparation").allowed).toBe(false);
  });

  it("refuses a no-op drop onto the same lane", () => {
    const decision = resolveBoardMove("review", "review");
    expect(decision.allowed).toBe(false);
  });

  it("refuses moving a Drive-only card into any CRM lane", () => {
    for (const target of ["preparation", "review", "approved_to_send", "sent_follow_up"] as const) {
      const decision = resolveBoardMove("drive_intake", target);
      expect(decision.allowed).toBe(false);
    }
  });

  it("refuses moving a durable CRM card back into the Drive intake lane", () => {
    for (const source of ["preparation", "review", "approved_to_send", "sent_follow_up"] as const) {
      const decision = resolveBoardMove(source, "drive_intake");
      expect(decision.allowed).toBe(false);
    }
  });

  it("every refusal carries an operator-readable reason", () => {
    const decision = resolveBoardMove("preparation", "sent_follow_up");
    expect(decision.allowed).toBe(false);
    if (!decision.allowed) {
      expect(decision.reason.length).toBeGreaterThan(0);
    }
  });
});

describe("legalActionsForStage", () => {
  it("preparation offers only submit_for_review", () => {
    const actions = legalActionsForStage("preparation");
    expect(actions.map((a) => a.command)).toEqual(["submit_for_review"]);
  });

  it("review offers approve and request_adjustments", () => {
    const actions = legalActionsForStage("review");
    expect(actions.map((a) => a.command).sort()).toEqual(
      ["approve", "request_adjustments"].sort(),
    );
  });

  it("approved_to_send offers only confirm_send", () => {
    const actions = legalActionsForStage("approved_to_send");
    expect(actions.map((a) => a.command)).toEqual(["confirm_send"]);
  });

  it("sent_follow_up is terminal: no further board actions", () => {
    expect(legalActionsForStage("sent_follow_up")).toEqual([]);
  });
});

describe("BOARD_TRANSITIONS", () => {
  it("is the single source of truth both functions above derive from", () => {
    expect(BOARD_TRANSITIONS).toHaveLength(4);
  });
});
