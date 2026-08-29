/**
 * Development-only operator identity for the Vite dev proxy.
 *
 * Production never runs this file's logic: `vite build` does not invoke
 * `server.proxy`, and this module is only ever imported from `vite.config.ts`
 * (a Node-side build tool file, never bundled into client JS). The header
 * this module builds is injected by the Vite dev-server process itself, the
 * same way `apps/dashboard-proxy` (the production Cloudflare Worker) injects
 * `X-OriginLab-Operator-Email` after its own auth.
 *
 * The dev proxy must uphold the same invariant the production proxy does:
 * a browser/client-supplied `X-OriginLab-Operator-Email` is never trusted.
 * `buildDevOperatorProxyConfigure` always strips any inbound value first
 * (`stripAndInjectDevOperatorHeader`) before conditionally injecting the
 * server-side trusted value -- unlike only installing a `proxyReq` listener
 * when a trusted value exists, which would let an unconfigured dev server
 * forward whatever the browser sent unchanged.
 *
 * Disabled by default: unset `ORIGENLAB_DEV_OPERATOR_EMAIL` (a plain env var,
 * deliberately NOT `VITE_`-prefixed so Vite never exposes it to client code
 * via `import.meta.env`) and no header is injected, matching real proxy
 * behavior with no trusted identity present.
 */

export const DEV_OPERATOR_EMAIL_ENV_VAR = "ORIGENLAB_DEV_OPERATOR_EMAIL";
export const OPERATOR_EMAIL_HEADER = "X-OriginLab-Operator-Email";

interface ProxyOutgoingRequest {
  setHeader: (name: string, value: string) => void;
  removeHeader: (name: string) => void;
}

function looksLikeEmail(value: string): boolean {
  if (value.length === 0 || value.length > 320) return false;
  const at = value.indexOf("@");
  if (at <= 0 || at !== value.lastIndexOf("@")) return false;
  const domain = value.slice(at + 1);
  return domain.includes(".") && domain.length > 2;
}

/**
 * Returns the dev-only operator header to inject, or `undefined` when no
 * email is configured or the command is not the dev server (`vite build`
 * never wants this, even if the env var happens to be set in the shell).
 */
export function buildDevOperatorProxyHeaders(
  env: NodeJS.ProcessEnv,
  command: "serve" | "build",
): Record<string, string> | undefined {
  if (command !== "serve") return undefined;

  const raw = (env[DEV_OPERATOR_EMAIL_ENV_VAR] ?? "").trim().toLowerCase();
  if (!looksLikeEmail(raw)) return undefined;

  return { [OPERATOR_EMAIL_HEADER]: raw };
}

/**
 * Always strips any inbound `X-OriginLab-Operator-Email` (node-http-proxy
 * has already copied the original request's headers onto `proxyReq` by the
 * time the `proxyReq` event fires), then re-sets it only when a trusted
 * server-side value is configured. A client can therefore never make its
 * own header value reach the upstream API.
 */
export function stripAndInjectDevOperatorHeader(
  proxyReq: ProxyOutgoingRequest,
  devOperatorHeaders: Record<string, string> | undefined,
): void {
  proxyReq.removeHeader(OPERATOR_EMAIL_HEADER);

  if (!devOperatorHeaders) return;

  for (const [name, value] of Object.entries(devOperatorHeaders)) {
    proxyReq.setHeader(name, value);
  }
}

/**
 * Builds the Vite `server.proxy.*.configure` callback. Unlike returning
 * `undefined` when no trusted value is configured (which would skip
 * installing a `proxyReq` listener at all, leaving a browser-supplied
 * header unstripped), this is always a real callback -- it always strips,
 * and only conditionally injects.
 */
export function buildDevOperatorProxyConfigure(
  env: NodeJS.ProcessEnv,
  command: "serve" | "build",
): (proxy: {
  on: (
    event: "proxyReq",
    listener: (proxyReq: ProxyOutgoingRequest, req: unknown) => void,
  ) => void;
}) => void {
  const devOperatorHeaders = buildDevOperatorProxyHeaders(env, command);

  return (proxy) => {
    proxy.on("proxyReq", (proxyReq) => {
      stripAndInjectDevOperatorHeader(proxyReq, devOperatorHeaders);
    });
  };
}
