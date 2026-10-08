#!/usr/bin/env python3
"""Read-only postrelease worker checks: queue heartbeat/sweep + next successful cron run."""
from __future__ import annotations
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "supabase/scripts"))
from hosted_env import Refused, database_env
from render_release import SERVICES, latest_deploy, request, verify_service


def healthy_worker(env: dict[str, str]) -> bool:
    # Do not read message bodies, queue arguments, contacts, or email addresses.
    query = """
    set role origenlab_owner;
    select case when
       exists (select 1 from procrastinate.procrastinate_workers
               where last_heartbeat >= now() - interval '3 minutes')
       and exists (select 1 from procrastinate.procrastinate_periodic_defers
                   where defer_timestamp >= extract(epoch from now()) - 300)
    then 'healthy' else 'not_ready' end;
    reset role;
    """
    r = subprocess.run(["psql", "-X", "--no-psqlrc", "-At", "-v", "ON_ERROR_STOP=1", "-c", query],
                       env=env, capture_output=True, text=True, check=False, timeout=25)
    if r.returncode:
        raise Refused("Postrelease worker health query failed")
    return "healthy" in r.stdout.splitlines()


def after_deploy(iso: str | None, mark: str | None) -> bool:
    if not iso or not mark:
        return False
    def as_utc(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    return as_utc(iso) >= as_utc(mark)


def verify() -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise Refused("Production postflight requires reviewed GitHub main")
    token = os.environ.get("RENDER_API_KEY", "")
    sha = os.environ.get("OL_RELEASE_SHA", "")
    if not token or len(sha) != 40:
        raise Refused("Missing scoped Render credentials or pinned SHA")
    services = dict(SERVICES)
    details = {}
    for name, ident in SERVICES:
        service = request("GET", f"/services/{ident}", token)
        verify_service(service, ident)
        deploy = latest_deploy(token, ident)
        if not deploy or deploy.get("status") != "live" or deploy.get("commit", {}).get("id") != sha:
            raise Refused(f"Service {name} is not live at the approved commit")
        details[name] = deploy
    with tempfile.TemporaryDirectory(prefix="ol-postflight-", dir=os.environ.get("RUNNER_TEMP")) as d:
        env = database_env(Path(d))
        worker_deadline = time.monotonic() + 360
        while True:
            if healthy_worker(env):
                print("WORKER HEALTHY: fresh Procrastinate heartbeat and periodic sweep", flush=True)
                break
            if time.monotonic() >= worker_deadline:
                raise Refused("Mail-triage worker has no recent heartbeat or periodic sweep")
            time.sleep(20)
    cron_since = details["gmail-sync"].get("finishedAt")
    cron_deadline = time.monotonic() + 1200
    while True:
        cron = request("GET", f"/services/{services['gmail-sync']}", token)
        verify_service(cron, services["gmail-sync"])
        completed = cron.get("serviceDetails", {}).get("lastSuccessfulRunAt")
        if after_deploy(completed, cron_since):
            print("CRON HEALTHY: successful Gmail-sync run after the currently deployed revision", flush=True)
            break
        if time.monotonic() >= cron_deadline:
            raise Refused("Gmail-sync cron has no verified successful run after deployment")
        time.sleep(30)
    print("POSTFLIGHT PASSED: worker, periodic sweep, cron and all four SHA-pinned services")


if __name__ == "__main__":
    try:
        verify()
    except (Refused, OSError, ValueError, subprocess.TimeoutExpired) as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        sys.exit(1)
