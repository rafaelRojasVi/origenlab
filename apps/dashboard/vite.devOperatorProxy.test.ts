import { describe, expect, it, vi } from "vitest";
import {
  buildDevOperatorProxyConfigure,
  buildDevOperatorProxyHeaders,
  DEV_OPERATOR_EMAIL_ENV_VAR,
  OPERATOR_EMAIL_HEADER,
  stripAndInjectDevOperatorHeader,
} from "./vite.devOperatorProxy";

function fakeProxyReq() {
  return { setHeader: vi.fn(), removeHeader: vi.fn() };
}

/** Simulates node-http-proxy's `proxy.on("proxyReq", ...)` registration. */
function capturedProxyReqListener(
  configure: (proxy: { on: (event: "proxyReq", listener: (proxyReq: unknown, req: unknown) => void) => void }) => void,
) {
  let listener: ((proxyReq: unknown, req: unknown) => void) | undefined;
  configure({
    on: (_event, fn) => {
      listener = fn;
    },
  });
  if (!listener) throw new Error("configure did not register a proxyReq listener");
  return listener;
}

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

describe("stripAndInjectDevOperatorHeader", () => {
  it("strips any inbound operator header and injects nothing when no trusted value is configured", () => {
    const proxyReq = fakeProxyReq();
    stripAndInjectDevOperatorHeader(proxyReq, undefined);

    expect(proxyReq.removeHeader).toHaveBeenCalledWith(OPERATOR_EMAIL_HEADER);
    expect(proxyReq.setHeader).not.toHaveBeenCalled();
  });

  it("strips the inbound header before injecting the configured trusted value", () => {
    const proxyReq = fakeProxyReq();
    const calls: string[] = [];
    proxyReq.removeHeader.mockImplementation(() => calls.push("removeHeader"));
    proxyReq.setHeader.mockImplementation(() => calls.push("setHeader"));

    stripAndInjectDevOperatorHeader(proxyReq, { [OPERATOR_EMAIL_HEADER]: "dev-operator@origenlab.cl" });

    expect(proxyReq.removeHeader).toHaveBeenCalledWith(OPERATOR_EMAIL_HEADER);
    expect(proxyReq.setHeader).toHaveBeenCalledWith(OPERATOR_EMAIL_HEADER, "dev-operator@origenlab.cl");
    expect(calls).toEqual(["removeHeader", "setHeader"]);
  });
});

describe("buildDevOperatorProxyConfigure", () => {
  it("strips a browser-supplied operator header when the env var is unset (no injection)", () => {
    const listener = capturedProxyReqListener(buildDevOperatorProxyConfigure({}, "serve"));
    const proxyReq = fakeProxyReq();

    listener(proxyReq, {});

    expect(proxyReq.removeHeader).toHaveBeenCalledWith(OPERATOR_EMAIL_HEADER);
    expect(proxyReq.setHeader).not.toHaveBeenCalled();
  });

  it("replaces a browser-supplied operator header with the trusted server-side value when configured", () => {
    const env = { [DEV_OPERATOR_EMAIL_ENV_VAR]: "dev-operator@origenlab.cl" };
    const listener = capturedProxyReqListener(buildDevOperatorProxyConfigure(env, "serve"));
    const proxyReq = fakeProxyReq();

    listener(proxyReq, {});

    expect(proxyReq.removeHeader).toHaveBeenCalledWith(OPERATOR_EMAIL_HEADER);
    expect(proxyReq.setHeader).toHaveBeenCalledWith(OPERATOR_EMAIL_HEADER, "dev-operator@origenlab.cl");
  });

  it("strips a browser-supplied operator header and injects nothing in build mode, even if the env var is set", () => {
    const env = { [DEV_OPERATOR_EMAIL_ENV_VAR]: "dev-operator@origenlab.cl" };
    const listener = capturedProxyReqListener(buildDevOperatorProxyConfigure(env, "build"));
    const proxyReq = fakeProxyReq();

    listener(proxyReq, {});

    expect(proxyReq.removeHeader).toHaveBeenCalledWith(OPERATOR_EMAIL_HEADER);
    expect(proxyReq.setHeader).not.toHaveBeenCalled();
  });

  it("strips a browser-supplied operator header when the configured value is invalid", () => {
    const env = { [DEV_OPERATOR_EMAIL_ENV_VAR]: "not-an-email" };
    const listener = capturedProxyReqListener(buildDevOperatorProxyConfigure(env, "serve"));
    const proxyReq = fakeProxyReq();

    listener(proxyReq, {});

    expect(proxyReq.removeHeader).toHaveBeenCalledWith(OPERATOR_EMAIL_HEADER);
    expect(proxyReq.setHeader).not.toHaveBeenCalled();
  });
});
