#!/usr/bin/env python3
"""Read-only postrelease worker checks: queue heartbeat/sweep + scheduled capture cycle."""
from __future__ import annotations
import os
import json
import re
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
import release_plan

WORKER_SRC = Path(__file__).resolve().parents[2] / "apps/worker/src/origenlab_worker"


def worker_constant(module: str, name: str) -> str:
    """A literal the worker writes, read from its source: this check must not import the worker,
    and a hard-coded copy went stale when triage moved from v1 to v3."""
    match = re.search(rf'^{name} = (\d+|"[a-z_]+:v\d+")$', (WORKER_SRC / module).read_text(encoding="utf-8"), re.M)
    if not match:
        raise Refused(f"Cannot read {name} from the worker's {module}")
    return match.group(1).strip('"')


def evidence_value_norms() -> dict[str, str]:
    """The value_norm of the current triage, delivery-failure and requester readings."""
    return {"triage": f"triage:v{worker_constant('triage_rules.py', 'TRIAGE_VERSION')}",
            "delivery": f"delivery:v{worker_constant('bounce.py', 'DELIVERY_FAILURE_VERSION')}",
            "requester": worker_constant("triage.py", "REQUESTER_VALUE_NORM")}


def worker_conditions(env: dict[str, str], since: str | None = None, *,
                      require_capture: bool = False) -> dict[str, bool]:
    """Each worker-health condition by name, so a failure says which one.

    heartbeat: a worker beat in the last 3 minutes; sweep_scheduled: the triage sweep was
    deferred in the last 5 minutes; triage_caught_up: no captured mail between 15 minutes and
    14 days old is left untriaged; capture_recent (with require_capture): a capture cycle in the
    last 15 minutes and an authorized mailbox synced in the last 20.
    """
    # Do not read message bodies, queue arguments, contacts, or email addresses.
    norms = evidence_value_norms()
    boundary = datetime.fromisoformat(since.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat() if since else "1970-01-01T00:00:00+00:00"
    columns = {
        "heartbeat": f"""exists (select 1 from procrastinate.procrastinate_workers
               where last_heartbeat >= greatest(now() - interval '3 minutes', '{boundary}'::timestamptz))""",
        "sweep_scheduled": """exists (select 1 from procrastinate.procrastinate_periodic_defers
                   where task_name = 'sweep_untriaged' and periodic_id = 'sweep'
                     and defer_timestamp >= extract(epoch from now()) - 300)""",
        "triage_caught_up": f"""not exists (
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
                   and a.kind = 'message_triage' and a.value_norm = '{norms["triage"]}')
              or (
                exists (
                  select 1 from evidence.assertion a
                   where a.source_record_id = sr.id
                     and a.kind = 'message_triage' and a.value_norm = '{norms["triage"]}'
                     and a.value->>'class' = 'bounce')
                and not exists (
                  select 1 from evidence.assertion d
                   where d.source_record_id = sr.id
                     and d.kind = 'delivery_failure' and d.value_norm = '{norms["delivery"]}')
              )
              or (
                exists (
                  select 1 from evidence.assertion a
                   where a.source_record_id = sr.id
                     and a.kind = 'message_triage' and a.value_norm = '{norms["triage"]}'
                     and a.value->>'class' = 'quote_request')
                and not exists (
                  select 1 from evidence.assertion r
                   where r.source_record_id = sr.id
                     and r.kind = 'message_triage' and r.value_norm = '{norms["requester"]}')
              )
            )
       )""",
    }
    if require_capture:
        columns["capture_recent"] = f"""(exists (select 1 from procrastinate.procrastinate_periodic_defers
                   where task_name = 'capture_mail_cycle' and periodic_id = 'gmail-drive-v1'
                     and defer_timestamp >= extract(epoch from now()) - 900)
       and exists (select 1 from comms.mailbox
                   where authorization_state = 'authorized'
                     and last_synced_at >= greatest(now() - interval '20 minutes', '{boundary}'::timestamptz)))"""
    select = ",\n".join(f"({sql})::text" for sql in columns.values())
    query = f"set role origenlab_owner;\nselect {select};\nreset role;"
    r = subprocess.run(["psql", "-X", "--no-psqlrc", "-At", "-F", "|", "-v", "ON_ERROR_STOP=1", "-c", query],
                       env=env, capture_output=True, text=True, check=False, timeout=25)
    if r.returncode:
        raise Refused("Postrelease worker/capture health query failed")
    row = next(line for line in r.stdout.splitlines() if "|" in line or line in ("true", "false"))
    values = row.split("|")
    if len(values) != len(columns) or any(v not in ("true", "false") for v in values):
        raise Refused("Postrelease worker/capture health query returned an unexpected row")
    return {name: value == "true" for name, value in zip(columns, values)}


def healthy_worker(env: dict[str, str], since: str | None = None, *, require_capture: bool = False) -> bool:
    return all(worker_conditions(env, since, require_capture=require_capture).values())


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
    try:
        plan = release_plan.parse_plan(os.environ.get("OL_RELEASE_PLAN"), sha)
    except release_plan.PlanRefused as exc:
        raise Refused(str(exc)) from exc
    # Each service at the commit it must run after this release: this SHA where it was deployed,
    # its unchanged live commit where the plan kept it. Without a plan, all three at this SHA.
    expected = release_plan.expected_commits(plan, sha)
    verify_legacy_cron(token)
    services = dict(SERVICES)
    details = {}
    for name, ident in SERVICES:
        service = request("GET", f"/services/{ident}", token)
        verify_service(service, ident)
        deploy = latest_deploy(token, ident)
        if not deploy or deploy.get("status") != "live" or deploy.get("commit", {}).get("id") != expected[name]:
            raise Refused(f"Service {name} is not live at its expected commit")
        details[name] = deploy
    if not details["worker"].get("finishedAt"):
        raise Refused("Worker deployment completion timestamp missing")
    # A kept worker is still checked: fresh heartbeat, sweep and capture at the revision it runs.
    revision = expected["worker"]
    with tempfile.TemporaryDirectory(prefix="ol-postflight-", dir=os.environ.get("RUNNER_TEMP")) as d:
        env = database_env(Path(d))
        worker_deadline = time.monotonic() + 1200
        while True:
            if (healthy_worker(env, details["worker"]["finishedAt"], require_capture=True)
                and swept_after_deploy(token, services["worker"], details["worker"]["finishedAt"], revision=revision)
                and swept_after_deploy(token, services["worker"], details["worker"]["finishedAt"], capture=True, revision=revision)):
                print("WORKER HEALTHY: running-revision sweep/capture, fresh cursor, heartbeat, no eligible gaps", flush=True)
                break
            if time.monotonic() >= worker_deadline:
                raise Refused("Worker lacks fresh heartbeat, cursor or running-revision sweep/capture success")
            time.sleep(20)
    verify_legacy_cron(token)
    for name, ident in SERVICES:
        deploy = latest_deploy(token, ident)
        if not deploy or deploy.get("status") != "live" or deploy.get("commit", {}).get("id") != expected[name]:
            raise Refused(f"Service {name} changed during postflight")
    print("POSTFLIGHT PASSED: worker, periodic sweep/capture and all three services at their expected commits: "
          + "; ".join(release_plan.summary_lines(plan, sha)))

if __name__ == "__main__":
    try:
        verify()
    except (Refused, OSError, ValueError, subprocess.TimeoutExpired) as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        sys.exit(1)
