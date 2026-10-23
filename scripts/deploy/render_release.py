#!/usr/bin/env python3
"""Render deploy controller: refuse independent autodeploy, release pinned commit sequentially.

Modes: check (gates only), plan (gates, then which services the release deploys, from the paths
changed since each one's live commit; scripts/deploy/release_plan.py), release (deploy the
services the preflight plan names, in the fixed order worker -> API -> dashboard).
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

import release_plan

OWNER = "tea-d88e6719rddc738bdh1g"
REPOSITORY = "https://github.com/rafaelRojasVi/origenlab"
SERVICES = (
    ("worker", "srv-db2r5c0m7kps73c0nmo0"),
    ("api", "srv-d88ffpf7f7vs73b5jcf0"),
    ("dashboard", "srv-d891dn5ckfvc7385i8eg"),
)
FAILED = {"build_failed", "update_failed", "pre_deploy_failed", "canceled", "deactivated"}
IN_PROGRESS = {"created", "queued", "build_in_progress", "update_in_progress", "pre_deploy_in_progress"}


LEGACY_CRON = ("gmail-sync", "crn-db1tk02jnfac73eir6fg")
ALL_SERVICES = (*SERVICES, LEGACY_CRON)
WORKER_COMMAND = "uv run --no-sync origenlab-worker mail-worker"
# 2026-10-22: the worker starts from its virtualenv without `uv run` (memory); same consolidated entrypoint.
WORKER_COMMANDS = {WORKER_COMMAND, "uv run --no-sync origenlab-worker triage-worker",
                   ".venv/bin/origenlab-worker mail-worker"}


class Refused(RuntimeError):
    pass


def request(method: str, path: str, token: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.render.com/v1{path}", data=data, method=method,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                 "Content-Type": "application/json"},
    )
    for attempt in range(3 if method == "GET" else 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                result = json.load(response)
                if not isinstance(result, (dict, list)):
                    raise Refused("Unexpected Render API response shape")
                return result
        except urllib.error.HTTPError as exc:
            if method == "GET" and exc.code in {429, 500, 502, 503, 504} and attempt < 2:
                time.sleep(min(30, 5 * (attempt + 1)))
                continue
            raise Refused(f"Render {method} failed with HTTP {exc.code}; reconcile state before retry") from exc
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            if method == "GET" and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            raise Refused(f"Render {method} response unavailable; reconcile state before retry") from exc


def verify_service(service: dict, service_id: str) -> None:
    if service.get("id") != service_id or service.get("ownerId") != OWNER \
            or service.get("repo", "").rstrip("/").removesuffix(".git") != REPOSITORY \
            or service.get("branch") != "main":
        raise Refused(f"Unapproved Render service identity for {service_id}")
    if service.get("autoDeployTrigger") != "off":
        raise Refused(f"{service_id} still has independent auto-deploy enabled. Turn it OFF before releases")


    expected_state = "suspended" if service_id == LEGACY_CRON[1] else "not_suspended"
    if service.get("suspended") != expected_state:
        raise Refused(f"{service_id} must be {expected_state} before release")
    if service_id == SERVICES[0][1]:
        command = service.get("serviceDetails", {}).get("envSpecificDetails", {}).get("startCommand")
        if command not in WORKER_COMMANDS:
            raise Refused("Worker must use the consolidated mail-worker entrypoint before release")


def verify_legacy_cron(token: str) -> None:
    verify_service(request("GET", f"/services/{LEGACY_CRON[1]}", token), LEGACY_CRON[1])


def check_current_main(sha: str) -> None:
    """Refuse stale candidates; every release POST still pins the exact commit."""
    req = urllib.request.Request(
        "https://api.github.com/repos/rafaelRojasVi/origenlab/git/ref/heads/main",
        headers={"Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28",
                 "Authorization": "Bearer " + os.environ.get("GITHUB_TOKEN", "")},
    )
    if not os.environ.get("GITHUB_TOKEN"):
        raise Refused("GitHub read token missing for main commit verification")
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            ref = json.load(response)
    except (urllib.error.URLError, ValueError) as exc:
        raise Refused("Could not recheck the current main release candidate") from exc
    if ref.get("object", {}).get("sha") != sha:
        raise Refused("Main has moved; stop the stale release candidate")


def latest_deploy(token: str, service_id: str) -> dict | None:
    rows = request("GET", f"/services/{service_id}/deploys?limit=1", token)
    if not isinstance(rows, list):
        raise Refused(f"Unexpected Render deploy history shape for {service_id}")
    return rows[0].get("deploy") if rows else None


def live_commit(deploy: dict | None) -> str | None:
    """The exact commit a service runs, only when its latest deploy is live; otherwise unknown."""
    if not deploy or deploy.get("status") != "live":
        return None
    commit = deploy.get("commit", {}).get("id")
    return commit if isinstance(commit, str) and release_plan.SHA.fullmatch(commit) else None


def pending_from_env() -> int | None:
    """The preflight's pending-migration count; anything but a plain integer is unknown."""
    value = os.environ.get("OL_RELEASE_PENDING_MIGRATIONS", "").strip()
    return int(value) if value.isdigit() else None


def write_github(name: str, text: str) -> None:
    path = os.environ.get(name)
    if path:
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(text)


API_HEALTH_URL = "https://dashboard.origenlab.cl/api/health"


def verify_api_http(sha: str, *, attempts: int = 6, wait: float = 20.0,
                    opener=urllib.request.urlopen, sleep=time.sleep) -> None:
    """Anonymous liveness through the public route, at the exact released commit.

    The API refuses hosts outside ORIGENLAB_API_ALLOWED_HOSTS, so its onrender.com address
    answers 403 by design (the first automatic release stopped there, 2026-10-13). The
    dashboard Worker forwards /api/health, the same route the 30-minute smoke reads.
    Authenticated DB journeys remain a separate gate.
    """
    request = urllib.request.Request(API_HEALTH_URL, headers={
        "User-Agent": "origenlab-release", "Accept": "application/json"})
    for attempt in range(attempts):
        try:
            with opener(request, timeout=30) as response:
                data = json.load(response)
            if data.get("ok") is True and data.get("service") == "origenlab-api" \
                    and data.get("version") == sha:
                return
        except (urllib.error.URLError, ValueError, TimeoutError):
            pass
        if attempt < attempts - 1:
            sleep(wait)
    raise Refused("API HTTP readiness at the released commit unavailable; dashboard remains unchanged")


def run(mode: str) -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise Refused("Deployment may only run from trusted GitHub main")
    token = os.environ.get("RENDER_API_KEY", "")
    sha = os.environ.get("OL_RELEASE_SHA", "")
    if not token or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise Refused("Missing Render API token or exact reviewed commit SHA")

    if mode not in {"check", "plan", "release"}:
        raise Refused("Usage: render_release.py check|plan|release")
    plan = None
    if mode == "release":
        try:
            plan = release_plan.parse_plan(os.environ.get("OL_RELEASE_PLAN"), sha)
        except release_plan.PlanRefused as exc:
            raise Refused(str(exc)) from exc
    expected = release_plan.expected_commits(plan, sha)

    # No mutation until the legacy scheduler is suspended and all services gated.
    verify_legacy_cron(token)
    bases: dict[str, str | None] = {}
    for name, service_id in SERVICES:
        verify_service(request("GET", f"/services/{service_id}", token), service_id)
        latest = latest_deploy(token, service_id)
        if latest and latest.get("status") in IN_PROGRESS:
            raise Refused(f"{name} has a pending deployment; refuse before any mutation")
        bases[name] = live_commit(latest)
        if plan is not None and not plan["services"][name]["deploy"] and bases[name] != expected[name]:
            raise Refused(f"{name} changed since the preflight plan; reconcile before release")
        print(f"Render gate OK: {name}, independent autodeploy off")

    if mode == "check":
        return
    if mode == "plan":
        try:
            computed = release_plan.build_plan(sha, bases, pending_from_env())
        except release_plan.PlanRefused as exc:
            raise Refused(str(exc)) from exc
        lines = release_plan.summary_lines(computed, sha)
        for line in lines:
            print("PLAN " + line)
        print(f"PLAN supabase workflow required: {computed['require_supabase']}")
        write_github("GITHUB_OUTPUT", "release_plan=" + json.dumps(computed, separators=(",", ":")) + "\n"
                     + "require_supabase=" + ("true" if computed["require_supabase"] else "false") + "\n")
        return
    for name, service_id in SERVICES:
        check_current_main(sha)
        verify_legacy_cron(token)
        if name == "dashboard":
            # Before the dashboard, always: the API at the commit it must run after this release
            # (this SHA when deployed, its unchanged live commit when kept).
            verify_api_http(expected["api"])
        verify_service(request("GET", f"/services/{service_id}", token), service_id)
        latest = latest_deploy(token, service_id)
        if plan is not None and not plan["services"][name]["deploy"]:
            if live_commit(latest) != expected[name]:
                raise Refused(f"{name} changed since the preflight plan; reconcile before release")
            print(f"Kept: {name} stays on {expected[name][:12]} (nothing it builds from changed)")
            continue
        if latest and latest.get("status") == "live" and latest.get("commit", {}).get("id") == sha:
            print(f"Already on reviewed commit: {name}")
            continue
        if latest and latest.get("status") in IN_PROGRESS:
            raise Refused(f"{name} already has a pending deployment; refuse to race")
        print(f"Deploy {name} at reviewed commit {sha[:12]}", flush=True)
        payload = {"commitId": sha, "clearCache": "do_not_clear"}
        deploy = request("POST", f"/services/{service_id}/deploys", token, payload)
        # Validate immediately so a racing main push cannot go unnoticed.
        if deploy.get("commit", {}).get("id") not in {None, sha}:
            raise Refused(f"Render accepted {name} at a different commit; stop immediately")
        deploy_id = deploy.get("id")
        if not isinstance(deploy_id, str) or not deploy_id.startswith("dep-"):
            raise Refused(f"Render returned no deploy ID for {name}")
        deadline = time.monotonic() + 2100
        while time.monotonic() < deadline:
            current = request("GET", f"/services/{service_id}/deploys/{deploy_id}", token)
            state = current.get("status")
            if current.get("commit", {}).get("id") not in {None, sha}:
                raise Refused(f"Render {name} switched to an unexpected commit")
            if state == "live":
                if current.get("commit", {}).get("id") != sha:
                    raise Refused(f"Render {name} does not prove its deployed commit")
                print(f"LIVE: {name} on {sha[:12]}", flush=True)
                break
            if state in FAILED:
                raise Refused(f"Render deploy of {name} failed: {state}")
            if state not in IN_PROGRESS:
                raise Refused(f"Unknown Render deployment status for {name}: {state}")
            time.sleep(15)
        else:
            raise Refused(f"Render {name} deploy timed out; no further components deployed")
    lines = release_plan.summary_lines(plan, sha)
    write_github("GITHUB_STEP_SUMMARY", "### Release " + sha[:12] + "\n\n" + "".join(f"- {l}\n" for l in lines))
    print("RELEASE COMPLETE on reviewed SHA " + sha[:12] + ": " + "; ".join(lines))

if __name__ == "__main__":
    try:
        run(sys.argv[1] if len(sys.argv) == 2 else "")
    except (Refused, OSError, TimeoutError) as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        sys.exit(1)
