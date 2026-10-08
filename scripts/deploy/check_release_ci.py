#!/usr/bin/env python3
"""Gate production release on successful CI for exactly the triggering main commit.

GitHub path filters determine which app workflows apply; every workflow that DID run
must complete successfully. The database and secret scans are mandatory on every push.
"""
from __future__ import annotations
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

APPLICABLE = frozenset({"supabase", "secret-scan", "api", "worker", "dashboard",
                         "dashboard-proxy", "email-pipeline", "web"})
MANDATORY = frozenset({"supabase", "secret-scan"})


class CIRefused(RuntimeError):
    pass


def check_runs(runs: list[dict]) -> tuple[str, str]:
    grouped = {}
    for run in runs:
        name = run.get("name")
        if name not in APPLICABLE or run.get("event") != "push":
            continue
        # Prefer the newest run attempt for each named workflow.
        current = grouped.get(name)
        if current is None or (str(run.get("created_at", "")), int(run.get("run_attempt", 0))) > (
                str(current.get("created_at", "")), int(current.get("run_attempt", 0))):
            grouped[name] = run
    missing = MANDATORY - set(grouped)
    if missing:
        raise CIRefused(f"Required CI did not start: {', '.join(sorted(missing))}")
    pending = sorted(k for k,v in grouped.items() if v.get("status") != "completed")
    failed = sorted(k for k,v in grouped.items() if v.get("status") == "completed" and v.get("conclusion") != "success")
    if failed:
        raise CIRefused(f"CI failed or was cancelled: {', '.join(failed)}")
    if pending:
        return "pending", ", ".join(pending)
    return "success", ", ".join(sorted(grouped))


def list_runs(sha: str, token: str) -> list[dict]:
    params = urllib.parse.urlencode({"head_sha": sha, "event": "push", "per_page": 100})
    req = urllib.request.Request(
        "https://api.github.com/repos/rafaelRojasVi/origenlab/actions/runs?" + params,
        headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            page = json.load(response)
    except (urllib.error.URLError, ValueError) as exc:
        raise CIRefused("Could not read GitHub CI results for pinned SHA") from exc
    if int(page.get("total_count", 0)) > 100:
        raise CIRefused("Too many CI workflow runs to verify in one page")
    return [r for r in page.get("workflow_runs", []) if r.get("head_sha") == sha]


def main() -> None:
    sha = os.environ.get("OL_RELEASE_SHA", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    if not re.fullmatch(r"[0-9a-f]{40}", sha) or not token:
        raise CIRefused("Missing pinned commit and GitHub read token")
    deadline = time.monotonic() + 2400
    while time.monotonic() < deadline:
        status, detail = check_runs(list_runs(sha, token))
        if status == "success":
            print("CI VERIFIED: " + detail)
            return
        print("Waiting for CI: " + detail, flush=True)
        time.sleep(25)
    raise CIRefused("Timed out waiting for all triggered main CI workflows")


if __name__ == "__main__":
    try:
        main()
    except CIRefused as exc:
        print("REFUSED: " + str(exc), file=sys.stderr)
        sys.exit(1)
