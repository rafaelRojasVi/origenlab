/**
 * Dashboard sign-in through the Worker: Google Workspace login on `apps/api`.
 *
 * The Worker was built never to carry cookies or redirects, and for every other path it
 * still does not. Sign-in needs three exceptions, and each is as narrow as it can be:
 *
 * 1. **Two cookies travel upstream**, and only on `/auth/*` and `/v2/*`: the API's session
 *    cookie and its sign-in transaction cookie. Every other cookie the browser holds for
 *    this hostname -- Cloudflare Access's `CF_Authorization` included -- is dropped, so the
 *    upstream never sees a credential it has no use for.
 * 2. **Two `Set-Cookie` names travel back**, and only from `/auth/*`. Anything else the
 *    upstream tries to set is still stripped.
 * 3. **Two redirects are passed through**, each checked against where it may lead: the login
 *    route may only send the browser to Google's authorization endpoint, and the callback
 *    may only send it back to this dashboard's own root. Every other upstream redirect is
 *    still a 502.
 *
 * The cookie names carry the `__Host-` prefix because production is HTTPS; the API uses the
 * unprefixed names only on plain-HTTP loopback, which never runs behind this Worker.
 */

export const AUTH_SESSION_COOKIE = "__Host-origenlab_session";
export const AUTH_SIGNIN_COOKIE = "__Host-origenlab_signin";
const AUTH_COOKIE_NAMES: ReadonlySet<string> = new Set([
  AUTH_SESSION_COOKIE,
  AUTH_SIGNIN_COOKIE,
]);

export const AUTH_LOGIN_PATH = "/auth/google/login";
export const AUTH_CALLBACK_PATH = "/auth/google/callback";
export const AUTH_SESSION_PATH = "/auth/session";
export const AUTH_LOGOUT_PATH = "/auth/logout";

const GOOGLE_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth";
const LOGIN_ERROR_RE = /^[a-z_]{1,40}$/;

function pathOnly(pathname: string): string {
  return pathname.split("?")[0];
}

/** `/auth/*` paths, whose responses may set the two sign-in cookies. */
export function isAuthPath(pathname: string): boolean {
  const path = pathOnly(pathname);
  return (
    path === AUTH_LOGIN_PATH ||
    path === AUTH_CALLBACK_PATH ||
    path === AUTH_SESSION_PATH ||
    path === AUTH_LOGOUT_PATH
  );
}

/** Paths whose upstream needs the session cookie to resolve the operator. */
export function forwardsSessionCookie(pathname: string): boolean {
  const path = pathOnly(pathname);
  return isAuthPath(path) || path.startsWith("/v2/");
}

/** Keep only the two OrigenLab sign-in cookies from a browser `Cookie` header. */
export function filterAuthCookieHeader(cookieHeader: string | null): string | null {
  if (!cookieHeader) {
    return null;
  }
  const kept: string[] = [];
  for (const part of cookieHeader.split(";")) {
    const pair = part.trim();
    const eq = pair.indexOf("=");
    if (eq <= 0) {
      continue;
    }
    if (AUTH_COOKIE_NAMES.has(pair.slice(0, eq).trim())) {
      kept.push(pair);
    }
  }
  return kept.length > 0 ? kept.join("; ") : null;
}

/** `Set-Cookie` values from the upstream that name one of the two sign-in cookies. */
export function filterAuthSetCookies(upstream: Headers): string[] {
  return upstream.getSetCookie().filter((value) => {
    const eq = value.indexOf("=");
    return eq > 0 && AUTH_COOKIE_NAMES.has(value.slice(0, eq).trim());
  });
}

/**
 * Whether an upstream redirect from `upstreamPath` to `location` may reach the browser.
 *
 * `dashboardOrigin` is the origin of the request the Worker received -- the dashboard's own
 * origin -- so the callback can send the browser nowhere else.
 */
export function isAllowedAuthRedirect(
  upstreamPath: string,
  location: string | null,
  dashboardOrigin: string,
): boolean {
  if (!location) {
    return false;
  }
  let target: URL;
  try {
    target = new URL(location);
  } catch {
    return false;
  }
  const path = pathOnly(upstreamPath);
  if (path === AUTH_LOGIN_PATH) {
    return `${target.origin}${target.pathname}` === GOOGLE_AUTHORIZATION_ENDPOINT;
  }
  if (path === AUTH_CALLBACK_PATH) {
    if (target.origin !== dashboardOrigin || target.pathname !== "/" || target.hash) {
      return false;
    }
    const keys = [...target.searchParams.keys()];
    if (keys.length === 0) {
      return true;
    }
    return (
      keys.length === 1 &&
      keys[0] === "login_error" &&
      LOGIN_ERROR_RE.test(target.searchParams.get("login_error") ?? "")
    );
  }
  return false;
}
