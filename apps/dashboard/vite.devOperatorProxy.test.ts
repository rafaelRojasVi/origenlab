import { describe, expect, it } from "vitest";
import {
  buildDevOperatorProxyHeaders,
  DEV_OPERATOR_EMAIL_ENV_VAR,
  OPERATOR_EMAIL_HEADER,
} from "./vite.devOperatorProxy";

describe("buildDevOperatorProxyHeaders", () => {
  it("injects nothing when the env var is unset (disabled by default)", () => {
    expect(buildDevOperatorProxyHeaders({}, "serve")).toBeUndefined();
  });

  it("injects nothing during a production build even if the env var is set", () => {
    const env = { [DEV_OPERATOR_EMAIL_ENV_VAR]: "dev-operator@origenlab.cl" };
    expect(buildDevOperatorProxyHeaders(env, "build")).toBeUndefined();
  });

  it("injects the header during `vite dev` when a valid email is configured", () => {
    const env = { [DEV_OPERATOR_EMAIL_ENV_VAR]: "Dev-Operator@Origenlab.cl" };
    expect(buildDevOperatorProxyHeaders(env, "serve")).toEqual({
      [OPERATOR_EMAIL_HEADER]: "dev-operator@origenlab.cl",
    });
  });

  it("rejects a value that is not a plausible email", () => {
    const env = { [DEV_OPERATOR_EMAIL_ENV_VAR]: "not-an-email" };
    expect(buildDevOperatorProxyHeaders(env, "serve")).toBeUndefined();
  });

  it("rejects an empty string", () => {
    const env = { [DEV_OPERATOR_EMAIL_ENV_VAR]: "   " };
    expect(buildDevOperatorProxyHeaders(env, "serve")).toBeUndefined();
  });
});
