#!/usr/bin/env python3
"""Render deploy controller: refuse independent autodeploy, release pinned commit sequentially."""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

OWNER = "tea-d88e6719rddc738bdh1g"
REPOSITORY = "https://github.com/rafaelRojasVi/origenlab"
SERVICES = (
    ("worker", "srv-db2r5c0m7kps73c0nmo0"),
    ("gmail-sync", "crn-db1tk02jnfac73eir6fg"),
    ("api", "srv-d88ffpf7f7vs73b5jcf0"),
    ("dashboard", "srv-d891dn5ckfvc7385i8eg"),
)
FAILED = {"build_failed", "update_failed", "pre_deploy_failed", "canceled", "deactivated"}
IN_PROGRESS = {"created", "queued", "build_in_progress", "update_in_progress", "pre_deploy_in_progress"}


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


def check_current_main(sha: str) -> None:
    """Cron cannot pin its commit: insist that GitHub main still names this SHA."""
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
        raise Refused("Could not recheck main before deploying the unpinnable cron job") from exc
    if ref.get("object", {}).get("sha") != sha:
        raise Refused("Main has moved; cannot deploy a cron job at a reliably pinned SHA")


def latest_deploy(token: str, service_id: str) -> dict | None:
    rows = request("GET", f"/services/{service_id}/deploys?limit=1", token)
    if not isinstance(rows, list):
        raise Refused(f"Unexpected Render deploy history shape for {service_id}")
    return rows[0].get("deploy") if rows else None


def verify_api_http() -> None:
    """Anonymous liveness check only; authenticated DB journey is a separate gate."""
    try:
        with urllib.request.urlopen("https://origenlab.onrender.com/health", timeout=30) as response:
            data = json.load(response)
        if data.get("ok") is not True or data.get("service") != "origenlab-api":
            raise Refused("API HTTP readiness failed; dashboard remains unchanged")
    except (urllib.error.URLError, ValueError, TimeoutError) as exc:
        raise Refused("API HTTP readiness unavailable; dashboard remains unchanged") from exc


def run(mode: str) -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise Refused("Deployment may only run from trusted GitHub main")
    token = os.environ.get("RENDER_API_KEY", "")
    sha = os.environ.get("OL_RELEASE_SHA", "")
    if not token or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise Refused("Missing Render API token or exact reviewed commit SHA")

    # Guard all four first: never perform a partial deploy before discovering one remains autonomous.
    for name, service_id in SERVICES:
        verify_service(request("GET", f"/services/{service_id}", token), service_id)
        latest = latest_deploy(token, service_id)
        if latest and latest.get("status") in IN_PROGRESS:
            raise Refused(f"{name} has a pending deployment; refuse before any mutation")
        print(f"Render gate OK: {name}, independent autodeploy off")

    if mode == "check":
        return
    if mode != "release":
        raise Refused("Usage: render_release.py check|release")
    for name, service_id in SERVICES:
        check_current_main(sha)
        if name == "dashboard":
            verify_api_http()
        verify_service(request("GET", f"/services/{service_id}", token), service_id)
        latest = latest_deploy(token, service_id)
        if latest and latest.get("status") == "live" and latest.get("commit", {}).get("id") == sha:
            print(f"Already on reviewed commit: {name}")
            continue
        if latest and latest.get("status") in IN_PROGRESS:
            raise Refused(f"{name} already has a pending deployment; refuse to race")
        print(f"Deploy {name} at reviewed commit {sha[:12]}", flush=True)
        # Render API explicitly forbids commitId for cron jobs. A cron deployment
        # builds HEAD of the configured branch; refuse if main advanced between
        # the CI preflight and this step. Other services use an exact commitId.
        if name == "gmail-sync":
            check_current_main(sha)
            payload = {"clearCache": "do_not_clear"}
        else:
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
    print("RELEASE COMPLETE: worker, cron, API, dashboard on one reviewed SHA")


if __name__ == "__main__":
    try:
        run(sys.argv[1] if len(sys.argv) == 2 else "")
    except (Refused, OSError, TimeoutError) as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        sys.exit(1)
