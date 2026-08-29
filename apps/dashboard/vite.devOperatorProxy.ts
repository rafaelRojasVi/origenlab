/**
 * Development-only operator identity for the Vite dev proxy.
 *
 * Production never runs this file's logic: `vite build` does not invoke
 * `server.proxy`, and this module is only ever imported from `vite.config.ts`
 * (a Node-side build tool file, never bundled into client JS). The header
 * this module builds is injected by the Vite dev-server process itself, the
 * same way `apps/dashboard-proxy` (the production Cloudflare Worker) injects
 * `X-OriginLab-Operator-Email` after its own auth — never something the
 * browser can set, in dev or production.
 *
 * Disabled by default: unset `ORIGENLAB_DEV_OPERATOR_EMAIL` (a plain env var,
 * deliberately NOT `VITE_`-prefixed so Vite never exposes it to client code
 * via `import.meta.env`) and no header is injected, matching real proxy
 * behavior with no trusted identity present.
 */

export const DEV_OPERATOR_EMAIL_ENV_VAR = "ORIGENLAB_DEV_OPERATOR_EMAIL";
export const OPERATOR_EMAIL_HEADER = "X-OriginLab-Operator-Email";

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
