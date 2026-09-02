import { describe, expect, it } from "vitest";
import { newIdempotencyKey } from "./idempotencyKey";

describe("newIdempotencyKey", () => {
  it("prefixes the key with the given kind", () => {
    expect(newIdempotencyKey("quote")).toMatch(/^quote:[0-9a-f-]+$/);
  });

  it("generates a different key on each call", () => {
    expect(newIdempotencyKey("quote")).not.toBe(newIdempotencyKey("quote"));
  });

  it("supports other kinds", () => {
    expect(newIdempotencyKey("opportunity")).toMatch(/^opportunity:[0-9a-f-]+$/);
  });
});
