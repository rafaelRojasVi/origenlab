"""Importing a sent V1-lane campaign into the CRM (`origenlab_api.v2.v1_sent_campaign_import`).

The extraction half runs against a throwaway SQLite carrying the V1 ledger's shape; the apply
half against a disposable PostgreSQL migrated from `supabase/migrations`. Every address is
fictitious (`.invalid`).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from origenlab_api.v2.v1_sent_campaign_import import (
    SentCampaignRefused,
    extract_sent_campaign,
)

# The V1 ledger's three tables, as `apps/email-pipeline/.../outbound_campaign_schema.py` creates
# them (only the columns the importer reads, plus the NOT NULL ones).
V1_DDL = """
create table outbound_campaign (
  campaign_id text primary key, name text not null, sender_email text not null,
  sender_name text not null, subject text not null, target_attempt_count integer not null,
  status text not null default 'active', created_at text not null, updated_at text not null);
create table outbound_campaign_recipient (
  id integer primary key autoincrement, campaign_id text not null, email text not null,
  email_norm text not null, state text not null, sent_at text, last_gmail_message_id text,
  created_at text not null, updated_at text not null, unique(campaign_id, email_norm));
create table outbound_send_attempt (
  id integer primary key autoincrement, campaign_id text not null, recipient_id integer not null,
  email_norm text not null, batch_id text not null, attempt_seq integer not null,
  attempted_at text not null, mode text not null, result text not null,
  gmail_message_id text, created_at text not null);
"""

CID = "promo-ficticia-2026-10"
T0 = "2026-10-05T12:00:00Z"


def _v1(path: Path, recipients: list[tuple[str, str]], attempts: list[tuple[str, str, str, str, str | None]],
        campaign_id: str = CID) -> Path:
    """recipients: (address, state); attempts: (address, attempted_at, mode, result, gmail_id)."""
    conn = sqlite3.connect(path)
    if not conn.execute("select 1 from sqlite_master where name='outbound_campaign'").fetchone():
        conn.executescript(V1_DDL)
    conn.execute(
        "insert or ignore into outbound_campaign values (?,?,?,?,?,?,?,?,?)",
        (campaign_id, "Promo Ficticia", "contacto@origenlab.cl", "OrigenLab", "Asunto ficticio", 5000, "active", T0, T0),
    )
    ids = {}
    for address, state in recipients:
        cur = conn.execute(
            "insert into outbound_campaign_recipient (campaign_id, email, email_norm, state, created_at, updated_at) "
            "values (?,?,?,?,?,?)", (campaign_id, address, address.lower(), state, T0, T0))
        ids[address.lower()] = cur.lastrowid
    for seq, (address, at, mode, result, gid) in enumerate(attempts, 1):
        conn.execute(
            "insert into outbound_send_attempt (campaign_id, recipient_id, email_norm, batch_id, attempt_seq, "
            "attempted_at, mode, result, gmail_message_id, created_at) values (?,?,?,?,?,?,?,?,?,?)",
            (campaign_id, ids[address.lower()], address.lower(), "b1", seq, at, mode, result, gid, at))
    conn.commit()
    conn.close()
    return path


def _three(tmp_path: Path) -> Path:
    return _v1(
        tmp_path / "v1.sqlite",
        [("uno@instituto.invalid", "sent"), ("dos@instituto.invalid", "sent"),
         ("tres@instituto.invalid", "bounced"), ("cuatro@instituto.invalid", "blocked"),
         ("cinco@instituto.invalid", "inactive")],
        [("uno@instituto.invalid", "2026-10-05T12:31:41Z", "live", "accepted", "g1"),
         ("dos@instituto.invalid", "2026-10-05T12:32:00Z", "live", "accepted", "g2"),
         ("tres@instituto.invalid", "2026-10-06T12:00:00Z", "live", "accepted", "g3"),
         ("cuatro@instituto.invalid", "2026-10-06T12:01:00Z", "live", "failed", None),
         ("uno@instituto.invalid", "2026-10-04T10:00:00Z", "dry_run", "accepted", None)],
    )


def test_extract_keeps_only_accepted_live_attempts(tmp_path: Path) -> None:
    c = extract_sent_campaign(_three(tmp_path), CID)
    assert len(c.attempts) == 3
    states: dict[str, int] = {}
    for r in c.recipients:
        states[r.v2_state] = states.get(r.v2_state, 0) + 1
    assert states == {"sent": 3}  # V1 «bounced» is a later reconcile, not a send fact
    assert c.name == "Promo Ficticia" and c.subject == "Asunto ficticio"
    assert {a.gmail_message_id for a in c.attempts} == {"g1", "g2", "g3"}


def test_extract_skips_recipients_without_accepted_attempt(tmp_path: Path) -> None:
    c = extract_sent_campaign(_three(tmp_path), CID)
    assert {r.address_norm for r in c.recipients} == {
        "uno@instituto.invalid", "dos@instituto.invalid", "tres@instituto.invalid"}


def test_extract_refuses_in_flight(tmp_path: Path) -> None:
    p = _v1(tmp_path / "v1.sqlite", [("uno@instituto.invalid", "reserved")],
            [("uno@instituto.invalid", T0, "live", "in_flight", None)])
    with pytest.raises(SentCampaignRefused, match="in flight"):
        extract_sent_campaign(p, CID)


def test_extract_refuses_campaign_never_sent(tmp_path: Path) -> None:
    p = _v1(tmp_path / "v1.sqlite", [("uno@instituto.invalid", "candidate")], [])
    with pytest.raises(SentCampaignRefused, match="never sent"):
        extract_sent_campaign(p, CID)


def test_extract_refuses_unknown_campaign(tmp_path: Path) -> None:
    with pytest.raises(SentCampaignRefused, match="no campaign"):
        extract_sent_campaign(_three(tmp_path), "otra")


def test_manifest_carries_no_address(tmp_path: Path) -> None:
    c = extract_sent_campaign(_three(tmp_path), CID)
    assert "@" not in json.dumps(c.manifest())
    assert c.dedupe_key() == f"migration_manifest:v1-sent-campaign:{CID}"


def test_sqlite_opened_read_only(tmp_path: Path) -> None:
    p = _three(tmp_path)
    before = (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
    extract_sent_campaign(p, CID)
    assert (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest()) == before


# --------------------------------------------------------------------------- database (opt-in)

import uuid  # noqa: E402

from v2_command_harness import build_disposable_database, needs_db, runtime_dsn  # noqa: E402


@pytest.fixture(scope="module")
def disposable_database():
    yield from build_disposable_database()


@pytest.fixture(scope="module")
def mailbox(disposable_database) -> str:
    import psycopg

    with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute("insert into comms.mailbox (address_norm) values ('contacto@origenlab.cl') returning id::text")
        return cur.fetchone()[0]


def _apply(dsn: str, campaign, *, dry_run: bool = False):
    import psycopg

    from origenlab_api.v2.v1_sent_campaign_import import apply_sent_campaign

    with psycopg.connect(dsn, autocommit=False) as conn:
        return apply_sent_campaign(conn, campaign, dry_run=dry_run)


def _counts(dsn: str, campaign_name: str) -> dict[str, int]:
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(
            "select (select count(*) from outbound.campaign where name = %(n)s), "
            "(select count(*) from outbound.campaign_recipient r join outbound.campaign c on c.id = r.campaign_id where c.name = %(n)s), "
            "(select count(*) from outbound.send_attempt a join outbound.campaign c on c.id = a.campaign_id where c.name = %(n)s)",
            {"n": campaign_name})
        c, r, a = cur.fetchone()
    return {"campaign": c, "recipients": r, "attempts": a}


def _wave(tmp_path: Path, cid: str, n_waves: int) -> Path:
    path = tmp_path / f"{cid}-{n_waves}.sqlite"
    recipients, attempts = [], []
    for w in range(1, n_waves + 1):
        for i in range(3):
            a = f"w{w}-p{i}@instituto.invalid"
            recipients.append((a, "sent"))
            attempts.append((a, f"2026-10-2{w}T13:0{i}:00Z", "live", "accepted", f"g-{cid}-{w}-{i}"))
    return _v1(path, recipients, attempts, campaign_id=cid)


def _named(campaign, name: str):
    from dataclasses import replace

    return replace(campaign, name=name)


@needs_db
def test_apply_creates_archived_imported_campaign(disposable_database, mailbox, tmp_path: Path) -> None:
    import psycopg

    cid = f"promo-{uuid.uuid4().hex[:6]}"
    c = _named(extract_sent_campaign(_wave(tmp_path, cid, 1), cid), f"Promo {cid}")
    result = _apply(disposable_database, c)
    assert result.inserted == {"evidence.source_record": 1, "outbound.campaign": 1,
                               "outbound.campaign_recipient": 3, "outbound.send_attempt": 3}
    with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute("select c.status, c.subject, s.dedupe_key, s.kind from outbound.campaign c "
                    "join evidence.source_record s on s.id = c.origin_source_record_id where c.id = %s",
                    (result.campaign_id,))
        assert cur.fetchone() == ("archived", "Asunto ficticio", c.dedupe_key(), "migration_manifest")
        cur.execute("select distinct purpose, submission_state, delivery_state from outbound.send_attempt "
                    "where campaign_id = %s", (result.campaign_id,))
        assert cur.fetchall() == [("marketing", "accepted", "pending")]
        cur.execute("select count(*) from outbound.send_attempt where campaign_id = %s and provider_message_id like 'g-%%' "
                    "and accepted_at is not null and campaign_recipient_id is not null", (result.campaign_id,))
        assert cur.fetchone()[0] == 3


@needs_db
def test_dry_run_writes_nothing(disposable_database, mailbox, tmp_path: Path) -> None:
    cid = f"promo-{uuid.uuid4().hex[:6]}"
    c = _named(extract_sent_campaign(_wave(tmp_path, cid, 1), cid), f"Promo {cid}")
    result = _apply(disposable_database, c, dry_run=True)
    assert result.dry_run and result.inserted["outbound.send_attempt"] == 3
    assert _counts(disposable_database, c.name) == {"campaign": 0, "recipients": 0, "attempts": 0}


@needs_db
def test_apply_twice_appends_only_new_wave(disposable_database, mailbox, tmp_path: Path) -> None:
    cid = f"promo-{uuid.uuid4().hex[:6]}"
    first = _named(extract_sent_campaign(_wave(tmp_path, cid, 1), cid), f"Promo {cid}")
    both = _named(extract_sent_campaign(_wave(tmp_path, cid, 2), cid), f"Promo {cid}")
    r1 = _apply(disposable_database, first)
    r2 = _apply(disposable_database, both)
    assert r2.campaign_id == r1.campaign_id
    assert r2.inserted["outbound.campaign_recipient"] == 3 and r2.already_present["outbound.campaign_recipient"] == 3
    assert r2.inserted["outbound.send_attempt"] == 3 and r2.already_present["outbound.send_attempt"] == 3
    r3 = _apply(disposable_database, both)
    assert r3.inserted == {"evidence.source_record": 0, "outbound.campaign": 0,
                           "outbound.campaign_recipient": 0, "outbound.send_attempt": 0}
    assert _counts(disposable_database, both.name) == {"campaign": 1, "recipients": 6, "attempts": 6}


@needs_db
def test_apply_refuses_changed_history(disposable_database, mailbox, tmp_path: Path) -> None:
    from dataclasses import replace

    from origenlab_api.v2.v1_sent_campaign_import import SentAttempt, SentRecipient

    cid = f"promo-{uuid.uuid4().hex[:6]}"
    c = _named(extract_sent_campaign(_wave(tmp_path, cid, 1), cid), f"Promo {cid}")
    _apply(disposable_database, c)
    flipped = replace(c, recipients=(SentRecipient(c.recipients[0].address_norm, "bounced"), *c.recipients[1:]))
    with pytest.raises(SentCampaignRefused, match="differs"):
        _apply(disposable_database, flipped)
    moved = replace(c, attempts=(SentAttempt(c.attempts[0].address_norm, "2026-10-01T00:00:00Z",
                                             c.attempts[0].gmail_message_id), *c.attempts[1:]))
    with pytest.raises(SentCampaignRefused, match="differs"):
        _apply(disposable_database, moved)
    assert _counts(disposable_database, c.name) == {"campaign": 1, "recipients": 3, "attempts": 3}


@needs_db
def test_apply_refuses_without_owner_role(disposable_database, mailbox, tmp_path: Path) -> None:
    cid = f"promo-{uuid.uuid4().hex[:6]}"
    c = extract_sent_campaign(_wave(tmp_path, cid, 1), cid)
    with pytest.raises(SentCampaignRefused, match="origenlab_owner"):
        _apply(runtime_dsn(disposable_database), c)


@needs_db
def test_v1_later_bounce_does_not_block_the_next_wave(disposable_database, mailbox, tmp_path: Path) -> None:
    import sqlite3 as _sq

    cid = f"promo-{uuid.uuid4().hex[:6]}"
    _apply(disposable_database, _named(extract_sent_campaign(_wave(tmp_path, cid, 1), cid), f"Promo {cid}"))
    later = _wave(tmp_path, cid, 2)
    with _sq.connect(later) as conn:  # V1's reconcile marks a wave-1 recipient bounced
        conn.execute("update outbound_campaign_recipient set state = 'bounced' where email_norm = 'w1-p0@instituto.invalid'")
    r = _apply(disposable_database, _named(extract_sent_campaign(later, cid), f"Promo {cid}"))
    assert r.inserted["outbound.campaign_recipient"] == 3


@needs_db
def test_apply_refuses_campaign_already_imported_by_another_importer(disposable_database, mailbox, tmp_path: Path) -> None:
    import psycopg

    cid = f"promo-{uuid.uuid4().hex[:6]}"
    c = _named(extract_sent_campaign(_wave(tmp_path, cid, 1), cid), f"Promo {cid}")
    with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute("insert into outbound.campaign (name, status, mailbox_id, max_sends) values (%s, 'archived', %s, 10)",
                    (c.name, mailbox))
    with pytest.raises(SentCampaignRefused, match="already in the target"):
        _apply(disposable_database, c)
    assert _counts(disposable_database, c.name)["recipients"] == 0


@needs_db
def test_imported_campaign_lists_with_its_v1_key_for_the_runtime_role(disposable_database, mailbox, tmp_path: Path) -> None:
    import psycopg

    from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository

    cid = f"promo-{uuid.uuid4().hex[:6]}"
    c = _named(extract_sent_campaign(_wave(tmp_path, cid, 1), cid), f"Promo {cid}")
    r = _apply(disposable_database, c)
    body = CrmWorkspaceRepository(psycopg.connect, runtime_dsn(disposable_database)).marketing()
    row = next(x for x in body["campaigns"] if x["campaign_id"] == r.campaign_id)
    assert row["v1_campaign_key"] == cid and row["origin"] == "imported_v1"
    assert row["totals"]["sent"] == 3


def test_cli_reports_a_database_error_without_its_detail(tmp_path: Path, monkeypatch, capsys) -> None:
    import importlib.util

    import psycopg

    spec = importlib.util.spec_from_file_location("v1_import_cli", Path(__file__).parents[1] / "scripts" / "v1_sent_campaign_import.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)  # type: ignore[union-attr]

    class _Conn:
        autocommit = False

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def boom(*a, **k):
        raise psycopg.errors.UniqueViolation("duplicate key: (address_norm)=(uno@instituto.invalid)")

    monkeypatch.setattr(cli, "_connect", lambda args: _Conn())
    monkeypatch.setattr(cli, "apply_sent_campaign", boom)
    code = cli.main(["--sqlite", str(_three(tmp_path)), "--campaign-id", CID])
    out = capsys.readouterr().out
    assert code == 2
    assert "UniqueViolation" in out and "@" not in out
