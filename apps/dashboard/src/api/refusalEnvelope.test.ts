/**
 * The refusal envelope every command answers with (apps/api errors.py), read in one place.
 * Every value is invented.
 */
import { describe, expect, it } from "vitest";
import { OperatorApiError } from "./operatorClient";
import { commandMissing, parseRefusalEnvelope, refusalOfError } from "./refusalEnvelope";

/** The API's envelope: the generic code outside, the command's own (and its details) in `details`. */
const real = (outer: string, code: string, message: string, extra: Record<string, unknown> = {}) => ({
  error: { code: outer, message, details: { code, message, ...extra }, request_id: "req-ejemplo" },
});
const apiError = (status: number, body: unknown) => new OperatorApiError(typeof body === "string" ? body : JSON.stringify(body), status);

describe("parseRefusalEnvelope", () => {
  it("prefers the command's own code and message (error.details), with the other details beside them", () => {
    expect(parseRefusalEnvelope(real("conflict", "stale_version", "changed", { candidates: [1] }))).toEqual({
      code: "stale_version",
      message: "changed",
      details: { candidates: [1] },
    });
  });

  it("falls back to the generic code (a body validation error, the Worker's envelope)", () => {
    expect(parseRefusalEnvelope({ error: { code: "path_not_allowed", message: "no" } })).toEqual({ code: "path_not_allowed", message: "no", details: null });
    expect(parseRefusalEnvelope({ error: { code: "validation_error", message: "bad", details: { validation_errors: [] } } })).toEqual({
      code: "validation_error",
      message: "bad",
      details: { validation_errors: [] },
    });
  });

  it("still reads an older {detail:{code,message}}, and nothing from what is no envelope", () => {
    expect(parseRefusalEnvelope({ detail: { code: "stale_version", message: "old" } })).toMatchObject({ code: "stale_version", message: "old" });
    for (const raw of [null, "text", 5, {}, { detail: "plain" }]) expect(parseRefusalEnvelope(raw).code).toBeNull();
  });
});

describe("refusalOfError", () => {
  it("reads the real envelope: the command's code, its message and the status", () => {
    expect(refusalOfError(apiError(409, real("conflict", "stale_version", "organization was modified")))).toEqual({
      code: "stale_version",
      message: "organization was modified",
      status: 409,
    });
  });

  it("a route the API lacks is its generic «not_found»; text that is no envelope is http_<status>", () => {
    expect(refusalOfError(apiError(404, { error: { code: "not_found", message: "Not Found", details: {} } }))).toMatchObject({ code: "not_found", status: 404 });
    expect(refusalOfError(apiError(502, "<html>bad gateway</html>"))).toEqual({ code: "http_502", message: "<html>bad gateway</html>", status: 502 });
    expect(refusalOfError(apiError(409, { detail: "plain words" }))).toEqual({ code: "http_409", message: "plain words", status: 409 });
  });

  it("the Worker's path refusal, whatever its shape, and nothing for an error that is not the API's", () => {
    expect(refusalOfError(apiError(403, "path_not_allowed: POST /v2/x"))).toMatchObject({ code: "path_not_allowed", status: 403 });
    expect(refusalOfError(new Error("boom"))).toBeNull();
  });
});

describe("commandMissing", () => {
  it("is a command this environment does not have: the Worker's refusal, a route the API lacks, a 405", () => {
    expect(commandMissing(refusalOfError(apiError(403, "path_not_allowed")))).toBe(true);
    expect(commandMissing(refusalOfError(apiError(404, { error: { code: "not_found", message: "Not Found", details: {} } })))).toBe(true);
    expect(commandMissing(refusalOfError(apiError(404, "Not Found")))).toBe(true);
    expect(commandMissing(refusalOfError(apiError(405, { error: { code: "validation_error", message: "Method Not Allowed", details: {} } })))).toBe(true);
  });

  it("is not a command's own 404 (something it looked for is gone), nor any other refusal", () => {
    expect(commandMissing(refusalOfError(apiError(404, real("not_found", "campaign_not_found", "no such campaign"))))).toBe(false);
    expect(commandMissing(refusalOfError(apiError(409, real("conflict", "stale_version", "changed"))))).toBe(false);
    expect(commandMissing(null)).toBe(false);
  });
});
