import { describe, expect, it } from "vitest";

// The app's tsconfig carries no Node types; the test runner is Node all the same.
const env = (globalThis as { process?: { env: Record<string, string | undefined> } }).process?.env ?? {};

// vite.config.ts pins the zone (`process.env.TZ ??= "America/Santiago"`) so a laptop and CI (UTC)
// run the same suite; `TZ=UTC npx vitest run` overrides it to check for zone dependence.
describe("the test clock's zone", () => {
  it("is pinned, never the machine's", () => {
    expect(env.TZ).toBeTruthy();
    expect(Intl.DateTimeFormat().resolvedOptions().timeZone).toBe(env.TZ);
  });
});
