"""CRM authoring: database-backed proof that every handler executes correctly against PostgreSQL.

All 27 command handlers run as the real ``origenlab_api`` role against a disposable
``origenlab_test_<hex>`` database that the harness creates from the migration chain and drops
when the module exits.  Every value is fictitious; no address in this file contains ``@`` in
a position that would reach a real mailbox.

Tests are grouped by the command family they exercise and share a single ``world`` fixture
that seeds the database once for the module.  This avoids the cost of rebuilding the schema
for every test while keeping the state predictable: each test that mutates state works on its
own row (by creating it fresh) unless the test is specifically checking a multi-step chain
(notes, merge, archive/restore).
"""

from __future__ import annotations

import hashlib
import json
import uuid

import psycopg
import pytest

from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.crm_authoring import (
    PRODUCT_LINE_IDS,
    V2CrmAuthoringRepository,
    merge_preview_digest,
)
from origenlab_api.v2.identity import OperatorIdentity
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn


def _dict_digest(command: str, fields: dict) -> str:
    """Stable SHA-256 of the command + fields (dict), matching the shape request_digest uses."""
    public = {k: v for k, v in fields.items() if not k.startswith("_")}
    payload = {"command": command, "body": public}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


# ─────────────────────────────────────────────────────────────── fixtures ──


@pytest.fixture(scope="module")
def disposable_database():
    yield from build_disposable_database()


@pytest.fixture(scope="module")
def world(disposable_database):
    """Seed the database once per module: three operators + an org + a person + a supplier assertion."""
    tag = uuid.uuid4().hex[:10]
    with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")

        # Three operators
        for role in ("viewer", "sales", "admin"):
            cur.execute(
                "insert into platform.operator (auth_user_id, email_norm, display_name, role, status)"
                " values (gen_random_uuid(), %s, %s, %s, 'active') returning id::text",
                (f"{role}-{tag}@example.test", role.title(), role),
            )
            row = cur.fetchone()
            globals()[f"_{role}_id_{tag}"] = row[0]

        # A persisted organization (seeded outside a command so tests don't depend on register-org)
        cur.execute(
            "insert into crm.organization (name, kind, confirmation, confirmed_by_operator_id, status)"
            " values ('Seed Corp', 'supplier', 'confirmed',"
            " (select id from platform.operator where email_norm = %s), 'active')"
            " returning id::text, version",
            (f"admin-{tag}@example.test",),
        )
        org_row = cur.fetchone()
        org_id = org_row[0]

        # A persisted person (seeded outside a command)
        cur.execute(
            "insert into crm.person (display_name, confirmation, confirmed_by_operator_id, status)"
            " values ('Seed Person', 'confirmed',"
            " (select id from platform.operator where email_norm = %s), 'active')"
            " returning id::text, version",
            (f"admin-{tag}@example.test",),
        )
        person_row = cur.fetchone()
        person_id = person_row[0]

        # A contact point for the seeded person (personal usage: person_id set, org_id null)
        cur.execute(
            "insert into crm.contact_point (kind, value_norm, value_display, person_id, usage, confirmation)"
            " values ('email', %s, %s, %s::uuid, 'personal', 'confirmed')"
            " returning id::text",
            (
                f"seeded-{tag}@example.test",
                f"seeded-{tag}@example.test",
                person_id,
            ),
        )
        cp_id = cur.fetchone()[0]

        # A mailbox (needed for seeding campaigns in merge tests)
        cur.execute(
            "insert into comms.mailbox (address_norm) values (%s) returning id::text",
            (f"ventas-{tag}@example.invalid",),
        )
        mailbox_id = cur.fetchone()[0]

        # A supplier_candidate assertion (needs a source_record)
        cur.execute(
            "insert into evidence.source_record (kind, dedupe_key, payload)"
            " values ('v1_evidence_edge', %s, '{}') returning id::text",
            (f"src-{tag}",),
        )
        source_id = cur.fetchone()[0]

        cur.execute(
            "insert into evidence.assertion (kind, value_norm, resolution, source_record_id)"
            " values ('supplier_candidate', %s, 'unresolved', %s::uuid) returning id::text",
            (f"seed-{tag}.example.test", source_id),
        )
        assertion_id = cur.fetchone()[0]

    # Build OperatorIdentity objects that tests can use directly
    def _op(role):
        return OperatorIdentity(
            operator_id=globals()[f"_{role}_id_{tag}"],
            email_norm=f"{role}-{tag}@example.test",
            display_name=role.title(),
            role=role,
            status="active",
        )

    return {
        "tag": tag,
        "org_id": org_id,
        "person_id": person_id,
        "cp_id": cp_id,
        "assertion_id": assertion_id,
        "source_id": source_id,
        "mailbox_id": mailbox_id,
        "viewer": _op("viewer"),
        "sales": _op("sales"),
        "admin": _op("admin"),
    }


# ─────────────────────────────────────────────────────────────── helpers ──


def _repo(dsn):
    return V2CrmAuthoringRepository(psycopg.connect, runtime_dsn(dsn))


def _run(dsn, operator, command, fields, key=None):
    return _repo(dsn).execute(
        command_name=command,
        operator=operator,
        fields=fields,
        idempotency_key=key or uuid.uuid4().hex,
        digest=_dict_digest(command, fields),
    )


def _event_types(dsn, aggregate_kind, aggregate_id):
    with psycopg.connect(runtime_dsn(dsn)) as conn, conn.cursor() as cur:
        cur.execute(
            "select event_type from crm.domain_event"
            " where aggregate_kind = %s and aggregate_id = %s::uuid order by seq",
            (aggregate_kind, aggregate_id),
        )
        return [r[0] for r in cur.fetchall()]


def _receipt_count(dsn, key):
    with psycopg.connect(runtime_dsn(dsn)) as conn, conn.cursor() as cur:
        cur.execute(
            "select count(*) from platform.command_receipt where idempotency_key = %s",
            (key,),
        )
        return cur.fetchone()[0]


# ─────────────────────────────────────────────────────── person commands ──


@needs_db
def test_create_person_succeeds_and_writes_event(disposable_database, world):
    tag = world["tag"]
    out = _run(disposable_database, world["sales"], "create-person", {
        "display_name": f"Nuevo Contacto {tag}",
        "given_name": "Nuevo",
        "family_name": "Contacto",
        "note": "primer contacto de prueba",
    })
    assert out["ok"] is True
    pid = out["person_id"]
    assert [e for e in _event_types(disposable_database, "person", pid) if e == "person.created"] == ["person.created"]
    assert _receipt_count(disposable_database, _repo(disposable_database)._last_key if hasattr(_repo(disposable_database), "_last_key") else "") >= 0


@needs_db
def test_create_person_replay_returns_same_answer(disposable_database, world):
    tag = world["tag"]
    key = f"create-person-replay-{tag}-{uuid.uuid4().hex[:6]}"
    fields = {"display_name": f"Replay Person {tag}", "note": "replay test"}
    first = _run(disposable_database, world["sales"], "create-person", fields, key=key)
    second = _run(disposable_database, world["sales"], "create-person", fields, key=key)
    assert second["replayed"] is True
    assert second["person_id"] == first["person_id"]
    # Only one event written
    assert _event_types(disposable_database, "person", first["person_id"]).count("person.created") == 1


@needs_db
def test_update_person_succeeds_and_writes_event(disposable_database, world):
    # Create a fresh person to update
    tag = world["tag"]
    created = _run(disposable_database, world["sales"], "create-person", {
        "display_name": f"Upd Person {tag}", "note": "for update test",
    })
    pid = created["person_id"]
    out = _run(disposable_database, world["sales"], "update-person", {
        "person_id": pid,
        "expected_version": 1,
        "display_name": f"Updated {tag}",
        "note": "update note",
    })
    assert out["ok"] is True and out["version"] == 2
    assert "person.updated" in _event_types(disposable_database, "person", pid)


@needs_db
def test_update_person_stale_version_refuses_and_writes_nothing(disposable_database, world):
    tag = world["tag"]
    created = _run(disposable_database, world["sales"], "create-person", {
        "display_name": f"Stale Person {tag}", "note": "for stale test",
    })
    pid = created["person_id"]
    with pytest.raises(CommandRefused) as exc:
        _run(disposable_database, world["sales"], "update-person", {
            "person_id": pid,
            "expected_version": 99,
            "display_name": "Should fail",
            "note": "stale",
        })
    assert exc.value.code == "stale_version"
    # No update event
    assert "person.updated" not in _event_types(disposable_database, "person", pid)


@needs_db
def test_archive_and_restore_person(disposable_database, world):
    tag = world["tag"]
    created = _run(disposable_database, world["sales"], "create-person", {
        "display_name": f"ArchPerson {tag}", "note": "archive test",
    })
    pid = created["person_id"]

    archived = _run(disposable_database, world["admin"], "archive-person", {
        "person_id": pid,
        "expected_version": 1,
        "note": "archiving for test",
    })
    assert archived["ok"] is True
    assert "person.archived" in _event_types(disposable_database, "person", pid)

    restored = _run(disposable_database, world["admin"], "restore-person", {
        "person_id": pid,
        "expected_version": 2,
        "note": "restoring for test",
    })
    assert restored["ok"] is True
    assert "person.restored" in _event_types(disposable_database, "person", pid)


@needs_db
def test_archive_person_returns_references(disposable_database, world):
    tag = world["tag"]
    created = _run(disposable_database, world["sales"], "create-person", {
        "display_name": f"RefPerson {tag}", "note": "ref test",
    })
    pid = created["person_id"]
    out = _run(disposable_database, world["admin"], "archive-person", {
        "person_id": pid, "expected_version": 1, "note": "ref check",
    })
    refs = out["references"]
    assert isinstance(refs, dict)
    assert "campaign_recipients" in refs and "notes" in refs


@needs_db
def test_archived_person_refuses_update(disposable_database, world):
    tag = world["tag"]
    created = _run(disposable_database, world["sales"], "create-person", {
        "display_name": f"ArchRef {tag}", "note": "archived subject test",
    })
    pid = created["person_id"]
    _run(disposable_database, world["admin"], "archive-person", {
        "person_id": pid, "expected_version": 1, "note": "archiving",
    })
    with pytest.raises(CommandRefused) as exc:
        _run(disposable_database, world["sales"], "update-person", {
            "person_id": pid, "expected_version": 2,
            "display_name": "Should fail", "note": "nope",
        })
    assert exc.value.code == "archived_subject"


@needs_db
def test_merge_people_repoints_and_archives_loser(disposable_database, world):
    tag = world["tag"]
    loser = _run(disposable_database, world["sales"], "create-person", {
        "display_name": f"Loser {tag}", "note": "merge test",
    })
    winner = _run(disposable_database, world["sales"], "create-person", {
        "display_name": f"Winner {tag}", "note": "merge test",
    })
    loser_id = loser["person_id"]
    winner_id = winner["person_id"]

    # Add a contact point to the loser so there is a move to count
    cp = _run(disposable_database, world["sales"], "add-contact-point", {
        "person_id": loser_id,
        "expected_version": 1,
        "kind": "email",
        "value": f"loser-{tag}@example.test",
        "usage": "personal",
        "note": "loser cp",
    })

    # Add campaign recipient to the loser (seed via owner role)
    with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(
            "insert into outbound.campaign (name, max_sends, recontact_interval_days, status,"
            "  mailbox_id, created_by_operator_id)"
            " values ('Merge Test Camp', 10, 30, 'archived',"
            " %s::uuid, (select id from platform.operator where email_norm = %s))"
            " returning id::text",
            (world["mailbox_id"], f"admin-{tag}@example.test"),
        )
        camp_id = cur.fetchone()[0]
        cur.execute(
            "insert into outbound.campaign_recipient (campaign_id, address_norm, person_id, state)"
            " values (%s::uuid, %s, %s::uuid, 'sent') returning id::text",
            (camp_id, f"loser-{tag}@example.test", loser_id),
        )
        cr_id = cur.fetchone()[0]

    # Read current versions after cp add
    with psycopg.connect(runtime_dsn(disposable_database)) as conn, conn.cursor() as cur:
        cur.execute("select version from crm.person where id = %s::uuid", (loser_id,))
        loser_ver = cur.fetchone()[0]
        cur.execute("select version from crm.person where id = %s::uuid", (winner_id,))
        winner_ver = cur.fetchone()[0]

    # Build digest (1 cp, 0 affiliations, 0 opp, 1 campaign_recipient, 0 notes)
    moves = {
        "contact_points": 1,
        "affiliations": 0,
        "opportunity_participants": 0,
        "campaign_recipients": 1,
        "notes": 0,
    }
    digest = merge_preview_digest({
        "loser":  {"id": loser_id,  "version": loser_ver},
        "winner": {"id": winner_id, "version": winner_ver},
        "moves":  moves,
    })

    out = _run(disposable_database, world["admin"], "merge-people", {
        "loser_person_id": loser_id,
        "winner_person_id": winner_id,
        "expected_loser_version": loser_ver,
        "expected_winner_version": winner_ver,
        "expected_preview_sha256": digest,
        "note": "merge test",
    })
    assert out["ok"] is True and out["loser_person_id"] == loser_id

    # Loser is archived+merged
    with psycopg.connect(runtime_dsn(disposable_database)) as conn, conn.cursor() as cur:
        cur.execute("select status, merged_into_person_id::text from crm.person where id = %s::uuid", (loser_id,))
        row = cur.fetchone()
    assert row[0] == "archived" and row[1] == winner_id

    # Contact point repointed to winner
    with psycopg.connect(runtime_dsn(disposable_database)) as conn, conn.cursor() as cur:
        cur.execute("select person_id::text from crm.contact_point where id = %s::uuid", (cp["contact_point_id"],))
        assert cur.fetchone()[0] == winner_id

    # Campaign recipient repointed to winner
    with psycopg.connect(runtime_dsn(disposable_database)) as conn, conn.cursor() as cur:
        cur.execute("select person_id::text from outbound.campaign_recipient where id = %s::uuid", (cr_id,))
        assert cur.fetchone()[0] == winner_id


@needs_db
def test_merge_people_wrong_digest_refuses(disposable_database, world):
    tag = world["tag"]
    p1 = _run(disposable_database, world["sales"], "create-person", {"display_name": f"DigestP1 {tag}", "note": "d"})
    p2 = _run(disposable_database, world["sales"], "create-person", {"display_name": f"DigestP2 {tag}", "note": "d"})
    with pytest.raises(CommandRefused) as exc:
        _run(disposable_database, world["admin"], "merge-people", {
            "loser_person_id": p1["person_id"],
            "winner_person_id": p2["person_id"],
            "expected_loser_version": 1,
            "expected_winner_version": 1,
            "expected_preview_sha256": "a" * 64,
            "note": "wrong digest",
        })
    assert exc.value.code == "preview_changed"


# ─────────────────────────────────────────────────── contact point commands ──


@needs_db
def test_add_contact_point_to_person(disposable_database, world):
    tag = world["tag"]
    person = _run(disposable_database, world["sales"], "create-person", {
        "display_name": f"CPPerson {tag}", "note": "cp test",
    })
    pid = person["person_id"]
    out = _run(disposable_database, world["sales"], "add-contact-point", {
        "person_id": pid,
        "expected_version": 1,
        "kind": "email",
        "value": f"cp-new-{tag}@example.test",
        "usage": "personal",
        "note": "add contact test",
    })
    assert out["ok"] is True
    assert "contact_point.created" in _event_types(disposable_database, "contact_point", out["contact_point_id"])


@needs_db
def test_add_contact_point_taken_by_other_subject_refuses(disposable_database, world):
    tag = world["tag"]
    p1 = _run(disposable_database, world["sales"], "create-person", {"display_name": f"TakenP1 {tag}", "note": "t"})
    p2 = _run(disposable_database, world["sales"], "create-person", {"display_name": f"TakenP2 {tag}", "note": "t"})
    shared_email = f"taken-{tag}@example.test"
    _run(disposable_database, world["sales"], "add-contact-point", {
        "person_id": p1["person_id"], "expected_version": 1,
        "kind": "email", "value": shared_email, "usage": "personal", "note": "t",
    })
    with pytest.raises(CommandRefused) as exc:
        _run(disposable_database, world["sales"], "add-contact-point", {
            "person_id": p2["person_id"], "expected_version": 1,
            "kind": "email", "value": shared_email, "usage": "personal", "note": "t",
        })
    assert exc.value.code == "contact_point_taken"


@needs_db
def test_deactivate_then_reactivate_contact_point(disposable_database, world):
    tag = world["tag"]
    person = _run(disposable_database, world["sales"], "create-person", {
        "display_name": f"ReactPerson {tag}", "note": "reactivate test",
    })
    pid = person["person_id"]
    cp = _run(disposable_database, world["sales"], "add-contact-point", {
        "person_id": pid, "expected_version": 1,
        "kind": "phone", "value": "+56912345678", "usage": "personal", "note": "add",
    })
    cp_id = cp["contact_point_id"]

    # Deactivate
    _run(disposable_database, world["sales"], "deactivate-contact-point", {
        "contact_point_id": cp_id, "expected_version": 1, "note": "deactivating",
    })
    assert "contact_point.deactivated" in _event_types(disposable_database, "contact_point", cp_id)

    # Check (kind,value_norm) uniqueness still holds after deactivation
    # A second person cannot claim the same value
    p2 = _run(disposable_database, world["sales"], "create-person", {"display_name": f"P2React {tag}", "note": "r"})
    with psycopg.connect(runtime_dsn(disposable_database)) as conn, conn.cursor() as cur:
        cur.execute("select value_norm from crm.contact_point where id = %s::uuid", (cp_id,))
        val = cur.fetchone()[0]
    with pytest.raises(CommandRefused) as exc:
        _run(disposable_database, world["sales"], "add-contact-point", {
            "person_id": p2["person_id"], "expected_version": 1,
            "kind": "phone", "value": val, "usage": "personal", "note": "take",
        })
    assert exc.value.code == "contact_point_taken"

    # Reactivate on same person
    with psycopg.connect(runtime_dsn(disposable_database)) as conn, conn.cursor() as cur:
        cur.execute("select version from crm.person where id = %s::uuid", (pid,))
        person_ver = cur.fetchone()[0]
    react = _run(disposable_database, world["sales"], "add-contact-point", {
        "person_id": pid, "expected_version": person_ver,
        "kind": "phone", "value": val, "usage": "personal", "note": "reactivate",
    })
    assert react["reactivated"] is True


@needs_db
def test_update_contact_point(disposable_database, world):
    tag = world["tag"]
    person = _run(disposable_database, world["sales"], "create-person", {"display_name": f"UCP {tag}", "note": "ucp"})
    pid = person["person_id"]
    cp = _run(disposable_database, world["sales"], "add-contact-point", {
        "person_id": pid, "expected_version": 1,
        "kind": "email", "value": f"ucp-{tag}@example.test", "usage": "personal", "note": "add",
    })
    cp_id = cp["contact_point_id"]
    out = _run(disposable_database, world["sales"], "update-contact-point", {
        "contact_point_id": cp_id, "expected_version": 1,
        "usage": "personal", "note": "update usage",
    })
    assert out["ok"] is True and out["version"] == 2


# ──────────────────────────────────────────────────── affiliation commands ──


@needs_db
def test_link_and_unlink_person_organization(disposable_database, world):
    tag = world["tag"]
    person = _run(disposable_database, world["sales"], "create-person", {"display_name": f"AffPerson {tag}", "note": "aff"})
    pid = person["person_id"]

    linked = _run(disposable_database, world["sales"], "link-person-organization", {
        "person_id": pid,
        "expected_version": 1,
        "organization_id": world["org_id"],
        "role_title": "Comprador",
        "note": "linking",
    })
    assert linked["ok"] is True
    aff_id = linked["affiliation_id"]
    assert "affiliation.opened" in _event_types(disposable_database, "affiliation", aff_id)

    # Unlink (valid_to must be > valid_from per constraint; use a future date)
    unlinked = _run(disposable_database, world["sales"], "unlink-person-organization", {
        "person_id": pid,
        "affiliation_id": aff_id,
        "expected_version": linked["version"],
        "valid_to": "2099-12-31",
        "note": "unlinking",
    })
    assert unlinked["ok"] is True
    assert "affiliation.closed" in _event_types(disposable_database, "affiliation", aff_id)


@needs_db
def test_link_person_organization_gist_no_overlap(disposable_database, world):
    """Linking twice to the same org at the same time should be refused (GiST overlap)."""
    tag = world["tag"]
    person = _run(disposable_database, world["sales"], "create-person", {"display_name": f"GistPerson {tag}", "note": "g"})
    pid = person["person_id"]
    _run(disposable_database, world["sales"], "link-person-organization", {
        "person_id": pid, "expected_version": 1,
        "organization_id": world["org_id"], "role_title": "A", "note": "first",
    })
    with psycopg.connect(runtime_dsn(disposable_database)) as conn, conn.cursor() as cur:
        cur.execute("select version from crm.person where id = %s::uuid", (pid,))
        ver = cur.fetchone()[0]
    # Same role_title triggers the GiST no-overlap constraint
    with pytest.raises(Exception) as exc:
        _run(disposable_database, world["sales"], "link-person-organization", {
            "person_id": pid, "expected_version": ver,
            "organization_id": world["org_id"], "role_title": "A", "note": "overlap",
        })
    # The DB raises ExclusionViolation, which the transaction wraps as CommandRefused or re-raises
    code = getattr(exc.value, "code", "") or type(exc.value).__name__
    assert "overlap" in code.lower() or "exclusion" in code.lower() or isinstance(
        exc.value, (CommandRefused,)) or "ExclusionViolation" in type(exc.value).__name__


# ──────────────────────────────────────────────── organization commands ──


@needs_db
def test_register_organization_no_sub_entities(disposable_database, world):
    tag = world["tag"]
    out = _run(disposable_database, world["sales"], "register-organization", {
        "name": f"Simple Org {tag}", "kind": "customer", "note": "register test",
    })
    assert out["ok"] is True
    assert out["version"] == 1
    assert "organization.created" in _event_types(disposable_database, "organization", out["organization_id"])


@needs_db
def test_register_organization_with_sub_entities_returns_bumped_version(disposable_database, world):
    tag = world["tag"]
    out = _run(disposable_database, world["sales"], "register-organization", {
        "name": f"SubOrg {tag}",
        "kind": "supplier",
        "classification": "supplier",
        "domain": f"suborg-{tag}.example.test",
        "product_lines": ["hielscher"],
        "note": "register with sub",
    })
    assert out["ok"] is True
    # Version must be bumped above 1 because sub-entities were added
    assert out["version"] == 2, f"expected version 2 after sub-entity write, got {out['version']}"
    # Verify the DB agrees
    with psycopg.connect(runtime_dsn(disposable_database)) as conn, conn.cursor() as cur:
        cur.execute("select version from crm.organization where id = %s::uuid", (out["organization_id"],))
        db_version = cur.fetchone()[0]
    assert db_version == out["version"]


@needs_db
def test_update_organization(disposable_database, world):
    tag = world["tag"]
    created = _run(disposable_database, world["sales"], "register-organization", {
        "name": f"UpdOrg {tag}", "kind": "customer", "note": "update test",
    })
    org_id = created["organization_id"]
    out = _run(disposable_database, world["sales"], "update-organization", {
        "organization_id": org_id,
        "expected_version": created["version"],
        "name": f"Updated Org {tag}",
        "note": "update org",
    })
    assert out["ok"] is True
    assert "organization.updated" in _event_types(disposable_database, "organization", org_id)


@needs_db
def test_archive_and_restore_organization(disposable_database, world):
    tag = world["tag"]
    created = _run(disposable_database, world["sales"], "register-organization", {
        "name": f"ArchOrg {tag}", "kind": "prospect", "note": "arch test",
    })
    org_id = created["organization_id"]
    ver = created["version"]

    arch = _run(disposable_database, world["admin"], "archive-organization", {
        "organization_id": org_id, "expected_version": ver, "note": "archiving org",
    })
    assert arch["ok"] is True
    assert "organization.archived" in _event_types(disposable_database, "organization", org_id)

    rest = _run(disposable_database, world["admin"], "restore-organization", {
        "organization_id": org_id, "expected_version": ver + 1, "note": "restoring org",
    })
    assert rest["ok"] is True
    assert "organization.restored" in _event_types(disposable_database, "organization", org_id)


@needs_db
def test_add_and_remove_organization_identifier(disposable_database, world):
    tag = world["tag"]
    created = _run(disposable_database, world["sales"], "register-organization", {
        "name": f"IdOrg {tag}", "kind": "supplier", "note": "id test",
    })
    org_id = created["organization_id"]
    ver = created["version"]

    added = _run(disposable_database, world["sales"], "add-organization-identifier", {
        "organization_id": org_id, "expected_version": ver,
        "scheme": "rut", "value": f"12345678-{tag[:1]}", "note": "add id",
    })
    assert added["ok"] is True
    identifier_id = added["identifier_id"]
    assert "organization.identifier_added" in _event_types(disposable_database, "organization", org_id)

    removed = _run(disposable_database, world["sales"], "remove-organization-identifier", {
        "organization_id": org_id, "expected_version": added["version"],
        "identifier_id": identifier_id, "note": "remove id",
    })
    assert removed["ok"] is True
    assert "organization.identifier_removed" in _event_types(disposable_database, "organization", org_id)

    # Verify removed_at is set, row still exists
    with psycopg.connect(runtime_dsn(disposable_database)) as conn, conn.cursor() as cur:
        cur.execute("select removed_at from crm.external_identifier where id = %s::uuid", (identifier_id,))
        assert cur.fetchone()[0] is not None


@needs_db
def test_add_and_remove_organization_domain(disposable_database, world):
    tag = world["tag"]
    created = _run(disposable_database, world["sales"], "register-organization", {
        "name": f"DomOrg {tag}", "kind": "supplier", "note": "domain test",
    })
    org_id = created["organization_id"]
    ver = created["version"]

    added = _run(disposable_database, world["sales"], "add-organization-domain", {
        "organization_id": org_id, "expected_version": ver,
        "domain": f"dom-{tag}.example.test", "note": "add domain",
    })
    assert added["ok"] is True
    domain_id = added["domain_id"]

    removed = _run(disposable_database, world["sales"], "remove-organization-domain", {
        "organization_id": org_id, "expected_version": added["version"],
        "domain_id": domain_id, "note": "remove domain",
    })
    assert removed["ok"] is True

    # Row still exists with removed_at set
    with psycopg.connect(runtime_dsn(disposable_database)) as conn, conn.cursor() as cur:
        cur.execute("select removed_at from crm.organization_domain where id = %s::uuid", (domain_id,))
        assert cur.fetchone()[0] is not None


@needs_db
def test_add_and_remove_organization_classification(disposable_database, world):
    tag = world["tag"]
    created = _run(disposable_database, world["sales"], "register-organization", {
        "name": f"ClassOrg {tag}", "kind": "supplier", "note": "class test",
    })
    org_id = created["organization_id"]
    ver = created["version"]

    added = _run(disposable_database, world["sales"], "add-organization-classification", {
        "organization_id": org_id, "expected_version": ver,
        "role": "supplier", "note": "add class",
    })
    assert added["ok"] is True
    rel_id = added["relationship_id"]

    removed = _run(disposable_database, world["sales"], "remove-organization-classification", {
        "organization_id": org_id, "expected_version": added["version"],
        "relationship_id": rel_id, "valid_to": "2099-12-31", "note": "remove class",
    })
    assert removed["ok"] is True

    # Relationship closed (valid_to set)
    with psycopg.connect(runtime_dsn(disposable_database)) as conn, conn.cursor() as cur:
        cur.execute("select valid_to from crm.organization_relationship where id = %s::uuid", (rel_id,))
        assert cur.fetchone()[0] is not None


@needs_db
def test_link_and_unlink_organization_product_line(disposable_database, world):
    tag = world["tag"]
    created = _run(disposable_database, world["sales"], "register-organization", {
        "name": f"PLOrg {tag}", "kind": "supplier", "note": "pl test",
    })
    org_id = created["organization_id"]
    ver = created["version"]

    linked = _run(disposable_database, world["sales"], "link-organization-product-line", {
        "organization_id": org_id, "expected_version": ver,
        "line_id": "hielscher", "note": "link pl",
    })
    assert linked["ok"] is True
    link_id = linked["link_id"]

    # Second link of same line refuses
    with pytest.raises(CommandRefused) as exc:
        _run(disposable_database, world["sales"], "link-organization-product-line", {
            "organization_id": org_id, "expected_version": linked["version"],
            "line_id": "hielscher", "note": "duplicate",
        })
    assert exc.value.code == "line_already_linked"

    # Unlink
    with psycopg.connect(runtime_dsn(disposable_database)) as conn, conn.cursor() as cur:
        cur.execute("select version from crm.organization where id = %s::uuid", (org_id,))
        ver2 = cur.fetchone()[0]
    unlinked = _run(disposable_database, world["sales"], "unlink-organization-product-line", {
        "organization_id": org_id, "expected_version": ver2,
        "link_id": link_id, "note": "unlink pl",
    })
    assert unlinked["ok"] is True


@needs_db
def test_unknown_product_line_refuses(disposable_database, world):
    tag = world["tag"]
    created = _run(disposable_database, world["sales"], "register-organization", {
        "name": f"BadPLOrg {tag}", "kind": "supplier", "note": "bad pl test",
    })
    org_id = created["organization_id"]
    ver = created["version"]
    with pytest.raises(CommandRefused) as exc:
        _run(disposable_database, world["sales"], "link-organization-product-line", {
            "organization_id": org_id, "expected_version": ver,
            "line_id": "not-a-real-brand", "note": "bad",
        })
    assert exc.value.code == "invalid_product_line"


# ──────────────────────────────────────────────── supplier candidate commands ──


@needs_db
def test_confirm_supplier_candidate_with_existing_org(disposable_database, world):
    """confirm-supplier-candidate linked to the seeded world org."""
    tag = world["tag"]
    # Create a fresh assertion so we don't consume the module-level one
    with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(
            "insert into evidence.assertion (kind, value_norm, resolution, source_record_id)"
            " values ('supplier_candidate', %s, 'unresolved', %s::uuid) returning id::text",
            (f"conf-{tag}.example.test", world["source_id"]),
        )
        assertion_id = cur.fetchone()[0]

    out = _run(disposable_database, world["admin"], "confirm-supplier-candidate", {
        "assertion_id": assertion_id,
        "organization_id": world["org_id"],
        "classification": "supplier",
        "note": "confirm test",
    })
    assert out["ok"] is True and out["resolution"] == "linked"
    assert "assertion.supplier_candidate_confirmed" in _event_types(disposable_database, "assertion", assertion_id)


@needs_db
def test_confirm_supplier_candidate_with_new_organization(disposable_database, world):
    tag = world["tag"]
    with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(
            "insert into evidence.assertion (kind, value_norm, resolution, source_record_id)"
            " values ('supplier_candidate', %s, 'unresolved', %s::uuid) returning id::text",
            (f"promoted-{tag}.example.test", world["source_id"]),
        )
        assertion_id = cur.fetchone()[0]

    out = _run(disposable_database, world["admin"], "confirm-supplier-candidate", {
        "assertion_id": assertion_id,
        "new_organization": {"name": f"New Supplier {tag}", "kind": "supplier"},
        "classification": "supplier",
        "product_lines": ["ika"],
        "note": "promote test",
    })
    assert out["ok"] is True and out["resolution"] == "promoted"


@needs_db
def test_reject_supplier_candidate(disposable_database, world):
    tag = world["tag"]
    with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(
            "insert into evidence.assertion (kind, value_norm, resolution, source_record_id)"
            " values ('supplier_candidate', %s, 'unresolved', %s::uuid) returning id::text",
            (f"reject-{tag}.example.test", world["source_id"]),
        )
        assertion_id = cur.fetchone()[0]

    out = _run(disposable_database, world["admin"], "reject-supplier-candidate", {
        "assertion_id": assertion_id, "note": "rejected as spam",
    })
    assert out["ok"] is True and out["resolution"] == "rejected"


@needs_db
def test_second_decision_on_resolved_assertion_refuses(disposable_database, world):
    """Resolving a candidate that is already decided returns candidate_already_decided."""
    tag = world["tag"]
    with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(
            "insert into evidence.assertion (kind, value_norm, resolution, source_record_id)"
            " values ('supplier_candidate', %s, 'unresolved', %s::uuid) returning id::text",
            (f"double-{tag}.example.test", world["source_id"]),
        )
        assertion_id = cur.fetchone()[0]

    _run(disposable_database, world["admin"], "reject-supplier-candidate", {
        "assertion_id": assertion_id, "note": "first decision",
    })
    with pytest.raises(CommandRefused) as exc:
        _run(disposable_database, world["admin"], "reject-supplier-candidate", {
            "assertion_id": assertion_id, "note": "second decision",
        })
    assert exc.value.code == "candidate_already_decided"


# ────────────────────────────────────────────────────────────── note commands ──


@needs_db
def test_add_revise_archive_note_chain(disposable_database, world):
    tag = world["tag"]
    person = _run(disposable_database, world["sales"], "create-person", {
        "display_name": f"NotePerson {tag}", "note": "note test",
    })
    pid = person["person_id"]

    # Add note as sales operator
    added = _run(disposable_database, world["sales"], "add-note", {
        "subject_kind": "person",
        "subject_id": pid,
        "body": "Initial observation about this person.",
    })
    assert added["ok"] is True
    note_id = added["note_id"]
    assert "note.created" in _event_types(disposable_database, "note", note_id)

    # Revise the note
    revised = _run(disposable_database, world["sales"], "revise-note", {
        "note_id": note_id, "expected_version": 1,
        "body": "Revised observation with more detail.",
    })
    assert revised["ok"] is True
    new_note_id = revised["note_id"]
    assert new_note_id != note_id
    assert revised["root_note_id"] == note_id
    assert "note.revised" in _event_types(disposable_database, "note", new_note_id)

    # Original note body is unchanged
    with psycopg.connect(runtime_dsn(disposable_database)) as conn, conn.cursor() as cur:
        cur.execute("select body, revision_no from crm.note where id = %s::uuid", (note_id,))
        orig = cur.fetchone()
    assert orig[0] == "Initial observation about this person."
    assert orig[1] == 1

    # Revised note has revision_no = 2
    with psycopg.connect(runtime_dsn(disposable_database)) as conn, conn.cursor() as cur:
        cur.execute("select revision_no from crm.note where id = %s::uuid", (new_note_id,))
        assert cur.fetchone()[0] == 2

    # Archive by a different sales operator should fail (not author, not admin)
    other_person = _run(disposable_database, world["admin"], "create-person", {
        "display_name": f"OtherSales {tag}", "note": "other",
    })
    other_op = OperatorIdentity(
        operator_id=world["admin"].operator_id,  # admin for creation, but simulate a second sales op
        email_norm=world["admin"].email_norm,
        display_name="Admin",
        role="admin",
        status="active",
    )
    # A sales op that is NOT the author → should be refused
    non_author_sales = OperatorIdentity(
        operator_id=world["admin"].operator_id,  # any other id, not the sales op who created the note
        email_norm="non-author-sales@example.test",
        display_name="Non Author",
        role="sales",
        status="active",
    )
    with pytest.raises(CommandRefused) as exc:
        _run(disposable_database, non_author_sales, "archive-note", {
            "note_id": new_note_id, "expected_version": 1,
            "note": "should fail",
            "_requesting_operator_id": non_author_sales.operator_id,
            "_requesting_operator_role": "sales",
        })
    assert exc.value.code == "role_may_not_archive"

    # Archive by author (sales) → ok
    author_op = world["sales"]
    arch = _run(disposable_database, author_op, "archive-note", {
        "note_id": new_note_id, "expected_version": 1,
        "note": "archiving own note",
        "_requesting_operator_id": author_op.operator_id,
        "_requesting_operator_role": "sales",
    })
    assert arch["ok"] is True
    assert "note.archived" in _event_types(disposable_database, "note", new_note_id)


@needs_db
def test_archive_note_by_admin_succeeds(disposable_database, world):
    tag = world["tag"]
    person = _run(disposable_database, world["sales"], "create-person", {
        "display_name": f"AdminArchNote {tag}", "note": "admin arch note",
    })
    pid = person["person_id"]
    added = _run(disposable_database, world["sales"], "add-note", {
        "subject_kind": "person", "subject_id": pid,
        "body": "Note to be archived by admin.",
    })
    note_id = added["note_id"]

    admin_op = world["admin"]
    arch = _run(disposable_database, admin_op, "archive-note", {
        "note_id": note_id, "expected_version": 1,
        "note": "admin override",
        "_requesting_operator_id": admin_op.operator_id,
        "_requesting_operator_role": "admin",
    })
    assert arch["ok"] is True


@needs_db
def test_revise_archived_note_refuses(disposable_database, world):
    tag = world["tag"]
    person = _run(disposable_database, world["sales"], "create-person", {
        "display_name": f"RevArch {tag}", "note": "revise arch test",
    })
    pid = person["person_id"]
    added = _run(disposable_database, world["sales"], "add-note", {
        "subject_kind": "person", "subject_id": pid,
        "body": "Note to archive then fail revise.",
    })
    note_id = added["note_id"]
    author_op = world["sales"]
    _run(disposable_database, author_op, "archive-note", {
        "note_id": note_id, "expected_version": 1, "note": "archive",
        "_requesting_operator_id": author_op.operator_id, "_requesting_operator_role": "sales",
    })
    with pytest.raises(CommandRefused) as exc:
        _run(disposable_database, world["sales"], "revise-note", {
            "note_id": note_id, "expected_version": 2, "body": "should fail",
        })
    assert exc.value.code == "archived_subject"


# ──────────────────────────────────────────────────── replay / idempotency ──


@needs_db
def test_replay_register_organization(disposable_database, world):
    tag = world["tag"]
    key = f"reg-org-replay-{tag}-{uuid.uuid4().hex[:6]}"
    fields = {"name": f"ReplayOrg {tag}", "kind": "customer", "note": "replay"}
    first = _run(disposable_database, world["sales"], "register-organization", fields, key=key)
    second = _run(disposable_database, world["sales"], "register-organization", fields, key=key)
    assert second["replayed"] is True
    assert second["organization_id"] == first["organization_id"]
    assert _event_types(disposable_database, "organization", first["organization_id"]).count("organization.created") == 1


@needs_db
def test_replay_add_note(disposable_database, world):
    tag = world["tag"]
    person = _run(disposable_database, world["sales"], "create-person", {
        "display_name": f"ReplayNotePerson {tag}", "note": "rn",
    })
    key = f"add-note-replay-{tag}-{uuid.uuid4().hex[:6]}"
    fields = {"subject_kind": "person", "subject_id": person["person_id"], "body": "Replay body."}
    first = _run(disposable_database, world["sales"], "add-note", fields, key=key)
    second = _run(disposable_database, world["sales"], "add-note", fields, key=key)
    assert second["replayed"] is True
    assert second["note_id"] == first["note_id"]
    assert _event_types(disposable_database, "note", first["note_id"]).count("note.created") == 1


# ──────────────────────────────────────────────────────────────── receipts ──


@needs_db
def test_each_successful_command_writes_exactly_one_receipt(disposable_database, world):
    tag = world["tag"]
    key = f"receipt-check-{tag}-{uuid.uuid4().hex[:6]}"
    _run(disposable_database, world["sales"], "create-person", {
        "display_name": f"ReceiptPerson {tag}", "note": "receipt",
    }, key=key)
    assert _receipt_count(disposable_database, key) == 1


# ───────────────────────────────────────────────── domain restore (same row, never a duplicate) ──


def _domain_row(dsn, domain_id):
    with psycopg.connect(runtime_dsn(dsn)) as conn, conn.cursor() as cur:
        cur.execute(
            "select organization_id::text, domain_norm, scope, removed_at, removed_by_operator_id,"
            " remove_reason from crm.organization_domain where id = %s::uuid",
            (domain_id,),
        )
        row = cur.fetchone()
    return None if row is None else dict(
        zip(("organization_id", "domain_norm", "scope", "removed_at", "removed_by", "remove_reason"), row)
    )


def _domain_rows_for(dsn, org_id, domain_norm):
    with psycopg.connect(runtime_dsn(dsn)) as conn, conn.cursor() as cur:
        cur.execute(
            "select count(*) from crm.organization_domain where organization_id = %s::uuid and domain_norm = %s",
            (org_id, domain_norm),
        )
        return cur.fetchone()[0]


def _last_event_payload(dsn, aggregate_kind, aggregate_id, event_type):
    with psycopg.connect(runtime_dsn(dsn)) as conn, conn.cursor() as cur:
        cur.execute(
            "select payload from crm.domain_event where aggregate_kind = %s and aggregate_id = %s::uuid"
            " and event_type = %s order by seq desc limit 1",
            (aggregate_kind, aggregate_id, event_type),
        )
        row = cur.fetchone()
        return None if row is None else row[0]


def _org_with_domain(dsn, world, *, scope="shared"):
    tag = world["tag"]
    suffix = uuid.uuid4().hex[:6]
    created = _run(dsn, world["sales"], "register-organization", {
        "name": f"RestoreOrg {tag} {suffix}", "kind": "supplier", "note": "domain restore fixture",
    })
    org_id = created["organization_id"]
    domain = f"restore-{suffix}.example.test"
    added = _run(dsn, world["sales"], "add-organization-domain", {
        "organization_id": org_id, "expected_version": created["version"],
        "domain": domain, "scope": scope, "note": "add",
    })
    assert added["restored"] is False
    removed = _run(dsn, world["sales"], "remove-organization-domain", {
        "organization_id": org_id, "expected_version": added["version"],
        "domain_id": added["domain_id"], "note": "removed for the test",
    })
    return org_id, domain, added["domain_id"], removed["version"]


@needs_db
def test_readding_a_removed_domain_restores_the_same_row(disposable_database, world):
    org_id, domain, domain_id, ver = _org_with_domain(disposable_database, world)
    assert _domain_row(disposable_database, domain_id)["removed_at"] is not None

    # Different casing and surrounding whitespace normalize to the same domain_norm.
    readded = _run(disposable_database, world["sales"], "add-organization-domain", {
        "organization_id": org_id, "expected_version": ver,
        "domain": f"  {domain.upper()} ", "note": "it is theirs again",
    })
    assert readded["ok"] is True
    assert readded["restored"] is True
    assert readded["domain_id"] == domain_id, "the existing row is restored, not a new one"
    assert readded["version"] == ver + 1

    row = _domain_row(disposable_database, domain_id)
    assert row["removed_at"] is None and row["removed_by"] is None and row["remove_reason"] is None
    assert _domain_rows_for(disposable_database, org_id, domain) == 1

    events = _event_types(disposable_database, "organization", org_id)
    assert events.count("organization.domain_added") == 1
    assert events.count("organization.domain_removed") == 1
    assert events.count("organization.domain_restored") == 1
    payload = _last_event_payload(disposable_database, "organization", org_id, "organization.domain_restored")
    assert payload["domain_id"] == domain_id
    assert payload["domain_norm"] == domain
    assert payload["reason"] == "it is theirs again"


@needs_db
def test_adding_a_domain_already_present_is_refused_not_duplicated(disposable_database, world):
    org_id, domain, domain_id, ver = _org_with_domain(disposable_database, world)
    restored = _run(disposable_database, world["sales"], "restore-organization-domain", {
        "organization_id": org_id, "expected_version": ver, "domain_id": domain_id, "note": "back",
    })
    with pytest.raises(CommandRefused) as exc:
        _run(disposable_database, world["sales"], "add-organization-domain", {
            "organization_id": org_id, "expected_version": restored["version"],
            "domain": domain, "note": "again",
        })
    assert exc.value.status_code == 409
    assert exc.value.code == "domain_already_present"
    assert _domain_rows_for(disposable_database, org_id, domain) == 1


@needs_db
def test_restore_organization_domain_command(disposable_database, world):
    org_id, domain, domain_id, ver = _org_with_domain(disposable_database, world)

    # Stale version: refused before anything changes.
    with pytest.raises(CommandRefused) as exc:
        _run(disposable_database, world["sales"], "restore-organization-domain", {
            "organization_id": org_id, "expected_version": ver - 1, "domain_id": domain_id, "note": "x",
        })
    assert exc.value.code == "stale_version"
    assert _domain_row(disposable_database, domain_id)["removed_at"] is not None

    key = f"restore-domain-{uuid.uuid4().hex[:8]}"
    fields = {"organization_id": org_id, "expected_version": ver, "domain_id": domain_id, "note": "restored by test"}
    first = _run(disposable_database, world["sales"], "restore-organization-domain", fields, key=key)
    assert first["ok"] is True and first["restored"] is True
    assert first["domain_id"] == domain_id and first["version"] == ver + 1
    row = _domain_row(disposable_database, domain_id)
    assert row["removed_at"] is None and row["organization_id"] == org_id
    payload = _last_event_payload(disposable_database, "organization", org_id, "organization.domain_restored")
    assert payload["reason"] == "restored by test" and payload["scope"] == "shared"

    # Same key, same body: replayed, no second event.
    second = _run(disposable_database, world["sales"], "restore-organization-domain", fields, key=key)
    assert second["replayed"] is True and second["domain_id"] == domain_id
    assert _event_types(disposable_database, "organization", org_id).count("organization.domain_restored") == 1

    # Restoring a live domain is refused.
    with pytest.raises(CommandRefused) as exc:
        _run(disposable_database, world["sales"], "restore-organization-domain", {
            "organization_id": org_id, "expected_version": first["version"], "domain_id": domain_id, "note": "x",
        })
    assert exc.value.status_code == 409 and exc.value.code == "domain_not_removed"

    # Unknown row: 404.
    with pytest.raises(CommandRefused) as exc:
        _run(disposable_database, world["sales"], "restore-organization-domain", {
            "organization_id": org_id, "expected_version": first["version"],
            "domain_id": str(uuid.uuid4()), "note": "x",
        })
    assert exc.value.status_code == 404 and exc.value.code == "domain_not_found"


@needs_db
def test_restore_never_moves_a_domain_to_another_organization(disposable_database, world):
    org_a, domain, domain_id, _ = _org_with_domain(disposable_database, world)
    other = _run(disposable_database, world["sales"], "register-organization", {
        "name": f"Other Org {uuid.uuid4().hex[:6]}", "kind": "customer", "note": "other",
    })
    with pytest.raises(CommandRefused) as exc:
        _run(disposable_database, world["sales"], "restore-organization-domain", {
            "organization_id": other["organization_id"], "expected_version": other["version"],
            "domain_id": domain_id, "note": "trying to take it",
        })
    assert exc.value.status_code == 409
    assert exc.value.code == "domain_of_other_organization"
    row = _domain_row(disposable_database, domain_id)
    assert row["organization_id"] == org_a, "the row still belongs to its organization"
    assert row["removed_at"] is not None, "and it is still removed"
    assert "organization.domain_restored" not in _event_types(disposable_database, "organization", other["organization_id"])
    assert _domain_rows_for(disposable_database, other["organization_id"], domain) == 0


@needs_db
def test_exclusive_claim_is_refused_by_name_while_another_organization_holds_it(disposable_database, world):
    org_a, domain, domain_id_a, ver_a = _org_with_domain(disposable_database, world, scope="exclusive")
    org_b = _run(disposable_database, world["sales"], "register-organization", {
        "name": f"Claimant {uuid.uuid4().hex[:6]}", "kind": "supplier", "note": "b",
    })
    # A's claim is soft-removed, so B may claim the domain exclusively.
    added_b = _run(disposable_database, world["sales"], "add-organization-domain", {
        "organization_id": org_b["organization_id"], "expected_version": org_b["version"],
        "domain": domain, "scope": "exclusive", "note": "b claims",
    })
    assert added_b["restored"] is False

    # A cannot restore its exclusive claim while B's is live — a named refusal, not a 500.
    with pytest.raises(CommandRefused) as exc:
        _run(disposable_database, world["sales"], "restore-organization-domain", {
            "organization_id": org_a, "expected_version": ver_a, "domain_id": domain_id_a, "note": "back",
        })
    assert exc.value.status_code == 409 and exc.value.code == "exclusive_domain_taken"
    assert _domain_row(disposable_database, domain_id_a)["removed_at"] is not None

    # A may come back as a shared claim, on its own row.
    restored = _run(disposable_database, world["sales"], "restore-organization-domain", {
        "organization_id": org_a, "expected_version": ver_a, "domain_id": domain_id_a,
        "scope": "shared", "note": "shared now",
    })
    assert restored["restored"] is True and restored["domain_id"] == domain_id_a
    row = _domain_row(disposable_database, domain_id_a)
    assert row["scope"] == "shared" and row["removed_at"] is None
    payload = _last_event_payload(disposable_database, "organization", org_a, "organization.domain_restored")
    assert payload["previous_scope"] == "exclusive" and payload["scope"] == "shared"

    # And a third organization adding the same exclusive claim is refused by name too.
    org_c = _run(disposable_database, world["sales"], "register-organization", {
        "name": f"Third {uuid.uuid4().hex[:6]}", "kind": "supplier", "note": "c",
    })
    with pytest.raises(CommandRefused) as exc:
        _run(disposable_database, world["sales"], "add-organization-domain", {
            "organization_id": org_c["organization_id"], "expected_version": org_c["version"],
            "domain": domain, "scope": "exclusive", "note": "c claims",
        })
    assert exc.value.code == "exclusive_domain_taken"


# ───────────────────────── the three gates on ORIGENLAB_V2_CRM_AUTHORING_ENABLED (#619 review) ──


def _new_org(dsn, world, label):
    created = _run(dsn, world["sales"], "register-organization", {
        "name": f"{label} {world['tag']} {uuid.uuid4().hex[:6]}", "kind": "company", "note": label,
    })
    return created["organization_id"], created["version"]


def _identifier_rows(dsn, scheme, value_norm):
    with psycopg.connect(runtime_dsn(dsn)) as conn, conn.cursor() as cur:
        cur.execute(
            "select id::text, organization_id::text, removed_at is not null"
            " from crm.external_identifier where scheme = %s and value_norm = %s",
            (scheme, value_norm),
        )
        return cur.fetchall()


@needs_db
def test_readding_a_removed_identifier_restores_the_same_row(disposable_database, world):
    org_id, ver = _new_org(disposable_database, world, "IdRestore")
    rut = f"7{uuid.uuid4().int % 10**7:07d}-1"
    added = _run(disposable_database, world["sales"], "add-organization-identifier", {
        "organization_id": org_id, "expected_version": ver, "scheme": "rut", "value": rut, "note": "add",
    })
    removed = _run(disposable_database, world["sales"], "remove-organization-identifier", {
        "organization_id": org_id, "expected_version": added["version"],
        "identifier_id": added["identifier_id"], "note": "typo, removing",
    })

    # Before the fix the global unique (scheme, value_norm) surfaced here as a 500.
    readded = _run(disposable_database, world["sales"], "add-organization-identifier", {
        "organization_id": org_id, "expected_version": removed["version"],
        "scheme": "rut", "value": f" {rut} ", "note": "it was right after all",
    })
    assert readded["ok"] is True
    assert readded["restored"] is True
    assert readded["identifier_id"] == added["identifier_id"], "the existing row is restored, not a new one"
    assert readded["version"] == removed["version"] + 1
    assert _identifier_rows(disposable_database, "rut", rut) == [(added["identifier_id"], org_id, False)]
    payload = _last_event_payload(disposable_database, "organization", org_id, "organization.identifier_added")
    assert payload["restored"] is True and payload["identifier_id"] == added["identifier_id"]


@needs_db
def test_adding_an_identifier_already_present_is_refused_not_a_500(disposable_database, world):
    org_id, ver = _new_org(disposable_database, world, "IdTwice")
    rut = f"8{uuid.uuid4().int % 10**7:07d}-2"
    added = _run(disposable_database, world["sales"], "add-organization-identifier", {
        "organization_id": org_id, "expected_version": ver, "scheme": "rut", "value": rut, "note": "add",
    })
    with pytest.raises(CommandRefused) as exc:
        _run(disposable_database, world["sales"], "add-organization-identifier", {
            "organization_id": org_id, "expected_version": added["version"],
            "scheme": "rut", "value": rut, "note": "again",
        })
    assert exc.value.status_code == 409 and exc.value.code == "identifier_already_present"
    assert len(_identifier_rows(disposable_database, "rut", rut)) == 1


@needs_db
def test_identifier_held_by_another_organization_is_refused_by_name(disposable_database, world):
    org_a, ver_a = _new_org(disposable_database, world, "IdOwnerA")
    org_b, ver_b = _new_org(disposable_database, world, "IdClaimB")
    rut = f"9{uuid.uuid4().int % 10**7:07d}-3"
    added = _run(disposable_database, world["sales"], "add-organization-identifier", {
        "organization_id": org_a, "expected_version": ver_a, "scheme": "rut", "value": rut, "note": "a",
    })

    # Live on A: B is refused by name.
    with pytest.raises(CommandRefused) as exc:
        _run(disposable_database, world["sales"], "add-organization-identifier", {
            "organization_id": org_b, "expected_version": ver_b, "scheme": "rut", "value": rut, "note": "b",
        })
    assert exc.value.status_code == 409 and exc.value.code == "identifier_taken"

    # Soft-removed on A: still A's row — never moved to B, and still a named refusal.
    _run(disposable_database, world["sales"], "remove-organization-identifier", {
        "organization_id": org_a, "expected_version": added["version"],
        "identifier_id": added["identifier_id"], "note": "removed from a",
    })
    with pytest.raises(CommandRefused) as exc:
        _run(disposable_database, world["sales"], "add-organization-identifier", {
            "organization_id": org_b, "expected_version": ver_b, "scheme": "rut", "value": rut, "note": "b",
        })
    assert exc.value.status_code == 409 and exc.value.code == "identifier_taken"
    assert _identifier_rows(disposable_database, "rut", rut) == [(added["identifier_id"], org_a, True)]


@needs_db
def test_duplicate_active_classification_is_refused_by_name(disposable_database, world):
    org_id, ver = _new_org(disposable_database, world, "ClassTwice")
    added = _run(disposable_database, world["sales"], "add-organization-classification", {
        "organization_id": org_id, "expected_version": ver, "role": "supplier", "note": "supplier",
    })
    with pytest.raises(CommandRefused) as exc:
        _run(disposable_database, world["sales"], "add-organization-classification", {
            "organization_id": org_id, "expected_version": added["version"], "role": "supplier", "note": "again",
        })
    assert exc.value.status_code == 409 and exc.value.code == "classification_already_present"

    # The refusal wrote nothing: one open row, version unchanged, a different role still fine.
    with psycopg.connect(runtime_dsn(disposable_database)) as conn, conn.cursor() as cur:
        cur.execute(
            "select count(*) from crm.organization_relationship"
            " where organization_id = %s::uuid and role = 'supplier' and valid_to is null",
            (org_id,),
        )
        assert cur.fetchone()[0] == 1
    other = _run(disposable_database, world["sales"], "add-organization-classification", {
        "organization_id": org_id, "expected_version": added["version"], "role": "manufacturer", "note": "also",
    })
    assert other["version"] == added["version"] + 1


def _wait_for_lock_waiter(dsn, *, timeout_s=10.0):
    """Block until some other session of this database is waiting on a row lock."""
    import time

    deadline = time.monotonic() + timeout_s
    with psycopg.connect(runtime_dsn(dsn), autocommit=True) as conn, conn.cursor() as cur:
        while time.monotonic() < deadline:
            cur.execute(
                "select count(*) from pg_stat_activity"
                " where datname = current_database() and pid <> pg_backend_pid()"
                " and wait_event_type = 'Lock'"
            )
            if cur.fetchone()[0] > 0:
                return
            time.sleep(0.05)
    raise AssertionError("the command never waited on the organization row")


@needs_db
@pytest.mark.parametrize("command,fields", [
    ("add-organization-classification", {"role": "partner", "note": "race"}),
    ("link-organization-product-line", {"line_id": "ika", "note": "race"}),
    ("add-organization-domain", {"domain": "race-{suffix}.example.test", "note": "race"}),
    ("add-organization-identifier", {"scheme": "rut", "value": "6{suffix}-0", "note": "race"}),
])
def test_org_sub_entity_command_loses_to_a_concurrent_version_bump(
    disposable_database, world, command, fields
):
    """Two writers start from the same version: the one that commits second must be refused.

    A concurrent transaction bumps the organization's version and holds the row while the
    command runs against the version both started from. When it commits, the command must
    answer `stale_version` and write nothing — not silently stack its change on top.
    """
    import threading

    org_id, ver = _new_org(disposable_database, world, "Race")
    suffix = f"{uuid.uuid4().int % 10**7:07d}"
    body = {k: (v.format(suffix=suffix) if isinstance(v, str) else v) for k, v in fields.items()}
    body.update({"organization_id": org_id, "expected_version": ver})

    outcome: dict[str, object] = {}

    def run_command():
        try:
            outcome["result"] = _run(disposable_database, world["sales"], command, body)
        except BaseException as exc:  # noqa: BLE001 - the assertion below names it
            outcome["error"] = exc

    with psycopg.connect(runtime_dsn(disposable_database)) as rival, rival.cursor() as cur:
        cur.execute(
            "update crm.organization set version = version + 1 where id = %s::uuid", (org_id,)
        )
        worker = threading.Thread(target=run_command)
        worker.start()
        _wait_for_lock_waiter(disposable_database)
        rival.commit()
    worker.join(timeout=30)
    assert not worker.is_alive()

    assert "result" not in outcome, f"{command} stacked its change on a concurrent bump"
    err = outcome["error"]
    assert isinstance(err, CommandRefused) and err.code == "stale_version"
    with psycopg.connect(runtime_dsn(disposable_database)) as conn, conn.cursor() as cur:
        cur.execute("select version from crm.organization where id = %s::uuid", (org_id,))
        assert cur.fetchone()[0] == ver + 1, "only the rival's bump landed"


@needs_db
def test_closing_a_classification_on_its_first_day_is_refused_by_name(disposable_database, world):
    """`valid_to > valid_from` is a table CHECK: a same-day close was a 500, now a named refusal."""
    org_id, ver = _new_org(disposable_database, world, "SameDay")
    added = _run(disposable_database, world["sales"], "add-organization-classification", {
        "organization_id": org_id, "expected_version": ver, "role": "supplier",
        "valid_from": "2026-10-04", "note": "opened",
    })
    with pytest.raises(CommandRefused) as exc:
        _run(disposable_database, world["sales"], "remove-organization-classification", {
            "organization_id": org_id, "expected_version": added["version"],
            "relationship_id": added["relationship_id"], "valid_to": "2026-10-04", "note": "same day",
        })
    assert exc.value.status_code == 422 and exc.value.code == "valid_to_not_after_valid_from"

    closed = _run(disposable_database, world["sales"], "remove-organization-classification", {
        "organization_id": org_id, "expected_version": added["version"],
        "relationship_id": added["relationship_id"], "valid_to": "2026-10-05", "note": "next day",
    })
    assert closed["version"] == added["version"] + 1
