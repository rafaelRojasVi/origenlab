#!/usr/bin/env python3
"""Hosted smoke check: is production serving what main says it should? Read-only.

Every GET path the dashboard Worker forwards (apps/dashboard-proxy/src/allowlist.ts) is requested
through the real Worker without a session. A route must answer 401 (mounted and protected), the
public ones their own status; 404 means not deployed, 5xx broken, 200 unprotected, 403 refused
by the Worker. `/api/health` must report a commit that is on main. Inside GitHub Actions, with
the production secrets present, it also checks that every migration in the repo is applied and
that the workers are alive; without them it says NOT CHECKED instead of passing silently.

With OL_SMOKE_SESSION set to your own dashboard session cookie (copy `__Host-origenlab_session`
from the browser after signing in), it also reads every route as you: each must answer 200 with
JSON (404 for the placeholder ids), within 3 s. Run it after a release; sessions last 7 days at
most, and nothing stores the cookie.

It sends only GETs, never prints a response body, and never prints the session.

    OL_SMOKE_SESSION=... python3 apps/api/scripts/hosted_smoke.py [--base-url https://dashboard.origenlab.cl]
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

REPO = Path(__file__).resolve().parents[3]
ALLOWLIST = REPO / "apps" / "dashboard-proxy" / "src" / "allowlist.ts"
MIGRATIONS = REPO / "supabase" / "migrations"
DEFAULT_BASE_URL = "https://dashboard.origenlab.cl"
NIL_UUID = "00000000-0000-0000-0000-000000000000"
UUID_PATTERN = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
FILE_KEY_PATTERN = "[a-z0-9_-]{1,64}"
MAX_SECONDS = 2.0
MAX_AUTHENTICATED_SECONDS = 3.0
SESSION_COOKIE = "__Host-origenlab_session"

# Routes the Worker forwards that answer without a session, by design.
PUBLIC_ROUTES = {
    "/health": "200",
    "/auth/google/login": "3xx",  # redirect to Google
    "/auth/google/callback": "3xx",  # redirect back to the dashboard
}

# Routes mounted only when a deployment switch is on: 401 when on, 404 when off; either is fine.
SWITCHED_ROUTES = {
    "/v2/workspace/marketing/test-send-history": "ORIGENLAB_V2_CAMPAIGN_TEST_SEND_ENABLED",
}

Fetch = Callable[[str], "tuple[int, bytes]"]


class UnsupportedPattern(ValueError):
    """An allowlist entry this script cannot turn into concrete paths. Extend it, never skip."""


# ------------------------------------------------------------------ allowlist → paths


def allowlist_entries(ts_text: str) -> list[tuple[str, list[str]]]:
    """(regex, concrete paths it matches) for every entry of ALLOWED_UPSTREAM_PATHS."""
    start = ts_text.index("export const ALLOWED_UPSTREAM_PATHS")
    block = ts_text[start : ts_text.index("\n];", start)]
    entries = []
    for literal in re.findall(r"^\s*/(\^.*\$)/,?\s*$", block, flags=re.MULTILINE):
        entries.append((literal, _expand(literal)))
    return entries


def _expand(pattern: str) -> list[str]:
    body = pattern[1:-1].replace(UUID_PATTERN, NIL_UUID).replace(FILE_KEY_PATTERN, "paquete").replace("\\/", "/")
    pieces = re.split(r"\(([a-z0-9_|-]+)\)", body)
    choices = [[p] if i % 2 == 0 else p.split("|") for i, p in enumerate(pieces)]
    paths = ["".join(c) for c in itertools.product(*choices)]
    for path in paths:
        if re.search(r"[\\\[\](){}*+?|.^$]", path):
            raise UnsupportedPattern(f"cannot expand allowlist entry /{pattern}/")
    return paths


def expected_status(path: str) -> str:
    if path in SWITCHED_ROUTES:
        return "401|404"
    return PUBLIC_ROUTES.get(path, "401")


# ------------------------------------------------------------------ probing


@dataclass
class Probe:
    path: str
    status: int | None
    seconds: float
    error: str = ""


def probe(path: str, fetch: Fetch, *, clock: Callable[[], float] = time.monotonic) -> Probe:
    """One GET. A network error is retried once, a status never is; an answer over the time
    budget is timed once more (a just-restarted instance warms up per route), and the second
    time is the one judged."""
    result = _probe_once(path, fetch, clock)
    if result.status is None or result.seconds > MAX_SECONDS:
        result = _probe_once(path, fetch, clock)
    return result


def _probe_once(path: str, fetch: Fetch, clock: Callable[[], float]) -> Probe:
    started = clock()
    try:
        status, _ = fetch(path)
        return Probe(path, status, clock() - started)
    except (OSError, urllib.error.URLError) as exc:  # TimeoutError is an OSError
        return Probe(path, None, clock() - started, error=str(exc) or type(exc).__name__)


def _matches(status: int, expected: str) -> bool:
    if expected == "3xx":
        return 300 <= status < 400
    return status in {int(code) for code in expected.split("|")}


def _why(status: int) -> str:
    if status == 404:
        return "not deployed"
    if status >= 500:
        return "broken"
    if status == 200:
        return "unprotected"
    if status == 403:
        return "refused by the Worker"
    return "unexpected"


def route_failures(probes: list[Probe]) -> list[str]:
    failures = []
    for p in probes:
        expected = expected_status(p.path)
        if p.status is None:
            failures.append(f"{p.path}: no answer ({p.error})")
        elif not _matches(p.status, expected):
            failures.append(f"{p.path}: HTTP {p.status}, expected {expected} — {_why(p.status)}")
        elif p.seconds > MAX_SECONDS:
            failures.append(f"{p.path}: {p.seconds:.1f} s, over the {MAX_SECONDS:.0f} s budget")
    return failures


def switch_notes(probes: list[Probe]) -> list[str]:
    return [f"{p.path}: switched off ({SWITCHED_ROUTES[p.path]})"
            for p in probes if p.path in SWITCHED_ROUTES and p.status == 404]


# ------------------------------------------------------------------ with your own session


def session_cookie_header(value: str) -> str:
    """The Cookie header for the session: a bare value, or `__Host-origenlab_session=<value>`."""
    value = value.strip()
    if ";" in value:
        raise ValueError(f"OL_SMOKE_SESSION must be the {SESSION_COOKIE} cookie and nothing else")
    if value.startswith(f"{SESSION_COOKIE}="):
        return value
    return f"{SESSION_COOKIE}={value}"


def authenticated_paths(paths: list[str]) -> list[str]:
    """Every probed path except /health and the Google redirects, which a session does not change."""
    return [p for p in paths if p != "/health" and not p.startswith("/auth/google/")]


def expected_authenticated(path: str) -> str:
    if NIL_UUID in path or path in SWITCHED_ROUTES:
        return "200|404"  # the placeholder id names nothing; a switched-off route is not mounted
    return "200"


@dataclass
class AuthProbe:
    path: str
    status: int | None
    body: bytes
    seconds: float
    error: str = ""


def authenticated_failures(probes: list[AuthProbe]) -> list[str]:
    failures = []
    for p in probes:
        expected = expected_authenticated(p.path)
        if p.status is None:
            failures.append(f"{p.path} (as you): no answer ({p.error})")
        elif p.status == 401:
            failures.append(f"{p.path} (as you): HTTP 401 — the session was not accepted; sign in again")
        elif not _matches(p.status, expected):
            why = "broken" if p.status >= 500 else "refused" if p.status == 403 else "unexpected"
            failures.append(f"{p.path} (as you): HTTP {p.status}, expected {expected} — {why}")
        elif p.status == 200 and not _is_json(p.body):
            failures.append(f"{p.path} (as you): HTTP 200 but not JSON")
        elif p.seconds > MAX_AUTHENTICATED_SECONDS:
            failures.append(f"{p.path} (as you): {p.seconds:.1f} s, over the {MAX_AUTHENTICATED_SECONDS:.0f} s budget")
    return failures


def _is_json(body: bytes) -> bool:
    try:
        json.loads(body)
    except ValueError:
        return False
    return True


def authenticated_probe(path: str, fetch: Fetch) -> AuthProbe:
    started = time.monotonic()
    try:
        status, body = fetch(path)
    except (OSError, urllib.error.URLError) as exc:
        return AuthProbe(path, None, b"", time.monotonic() - started, error=str(exc) or type(exc).__name__)
    return AuthProbe(path, status, body, time.monotonic() - started)


# ------------------------------------------------------------------ version


@dataclass
class Verdict:
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def version_verdict(
    sha: str | None, *, on_main: Callable[[str], bool], behind: Callable[[str], int]
) -> Verdict:
    v = Verdict()
    if not sha:
        v.warnings.append("/health reports no version: the deployed API predates the version field")
        return v
    if not on_main(sha):
        v.failures.append(f"production runs {sha[:12]}, which is not a commit on main")
        return v
    n = behind(sha)
    if n:
        v.warnings.append(f"production API is {n} commit(s) behind main on apps/api or supabase/migrations")
    return v


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True)


def _main_ref() -> str:
    return "origin/main" if _git("rev-parse", "--verify", "-q", "origin/main").returncode == 0 else "HEAD"


def git_on_main(sha: str) -> bool:
    return _git("merge-base", "--is-ancestor", sha, _main_ref()).returncode == 0


def git_behind(sha: str) -> int:
    out = _git("rev-list", "--count", f"{sha}..{_main_ref()}", "--", "apps/api", "supabase/migrations")
    return int(out.stdout.strip() or 0)


# ------------------------------------------------------------------ production database (CI only)


def database_checks() -> Verdict:
    """Pending migrations and worker liveness, read-only, as the migrator; CI with secrets only."""
    v = Verdict()
    if os.environ.get("GITHUB_ACTIONS") != "true" or not os.environ.get("OL_PROD_MIGRATOR_PASSWORD"):
        v.warnings.append(
            "NOT CHECKED: applied migrations and worker liveness need the OL_PROD_* secrets in GitHub Actions"
        )
        return v
    sys.path.insert(0, str(REPO / "supabase" / "scripts"))
    sys.path.insert(0, str(REPO / "scripts" / "deploy"))
    from hosted_env import database_env  # noqa: E402
    from verify_workers import worker_conditions  # noqa: E402

    with tempfile.TemporaryDirectory() as cert_dir:
        env = database_env(Path(cert_dir))
        out = subprocess.run(
            ["psql", "-X", "-v", "ON_ERROR_STOP=1", "-Atc",
             "select version from supabase_migrations.schema_migrations"],
            env=env, capture_output=True, text=True, timeout=60,
        )
        if out.returncode != 0:
            v.failures.append("could not read the production migration ledger")
            return v
        v.failures += pending_migration_failures(set(out.stdout.split()), MIGRATIONS)
        v.failures += worker_failures(worker_conditions(env))
    return v


WORKER_CONDITION_FAILURES = {
    "heartbeat": "workers: no worker heartbeat in the last 3 minutes",
    "sweep_scheduled": "workers: the triage sweep was not scheduled in the last 5 minutes",
    "triage_caught_up": "workers: captured mail older than 15 minutes is waiting for triage",
    "capture_recent": "workers: no mail capture cycle in the last 15 minutes",
}


def worker_failures(conditions: dict[str, bool]) -> list[str]:
    return [WORKER_CONDITION_FAILURES[name] for name, ok in conditions.items() if not ok]


def pending_migration_failures(applied: set[str], migrations_dir: Path) -> list[str]:
    repo = sorted(p.name.split("_", 1)[0] for p in migrations_dir.glob("*.sql"))
    pending = [version for version in repo if version not in applied]
    if not pending:
        return []
    return [f"{len(pending)} migration(s) on main not applied in production, first {pending[0]}"]


# ------------------------------------------------------------------ main


def http_fetch(base_url: str, cookie: str | None = None) -> Fetch:
    """GET only, no redirects followed. With `cookie` it returns every body (never printed)."""
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return None

    opener = urllib.request.build_opener(_NoRedirect)

    def fetch(path: str) -> tuple[int, bytes]:
        headers = {"User-Agent": "origenlab-hosted-smoke", "Accept": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        request = urllib.request.Request(f"{base_url}/api{path}", method="GET", headers=headers)
        try:
            with opener.open(request, timeout=15) as response:
                return response.status, response.read() if cookie or path == "/health" else b""
        except urllib.error.HTTPError as exc:
            return exc.code, b""

    return fetch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default=os.environ.get("OL_SMOKE_BASE_URL", DEFAULT_BASE_URL))
    args = parser.parse_args(argv)
    fetch = http_fetch(args.base_url.rstrip("/"))

    try:
        status, body = fetch("/health")  # warm-up; also wakes a sleeping instance
    except (OSError, urllib.error.URLError) as exc:
        print(f"FAIL /health: no answer ({exc})")
        return 1
    version = None
    if status == 200:
        try:
            version = json.loads(body).get("version")
        except ValueError:
            pass

    paths = [p for _, ps in allowlist_entries(ALLOWLIST.read_text(encoding="utf-8")) for p in ps]
    probes = [probe(path, fetch) for path in paths]
    failures = route_failures(probes)
    verdicts = [version_verdict(version, on_main=git_on_main, behind=git_behind), database_checks()]
    failures += [f for v in verdicts for f in v.failures]
    warnings = [w for v in verdicts for w in v.warnings]

    session = os.environ.get("OL_SMOKE_SESSION", "")
    authenticated: list[AuthProbe] = []
    if session:
        as_you = http_fetch(args.base_url.rstrip("/"), cookie=session_cookie_header(session))
        authenticated = [authenticated_probe(p, as_you) for p in authenticated_paths(paths)]
        failures += authenticated_failures(authenticated)
    elif os.environ.get("GITHUB_ACTIONS") != "true":  # on demand by design; the schedule has none
        warnings.append("authenticated reads not checked: set OL_SMOKE_SESSION to your own session cookie")

    slowest = max(probes, key=lambda p: p.seconds)
    print(f"{len(probes)} routes probed at {args.base_url}; slowest {slowest.path} {slowest.seconds:.2f} s")
    print(f"deployed version: {version or 'not reported'}")
    if authenticated:
        slow = max(authenticated, key=lambda p: p.seconds)
        print(f"{len(authenticated)} authenticated reads as you; slowest {slow.path} {slow.seconds:.2f} s")
    for note in switch_notes(probes):
        print(f"note {note}")
    for w in warnings:
        print(f"WARN {w}")
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print(f"::warning title=hosted smoke::{w}")
    for f in failures:
        print(f"FAIL {f}")
    print("hosted smoke: " + ("FAILED" if failures else "passed"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
