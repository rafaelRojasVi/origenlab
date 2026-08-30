import { describe, expect, it } from "vitest";

const smokeModules = import.meta.glob("../../scripts/smoke-v1.mjs", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

const smokeSource = Object.values(smokeModules)[0] ?? "";

describe("smoke-v1 policy", () => {
  it("loads smoke-v1.mjs for inspection", () => {
    expect(smokeSource.length).toBeGreaterThan(100);
  });

  it("documents Dashboard v1 GET routes and Dashboard-2 contact drilldown", () => {
    expect(smokeSource).toMatch(/GET \/health/);
    expect(smokeSource).toMatch(/\/operator\/status/);
    expect(smokeSource).toMatch(/\/cases\/warm/);
    expect(smokeSource).toMatch(/\/operator\/procurement\/status/);
    expect(smokeSource).toMatch(/\/contacts\//);
    expect(smokeSource).toMatch(/pickContactEmailFromLists/);
    expect(smokeSource).toMatch(/no contact_email in warm rows/);
    expect(smokeSource).not.toMatch(/method:\s*["'](POST|PUT|PATCH|DELETE)["']/i);
  });

  it("does not call legacy dashboard or classification paths", () => {
    const paths = [...smokeSource.matchAll(/path:\s*"([^"]+)"/g)].map((m) => m[1]);
    expect(paths.length).toBe(4);
    for (const path of paths) {
      expect(path).not.toMatch(/\/dashboard\//);
      expect(path).not.toMatch(/\/classification\//);
    }
    expect(smokeSource).toMatch(/FORBIDDEN_LEGACY/);
  });

  it("supports postgres mirror label validation via EXPECT_BACKEND", () => {
    expect(smokeSource).toMatch(/EXPECT_BACKEND/);
    expect(smokeSource).toMatch(/postgres_mirror/);
    expect(smokeSource).toMatch(/operator-postgres-mirror-readonly/);
  });
});
