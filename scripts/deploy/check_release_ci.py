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
                         "dashboard-proxy", "email-pipeline", "web", "production-db-migrations"})
MANDATORY = APPLICABLE


class CIRefused(RuntimeError):
    pass


def check_runs(runs: list[dict], sha: str | None = None) -> tuple[str, str]:
    grouped = {}
    for run in runs:
        name = run.get("name")
        if name not in APPLICABLE or run.get("event") != "push":
            continue
        if sha is not None and (run.get("head_sha") != sha or run.get("head_branch") != "main"
                                or run.get("path") != f".github/workflows/{name}.yml"
                                or run.get("head_repository", {}).get("full_name") != "rafaelRojasVi/origenlab"):
            raise CIRefused("CI workflow identity does not match trusted main and pinned SHA")
        # Prefer the newest run attempt for each named workflow.
        current = grouped.get(name)
        if current is None or (str(run.get("created_at", "")), int(run.get("run_attempt", 0))) > (
                str(current.get("created_at", "")), int(current.get("run_attempt", 0))):
            grouped[name] = run
    missing = MANDATORY - set(grouped)
    if missing:
        return "pending", "not yet scheduled: " + ", ".join(sorted(missing))
    pending = sorted(k for k,v in grouped.items() if v.get("status") != "completed")
    failed = sorted(k for k,v in grouped.items() if v.get("status") == "completed" and v.get("conclusion") != "success")
    if failed:
        raise CIRefused(f"CI failed or was cancelled: {', '.join(failed)}")
    if pending:
        return "pending", ", ".join(pending)
    return "success", ", ".join(sorted(grouped))


def validate_jobs(run: dict, token: str) -> None:
    req = urllib.request.Request(
        f"https://api.github.com/repos/rafaelRojasVi/origenlab/actions/runs/{run['id']}/jobs?filter=latest&per_page=100",
        headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            page = json.load(response)
    except (urllib.error.URLError, ValueError) as exc:
        raise CIRefused("Could not verify completed CI jobs") from exc
    jobs = page.get("jobs", [])
    if not jobs or int(page.get("total_count", 0)) > 100:
        raise CIRefused("Missing or truncated CI job list")
    # Production jobs are intentionally skipped on a push. Only its credential-free
    # controller and isolated PostgreSQL tests are required in this workflow.
    if run["name"] == "production-db-migrations":
        jobs = [job for job in jobs if job.get("name") in {
            "Validate proposed release controller without production access", "Isolated PostgreSQL release contract"}]
        if len(jobs) != 2:
            raise CIRefused("Missing release-controller test jobs")
    if any(job.get("status") != "completed" or job.get("conclusion") != "success" for job in jobs):
        raise CIRefused("CI contains failing, cancelled or skipped required jobs")


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
        runs = list_runs(sha, token)
        status, detail = check_runs(runs, sha)
        if status == "success":
            # Reject hidden skips inside otherwise green workflows, including reruns.
            newest = {}
            for run in runs:
                if run.get("name") in APPLICABLE and run.get("event") == "push":
                    key = (str(run.get("created_at", "")), int(run.get("run_attempt", 0)))
                    if run["name"] not in newest or key > newest[run["name"]][0]:
                        newest[run["name"]] = (key, run)
            for _, run in newest.values():
                validate_jobs(run, token)
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
