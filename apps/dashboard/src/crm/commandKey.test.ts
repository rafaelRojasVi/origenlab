import { describe, expect, it } from "vitest";
import { OperatorApiError } from "../api/operatorClient";
import { CommandKey, settlesAttempt } from "./commandKey";

const refused = (status: number, code: string) =>
  new OperatorApiError(JSON.stringify({ error: { code: "conflict", message: "x", details: { code, message: "x" } } }), status);

describe("CommandKey", () => {
  const body = { person_id: "p1", expected_version: 1, display_name: "Nombre", note: "nota" };

  it("resends the identical body under the same key after a network failure or a 5xx", () => {
    const key = new CommandKey();
    const first = key.keyFor(body);
    key.settle(new TypeError("Failed to fetch"));
    expect(key.keyFor({ ...body })).toBe(first);
    key.settle(new OperatorApiError("<html>502</html>", 502));
    expect(key.keyFor(body)).toBe(first);
    key.settle(refused(503, "service_busy"));
    expect(key.keyFor(body)).toBe(first);
  });

  it("a different body never reuses a pending key", () => {
    const key = new CommandKey();
    const first = key.keyFor(body);
    key.settle(new TypeError("Failed to fetch"));
    expect(key.keyFor({ ...body, display_name: "Otro" })).not.toBe(first);
  });

  it("rotates after a success", () => {
    const key = new CommandKey();
    const first = key.keyFor(body);
    key.settle();
    expect(key.keyFor(body)).not.toBe(first);
  });

  it.each([
    [409, "stale_version"],
    [409, "duplicate"],
    [409, "idempotency_key_reused"],
    [422, "validation_error"],
    [403, "path_not_allowed"],
  ])("rotates after a %s %s refusal", (status, code) => {
    const key = new CommandKey();
    const first = key.keyFor(body);
    key.settle(refused(status, code));
    expect(key.keyFor(body)).not.toBe(first);
  });

  it.each(["command_in_progress", "idempotency_conflict", "record_busy"])(
    "keeps the key after %s: the first request under it may still commit",
    (code) => {
      const key = new CommandKey();
      const first = key.keyFor(body);
      key.settle(refused(409, code));
      expect(key.keyFor(body)).toBe(first);
    },
  );

  it("settlesAttempt says which answers end an attempt", () => {
    expect(settlesAttempt()).toBe(true);
    expect(settlesAttempt(refused(409, "stale_version"))).toBe(true);
    expect(settlesAttempt(refused(409, "command_in_progress"))).toBe(false);
    expect(settlesAttempt(refused(409, "record_busy"))).toBe(false);
    expect(settlesAttempt(new OperatorApiError("", 500))).toBe(false);
    expect(settlesAttempt(new TypeError("Failed to fetch"))).toBe(false);
  });
});
