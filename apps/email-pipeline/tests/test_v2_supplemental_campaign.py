"""The supplemental import of a never-sent archived V1 campaign.

**Always run:** the extraction refuses anything that was ever attempted, anything not archived
and any audience in a sent state; the manifest carries no address and hashes stably.

**Database-backed, opt-in** (``ORIGENLAB_V2_TEST_DSN``, a disposable migrated database): a dry
run writes nothing, an apply writes one manifest, one archived campaign and its audience, a
second apply writes nothing, no person/organization/contact point/event appears, and the loaded
campaign is immutable. Every value is fictitious.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any

import pytest

from origenlab_email_pipeline.migration.v2_import.supplemental_campaign import (
    SupplementalRefused,
    apply_supplemental,
    extract,
)

SCHEMA = """
create table outbound_campaign (campaign_id text primary key, name text, sender_email text, sender_name text,
  subject text, target_attempt_count integer, baseline_attempt_count integer, status text, created_at text, updated_at text);
create table outbound_campaign_recipient (id integer primary key autoincrement, campaign_id text, email text,
  email_norm text, state text, created_at text, updated_at text);
create table outbound_send_attempt (id integer primary key autoincrement, campaign_id text, recipient_id integer,
  email_norm text, batch_id text, attempt_seq integer, attempted_at text, mode text, result text, created_at text);
"""


def _sqlite(tmp_path: Path, *, cid: str, status: str = "archived", states=("inactive",) * 3, attempts: int = 0,
            sender: str = "ventas@example.invalid") -> Path:
    path = tmp_path / f"{cid}.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.execute("insert into outbound_campaign values (?, 'Campaña ficticia', ?, 'Ventas', 'Asunto', 1000, 0, ?, "
                 "'2026-09-15T21:07:22Z', '2026-09-15T21:07:22Z')", (cid, sender, status))
    for i, state in enumerate(states):
        conn.execute("insert into outbound_campaign_recipient (campaign_id, email, email_norm, state, created_at, updated_at) "
                     "values (?, ?, ?, ?, 'x', 'x')", (cid, f"Persona{i}@Lab.Example", f"persona{i}@lab.example", state))
    for _ in range(attempts):
        conn.execute("insert into outbound_send_attempt (campaign_id, recipient_id, email_norm, batch_id, attempt_seq, "
                     "attempted_at, mode, result, created_at) values (?, 1, 'persona0@lab.example', 'b', 1, 'x', 'live', "
                     "'accepted', 'x')", (cid,))
    conn.commit()
    conn.close()
    return path


def test_a_never_sent_archived_campaign_is_extracted_without_any_address_in_its_manifest(tmp_path: Path) -> None:
    c = extract(_sqlite(tmp_path, cid="c1"), "c1")
    assert [a for a, _ in c.recipients] == ["persona0@lab.example", "persona1@lab.example", "persona2@lab.example"]
    manifest = json.dumps(c.manifest())
    assert "@lab.example" not in manifest and c.manifest()["recipients_by_v1_state"] == {"inactive": 3}
    assert c.manifest()["send_attempts"] == 0
    (tmp_path / "again").mkdir()
    again = extract(_sqlite(tmp_path / "again", cid="c1"), "c1")
    assert again.manifest_sha256() == c.manifest_sha256()  # same rows, same hash
    assert c.dedupe_key().startswith("migration_manifest:v1-unsent-campaign:c1:")


@pytest.mark.parametrize(("kw", "reason"), [
    ({"status": "active"}, "only an archived campaign"),
    ({"attempts": 1}, "safety bundle"),
    ({"states": ("inactive", "sent")}, "never-sent audience"),
    ({"states": ("bounced",)}, "never-sent audience"),
])
def test_anything_that_was_ever_sent_is_refused(tmp_path: Path, kw: dict[str, Any], reason: str) -> None:
    with pytest.raises(SupplementalRefused, match=reason):
        extract(_sqlite(tmp_path, cid="c2", **kw), "c2")


def test_an_unknown_campaign_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SupplementalRefused, match="no campaign"):
        extract(_sqlite(tmp_path, cid="c3"), "nope")


def test_the_source_is_never_written(tmp_path: Path) -> None:
    path = _sqlite(tmp_path, cid="c4")
    before = path.read_bytes()
    extract(path, "c4")
    assert path.read_bytes() == before


# --------------------------------------------------------------------------- database (opt-in)

_DSN = os.environ.get("ORIGENLAB_V2_TEST_DSN", "")
requires_db = pytest.mark.skipif(not _DSN, reason="set ORIGENLAB_V2_TEST_DSN to a disposable migrated database")


def _counts(dsn: str) -> dict[str, int]:
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        out = {}
        for t in ("evidence.source_record", "outbound.campaign", "outbound.campaign_recipient", "outbound.send_attempt",
                  "crm.person", "crm.organization", "crm.contact_point", "crm.domain_event"):
            cur.execute(f"select count(*) from {t}")  # noqa: S608 - fixed names
            out[t] = cur.fetchone()[0]
        return out


@requires_db
def test_dry_run_then_apply_then_reapply(tmp_path: Path) -> None:
    import psycopg

    from origenlab_email_pipeline.migration.v2_import.target import assert_local_target

    tag = uuid.uuid4().hex[:8]
    sender = f"ventas-{tag}@example.invalid"
    with psycopg.connect(_DSN, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute("insert into comms.mailbox (address_norm) values (%s)", (sender,))
    campaign = extract(_sqlite(tmp_path, cid=f"sup-{tag}", sender=sender, states=("inactive", "inactive", "blocked")), f"sup-{tag}")
    target = assert_local_target(_DSN)
    before = _counts(_DSN)

    dry = apply_supplemental(campaign, target, dry_run=True)
    assert dry.inserted == {"evidence.source_record": 1, "outbound.campaign": 1, "outbound.campaign_recipient": 3}
    assert _counts(_DSN) == before

    applied = apply_supplemental(campaign, target, dry_run=False)
    after = _counts(_DSN)
    assert applied.recipients_by_v2_state == {"excluded": 3}
    assert {k: after[k] - before[k] for k in after} == {
        "evidence.source_record": 1, "outbound.campaign": 1, "outbound.campaign_recipient": 3, "outbound.send_attempt": 0,
        "crm.person": 0, "crm.organization": 0, "crm.contact_point": 0, "crm.domain_event": 0}

    again = apply_supplemental(campaign, target, dry_run=False)
    assert again.already_present is True and _counts(_DSN) == after

    with psycopg.connect(_DSN, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute("select c.status, s.payload_sha256 from outbound.campaign c join evidence.source_record s "
                    "on s.id = c.origin_source_record_id where s.payload->>'v1_campaign_id' = %s", (f"sup-{tag}",))
        status, sha = cur.fetchone()
        assert status == "archived" and sha == campaign.manifest_sha256()
        with pytest.raises(psycopg.errors.RaiseException):
            cur.execute("update outbound.campaign set status = 'draft' where origin_source_record_id = "
                        "(select id from evidence.source_record where payload->>'v1_campaign_id' = %s)", (f"sup-{tag}",))
