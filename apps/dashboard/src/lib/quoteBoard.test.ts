import { describe, expect, it } from "vitest";
import {
  REVISION_TRANSITIONS,
  legalActionsForRevisionStatus,
  resolveBoardMove,
} from "./quoteBoard";

describe("resolveBoardMove", () => {
  it("resolves review -> approved_to_send as approve, no confirmation", () => {
    const decision = resolveBoardMove("review", "approved_to_send");
    expect(decision).toEqual({
      allowed: true,
      command: "approve",
      requiresConfirmation: false,
      label: "Aprobar",
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

  it("refuses a no-op drop onto the same lane (submit_for_review/request_adjustments are never drag-triggerable post-collapse)", () => {
    expect(resolveBoardMove("review", "review").allowed).toBe(false);
  });

  it("refuses skipping stages forward", () => {
    expect(resolveBoardMove("review", "sent_follow_up").allowed).toBe(false);
  });

  it("refuses moving a sent revision backwards to any lane", () => {
    expect(resolveBoardMove("sent_follow_up", "approved_to_send").allowed).toBe(false);
    expect(resolveBoardMove("sent_follow_up", "review").allowed).toBe(false);
  });

  it("refuses backmoves that skip a stage", () => {
    expect(resolveBoardMove("approved_to_send", "review").allowed).toBe(false);
  });

  it("refuses moving a Drive-only card into any CRM lane", () => {
    for (const target of ["review", "approved_to_send", "sent_follow_up"] as const) {
      const decision = resolveBoardMove("drive_intake", target);
      expect(decision.allowed).toBe(false);
    }
  });

  it("refuses moving a durable CRM card back into the Drive intake lane", () => {
    for (const source of ["review", "approved_to_send", "sent_follow_up"] as const) {
      const decision = resolveBoardMove(source, "drive_intake");
      expect(decision.allowed).toBe(false);
    }
  });

  it("every refusal carries an operator-readable reason", () => {
    const decision = resolveBoardMove("review", "sent_follow_up");
    expect(decision.allowed).toBe(false);
    if (!decision.allowed) {
      expect(decision.reason.length).toBeGreaterThan(0);
    }
  });
});

describe("legalActionsForRevisionStatus", () => {
  it("draft offers only submit_for_review", () => {
    const actions = legalActionsForRevisionStatus("draft");
    expect(actions.map((a) => a.command)).toEqual(["submit_for_review"]);
  });

  it("adjustments_requested offers only submit_for_review", () => {
    const actions = legalActionsForRevisionStatus("adjustments_requested");
    expect(actions.map((a) => a.command)).toEqual(["submit_for_review"]);
  });

  it("pending_approval offers approve and request_adjustments, not submit_for_review", () => {
    const actions = legalActionsForRevisionStatus("pending_approval");
    expect(actions.map((a) => a.command).sort()).toEqual(
      ["approve", "request_adjustments"].sort(),
    );
  });

  it("approved offers only confirm_send", () => {
    const actions = legalActionsForRevisionStatus("approved");
    expect(actions.map((a) => a.command)).toEqual(["confirm_send"]);
  });

  it("sent is terminal for these four commands: no further board actions", () => {
    expect(legalActionsForRevisionStatus("sent")).toEqual([]);
  });

  it("superseded offers no actions (unreachable in this slice, but must fail safe, not throw)", () => {
    expect(legalActionsForRevisionStatus("superseded")).toEqual([]);
  });
});

describe("REVISION_TRANSITIONS", () => {
  it("is the single source of truth legalActionsForRevisionStatus derives from", () => {
    expect(REVISION_TRANSITIONS).toHaveLength(5);
  });
});
