#!/usr/bin/env python3
"""Read-only postrelease worker checks: queue heartbeat/sweep + scheduled capture cycle."""
from __future__ import annotations
import os
import json
import urllib.parse
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "supabase/scripts"))
from hosted_env import Refused, database_env
from render_release import OWNER, SERVICES, latest_deploy, request, verify_service, verify_legacy_cron


def healthy_worker(env: dict[str, str], since: str | None = None, *, require_capture: bool = False) -> bool:
    # Do not read message bodies, queue arguments, contacts, or email addresses.
    boundary = datetime.fromisoformat(since.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat() if since else "1970-01-01T00:00:00+00:00"
    capture_guard = f"""
       and exists (select 1 from procrastinate.procrastinate_periodic_defers
                   where task_name = 'capture_mail_cycle' and periodic_id = 'gmail-drive-v1'
                     and defer_timestamp >= extract(epoch from now()) - 900)
       and exists (select 1 from comms.mailbox
                   where authorization_state = 'authorized'
                     and last_synced_at >= greatest(now() - interval '20 minutes', '{boundary}'::timestamptz))
    """ if require_capture else ""
    query = f"""
    set role origenlab_owner;
    select case when
       exists (select 1 from procrastinate.procrastinate_workers
               where last_heartbeat >= greatest(now() - interval '3 minutes', '{boundary}'::timestamptz))
       and exists (select 1 from procrastinate.procrastinate_periodic_defers
                   where task_name = 'sweep_untriaged' and periodic_id = 'sweep'
                     and defer_timestamp >= extract(epoch from now()) - 300)
       and not exists (
         select 1 from comms.message m
           left join evidence.source_record sr
             on sr.dedupe_key = 'gmail_message:' || m.provider_message_id and sr.kind = 'gmail_message'
          where m.parse_status = 'parsed'
            -- Outbound bulk sends intentionally have no evidence source. Capture
            -- does not persist their List-Unsubscribe marker, so source-less
            -- outbound mail cannot be classified safely without reading EML.
            and (sr.id is not null or m.direction = 'inbound')
            and m.eml_storage_path is not null
            and m.internal_date between now() - interval '14 days'
                                    and now() - interval '15 minutes'
            and (
              sr.id is null or not exists (
                select 1 from evidence.assertion a
                 where a.source_record_id = sr.id
                   and a.kind = 'message_triage' and a.value_norm = 'triage:v1')
              or (
                exists (
                  select 1 from evidence.assertion a
                   where a.source_record_id = sr.id
                     and a.kind = 'message_triage' and a.value_norm = 'triage:v1'
                     and a.value->>'class' = 'bounce')
                and not exists (
                  select 1 from evidence.assertion d
                   where d.source_record_id = sr.id
                     and d.kind = 'delivery_failure' and d.value_norm = 'delivery:v1')
              )
              or (
                exists (
                  select 1 from evidence.assertion a
                   where a.source_record_id = sr.id
                     and a.kind = 'message_triage' and a.value_norm = 'triage:v1'
                     and a.value->>'class' = 'quote_request')
                and not exists (
                  select 1 from evidence.assertion r
                   where r.source_record_id = sr.id
                     and r.kind = 'message_triage' and r.value_norm = 'requester:v1')
              )
            )
       )
       {capture_guard}
    then 'healthy' else 'not_ready' end;
    reset role;
    """
    r = subprocess.run(["psql", "-X", "--no-psqlrc", "-At", "-v", "ON_ERROR_STOP=1", "-c", query],
                       env=env, capture_output=True, text=True, check=False, timeout=25)
    if r.returncode:
        raise Refused("Postrelease worker/capture health query failed")
    return "healthy" in r.stdout.splitlines()


def after_deploy(iso: str | None, mark: str | None) -> bool:
    if not iso or not mark:
        return False
    def as_utc(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    return as_utc(iso) >= as_utc(mark)


def swept_after_deploy(token: str, service_id: str, since: str, *, capture: bool = False,
                      revision: str | None = None) -> bool:
    """Successful jobs are deleted by the worker, so their events cannot be proof.

    Read only the worker's aggregate triage_sweep events, never error details,
    queue arguments or email data. The event is emitted after DB reads/defers.
    """
    boundary = datetime.fromisoformat(since.replace("Z", "+00:00")).astimezone(timezone.utc)
    now = datetime.now(timezone.utc)
    from datetime import timedelta
    boundary = max(boundary, now - timedelta(minutes=20 if capture else 5))
    event_name = "capture_cycle" if capture else "triage_sweep"
    params = urllib.parse.urlencode({"ownerId": OWNER, "resource": service_id,
                                    "startTime": boundary.isoformat(), "endTime": now.isoformat(),
                                    "text": f'"event": "{event_name}"', "type": "app", "limit": 100})
    page = request("GET", "/logs?" + params, token)
    if not isinstance(page, dict) or not isinstance(page.get("logs"), list):
        raise Refused("Unexpected aggregate worker log response")
    for row in page["logs"]:
        try:
            labels = {label["name"]:label["value"] for label in row.get("labels", [])}
            event = json.loads(row.get("message", ""))
            if (labels.get("resource") == service_id and after_deploy(row.get("timestamp"), boundary.isoformat())
                and event.get("event") == event_name
                and (revision is None or event.get("revision") == revision)
                and ((capture and event.get("outcome") == "success"
                      and event.get("gmail_mode") in {"history", "resync"}
                      and event.get("drive_mode") in {"file", "paused"})
                     or (not capture and all(isinstance(event.get(key), int) and event[key] >= 0
                                             for key in ("found", "deferred"))))):
                return True
        except (ValueError, TypeError, KeyError):
            continue
    return False


def verify() -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise Refused("Production postflight requires reviewed GitHub main")
    token = os.environ.get("RENDER_API_KEY", "")
    sha = os.environ.get("OL_RELEASE_SHA", "")
    if not token or len(sha) != 40:
        raise Refused("Missing scoped Render credentials or pinned SHA")
    verify_legacy_cron(token)
    services = dict(SERVICES)
    details = {}
    for name, ident in SERVICES:
        service = request("GET", f"/services/{ident}", token)
        verify_service(service, ident)
        deploy = latest_deploy(token, ident)
        if not deploy or deploy.get("status") != "live" or deploy.get("commit", {}).get("id") != sha:
            raise Refused(f"Service {name} is not live at the approved commit")
        details[name] = deploy
    if not details["worker"].get("finishedAt"):
        raise Refused("Worker deployment completion timestamp missing")
    with tempfile.TemporaryDirectory(prefix="ol-postflight-", dir=os.environ.get("RUNNER_TEMP")) as d:
        env = database_env(Path(d))
        worker_deadline = time.monotonic() + 1200
        while True:
            if (healthy_worker(env, details["worker"]["finishedAt"], require_capture=True)
                and swept_after_deploy(token, services["worker"], details["worker"]["finishedAt"], revision=sha)
                and swept_after_deploy(token, services["worker"], details["worker"]["finishedAt"], capture=True, revision=sha)):
                print("WORKER HEALTHY: candidate SHA sweep/capture, fresh cursor, heartbeat, no eligible gaps", flush=True)
                break
            if time.monotonic() >= worker_deadline:
                raise Refused("Worker lacks fresh heartbeat, cursor or candidate-SHA sweep/capture success")
            time.sleep(20)
    verify_legacy_cron(token)
    for name, ident in SERVICES:
        deploy = latest_deploy(token, ident)
        if not deploy or deploy.get("status") != "live" or deploy.get("commit", {}).get("id") != sha:
            raise Refused(f"Service {name} changed during postflight")
    print("POSTFLIGHT PASSED: worker, periodic sweep/capture and all three SHA-pinned services")


if __name__ == "__main__":
    try:
        verify()
    except (Refused, OSError, ValueError, subprocess.TimeoutExpired) as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        sys.exit(1)
