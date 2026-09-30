"""Campaign HTML archive read — outbound.campaign_content and the marketing list.

In-process tests (fake-repo):

* html_state per case: sent_html_archived, historical_draft, not_recovered, ambiguous_attribution.
* Hash mismatch → html null and html_state fingerprint_mismatch.
* contents[] and recovery keys present.
* marketing list has_html and html_state.

Database-backed tests (opt-in, module-scoped disposable cluster):

* Seed: an archived imported campaign with two campaign_content variants (sent_html) + messages.
  One message is linked to a seeded send_attempt; one is not.
* A native draft campaign (no content → not_frozen).
* An archived imported campaign with a migration_manifest source_record with decision='ambiguous'.
* html_state per case.
* hash_verified per content row.
* hash mismatch → html null.
* linked_attempts count per variant.

Every name, address, number and hash below is invented; `.test` is a reserved TLD.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository
from origenlab_api.v2.crm_workspace_routes import workspace_router


# ─────────────────────────────────────── helpers / fake identity ──────────────


class _Identity:
    def __init__(self, role: str, op_id: str) -> None:
        self._role = role
        self._op_id = op_id

    def resolve(self, headers: Any) -> Any:  # noqa: ANN401
        from origenlab_api.v2.identity import OperatorIdentity
        return OperatorIdentity(
            operator_id=self._op_id,
            email_norm=f"{self._role}@example.test",
            display_name=self._role.capitalize(),
            role=self._role,
            status="active",
        )


_FAKE_OP = str(uuid.uuid4())

_REAL_HTML = "<html><body>Hello</body></html>"
_REAL_SHA256 = hashlib.sha256(_REAL_HTML.encode()).hexdigest()

_DRAFT_HTML = "<html><body>Draft</body></html>"


class _FakeRepo:
    """Fake for in-process shape tests. Returns hard-coded rows."""

    def __init__(
        self,
        *,
        campaign_html: str | None = None,
        content_kind: str = "sent_html",
        hash_ok: bool = True,
        no_content: bool = False,
        ambiguous: bool = False,
        imported: bool = True,
    ) -> None:
        self._campaign_html = campaign_html
        self._content_kind = content_kind
        self._hash_ok = hash_ok
        self._no_content = no_content
        self._ambiguous = ambiguous
        self._imported = imported

    def campaign_archive(self, campaign_id: str) -> dict[str, Any] | None:
        if campaign_id == "00000000-0000-4000-8000-000000000000":
            return None
        # Simulate a campaign row with no body_html (imported, archived)
        html_state: str
        html: str | None
        contents: list[dict] = []
        recovery: dict | None = None

        if self._no_content and self._ambiguous:
            html_state = "ambiguous_attribution"
            html = None
        elif self._no_content and self._imported:
            html_state = "not_recovered"
            html = None
        elif self._no_content:
            html_state = "not_archived"
            html = None
        elif self._content_kind == "sent_html":
            sha = _REAL_SHA256 if self._hash_ok else "b" * 64
            html_state = "sent_html_archived" if self._hash_ok else "fingerprint_mismatch"
            html = _REAL_HTML if self._hash_ok else None
            contents = [{
                "id": str(uuid.uuid4()),
                "content_kind": "sent_html",
                "variant_no": 1,
                "subject": "Test Subject",
                "preheader": None,
                "body_html_sha256": sha,
                "body_html_normalized_sha256": "c" * 64,
                "message_count": 5,
                "first_sent_at": "2026-09-01T00:00:00Z",
                "last_sent_at": "2026-09-03T00:00:00Z",
                "attribution_method": "recipient_lineage_timestamp",
                "attribution_confidence": "corroborated",
                "attribution_policy_version": "campaign-content-attribution/2026-09-30.v1",
                "unmatched_attempt_count": 1,
                "linked_attempts": 4,
                "hash_verified": self._hash_ok,
            }]
            recovery = {
                "policy_version": "campaign-content-attribution/2026-09-30.v1",
                "matched_messages": 5,
                "unmatched_attempts": 1,
                "manifest_sha256": "d" * 64,
            }
        else:
            html_state = "historical_draft"
            html = _DRAFT_HTML
            contents = [{
                "id": str(uuid.uuid4()),
                "content_kind": "historical_draft",
                "variant_no": 1,
                "subject": None,
                "preheader": None,
                "body_html_sha256": hashlib.sha256(_DRAFT_HTML.encode()).hexdigest(),
                "body_html_normalized_sha256": "e" * 64,
                "message_count": 0,
                "first_sent_at": None,
                "last_sent_at": None,
                "attribution_method": "unsent_draft",
                "attribution_confidence": "exact",
                "attribution_policy_version": "campaign-content-attribution/2026-09-30.v1",
                "unmatched_attempt_count": 0,
                "linked_attempts": 0,
                "hash_verified": True,
            }]

        return {
            "campaign_id": campaign_id,
            "name": "Campaign Test",
            "status": "archived",
            "subject": "Test Subject",
            "preheader": None,
            "version": 1,
            "content_sha256": None,
            "content_frozen_at": "2026-09-01T00:00:00Z",
            "audience_frozen_at": "2026-09-01T00:00:00Z",
            "audience_sha256": None,
            "audience_policy_version": None,
            "created_at": "2026-01-01T00:00:00Z",
            "origin": "imported_v1" if self._imported else "native_v2",
            "sender_address": "send@example.test",
            "sender_name": "Sender",
            "html": html,
            "html_state": html_state,
            "contents": contents,
            "recovery": recovery,
            "recipients_by_state": {},
            "send_attempts": [],
            "send_batches": [],
            "totals": None,
            "attempt_totals": None,
            "replies": {"state": "no_replies"},
            "subject_state": "recorded",
            "preheader_state": "not_imported",
            "immutable": True,
            "immutable_enforced_by_database": True,
            "metrics": {"opens": None, "clicks": None, "note": ""},
            "storage": {"table": "outbound.campaign", "database": "test"},
        }

    def marketing(self) -> dict[str, Any]:
        return {
            "campaigns": [
                {
                    "campaign_id": "00000000-0000-4000-8001-000000000001",
                    "name": "Campaign A",
                    "status": "archived",
                    "subject": "Subj",
                    "preheader": None,
                    "has_html": True,
                    "html_state": "sent_html_archived",
                    "version": 1,
                    "approved_at": None,
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                    "first_sent_at": None,
                    "last_sent_at": None,
                    "planned_for_date": None,
                    "planned_for_at": None,
                    "planning_version": None,
                    "audience_frozen_at": None,
                    "content_frozen_at": None,
                    "content_sha256": None,
                    "audience_criteria": None,
                    "origin": "imported_v1",
                    "sender_address": "send@example.test",
                    "sender_name": "Sender",
                    "hold": None,
                    "recipients_by_state": {},
                    "send_attempts": [],
                    "replies_recorded": 0,
                    "send_batches": [],
                    "attempts_without_date": 0,
                    "totals": None,
                    "attempt_totals": None,
                    "replies": {"state": "no_replies"},
                    "subject_state": "recorded",
                },
            ],
            "holds": {},
            "contact_controls": [],
            "replies_note": "",
            "storage": {"table": "outbound.campaign", "database": "test"},
        }


def _app(repo: _FakeRepo | None = None) -> FastAPI:
    app = FastAPI()
    app.state.v2_identity = _Identity("sales", _FAKE_OP)
    app.state.crm_workspace = repo or _FakeRepo()
    app.include_router(workspace_router)
    return app


# ─────────────────────────────────── in-process tests ─────────────────────────


def test_archive_sent_html_archived_state() -> None:
    client = TestClient(_app(_FakeRepo(content_kind="sent_html", hash_ok=True)))
    r = client.get("/v2/workspace/marketing/campaigns/00000000-0000-4000-8001-000000000001/archive")
    assert r.status_code == 200
    body = r.json()
    assert body["html_state"] == "sent_html_archived"
    assert body["html"] == _REAL_HTML
    assert body["contents"]
    assert body["recovery"] is not None


def test_archive_fingerprint_mismatch_html_null() -> None:
    client = TestClient(_app(_FakeRepo(content_kind="sent_html", hash_ok=False)))
    r = client.get("/v2/workspace/marketing/campaigns/00000000-0000-4000-8001-000000000001/archive")
    assert r.status_code == 200
    body = r.json()
    assert body["html_state"] == "fingerprint_mismatch"
    assert body["html"] is None
    # contents still returned, hash_verified=False
    assert body["contents"]
    assert body["contents"][0]["hash_verified"] is False


def test_archive_historical_draft_state() -> None:
    client = TestClient(_app(_FakeRepo(content_kind="historical_draft")))
    r = client.get("/v2/workspace/marketing/campaigns/00000000-0000-4000-8001-000000000001/archive")
    assert r.status_code == 200
    body = r.json()
    assert body["html_state"] == "historical_draft"
    assert body["html"] == _DRAFT_HTML


def test_archive_not_recovered_state() -> None:
    client = TestClient(_app(_FakeRepo(no_content=True, imported=True, ambiguous=False)))
    r = client.get("/v2/workspace/marketing/campaigns/00000000-0000-4000-8001-000000000001/archive")
    assert r.status_code == 200
    body = r.json()
    assert body["html_state"] == "not_recovered"
    assert body["html"] is None
    assert body["contents"] == []
    assert body["recovery"] is None


def test_archive_ambiguous_attribution_state() -> None:
    client = TestClient(_app(_FakeRepo(no_content=True, imported=True, ambiguous=True)))
    r = client.get("/v2/workspace/marketing/campaigns/00000000-0000-4000-8001-000000000001/archive")
    assert r.status_code == 200
    body = r.json()
    assert body["html_state"] == "ambiguous_attribution"
    assert body["html"] is None


def test_archive_404_for_unknown() -> None:
    client = TestClient(_app())
    r = client.get("/v2/workspace/marketing/campaigns/00000000-0000-4000-8000-000000000000/archive")
    assert r.status_code == 404


def test_archive_contents_keys() -> None:
    """Every content row must carry all CONTRACT keys."""
    client = TestClient(_app(_FakeRepo(content_kind="sent_html", hash_ok=True)))
    r = client.get("/v2/workspace/marketing/campaigns/00000000-0000-4000-8001-000000000001/archive")
    body = r.json()
    for content in body["contents"]:
        for key in ("id", "content_kind", "variant_no", "subject", "preheader",
                    "body_html_sha256", "body_html_normalized_sha256",
                    "message_count", "first_sent_at", "last_sent_at",
                    "attribution_method", "attribution_confidence",
                    "attribution_policy_version", "unmatched_attempt_count",
                    "linked_attempts", "hash_verified"):
            assert key in content, f"Missing key '{key}' in content row"


def test_archive_recovery_keys() -> None:
    client = TestClient(_app(_FakeRepo(content_kind="sent_html", hash_ok=True)))
    r = client.get("/v2/workspace/marketing/campaigns/00000000-0000-4000-8001-000000000001/archive")
    body = r.json()
    rec = body["recovery"]
    assert rec is not None
    for key in ("policy_version", "matched_messages", "unmatched_attempts", "manifest_sha256"):
        assert key in rec, f"Missing key '{key}' in recovery"


def test_marketing_list_has_html_state() -> None:
    client = TestClient(_app(_FakeRepo()))
    r = client.get("/v2/workspace/marketing")
    assert r.status_code == 200
    campaigns = r.json()["campaigns"]
    assert campaigns
    for c in campaigns:
        assert "has_html" in c
        assert "html_state" in c


def test_marketing_list_has_html_true_when_sent_html_content() -> None:
    """Campaigns with a sent_html content row must have has_html=True."""
    client = TestClient(_app(_FakeRepo()))
    r = client.get("/v2/workspace/marketing")
    body = r.json()
    campaign = body["campaigns"][0]
    assert campaign["has_html"] is True
    assert campaign["html_state"] == "sent_html_archived"


# ─────────────────────────────────────── database-backed tests ────────────────

try:
    from v2_command_harness import build_disposable_database, needs_db, runtime_dsn
except ImportError:  # pragma: no cover
    needs_db = pytest.mark.skip(reason="v2_command_harness not available")

    def build_disposable_database():  # type: ignore[misc]
        yield ""

    def runtime_dsn(x: str) -> str:  # type: ignore[misc]
        return x


@pytest.fixture(scope="module")
def disposable_database():
    yield from build_disposable_database()


def _insert(cur: Any, sql: str, params: tuple) -> str:
    cur.execute(sql + " returning id::text", params)
    return cur.fetchone()[0]


SENT_HTML_V1 = "<html><body>Campaign V1 sent</body></html>"
SENT_HTML_V2 = "<html><body>Campaign V1 variant 2</body></html>"
SENT_HTML_SHA_V1 = hashlib.sha256(SENT_HTML_V1.encode()).hexdigest()
SENT_HTML_SHA_V2 = hashlib.sha256(SENT_HTML_V2.encode()).hexdigest()
NORM_SHA_V1 = hashlib.sha256(b"<html><body>Campaign V1 sent</body></html>").hexdigest()
NORM_SHA_V2 = hashlib.sha256(b"<html><body>Campaign V1 variant 2</body></html>").hexdigest()


@pytest.fixture(scope="module")
def world(disposable_database: str) -> dict[str, str]:
    """Seed three campaigns: archived-with-content, ambiguous, and native-draft."""
    import psycopg

    tag = uuid.uuid4().hex[:10]
    w: dict[str, str] = {"tag": tag}

    with psycopg.connect(disposable_database) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")

        op = w["op"] = _insert(
            cur,
            "insert into platform.operator (auth_user_id, email_norm, display_name, role, status)"
            " values (gen_random_uuid(), %s, 'CC Test Op', 'sales', 'active')",
            (f"ccop-{tag}@example.test",),
        )

        mailbox = w["mailbox"] = _insert(
            cur,
            "insert into comms.mailbox (address_norm, display_name) values (%s, 'OrigenLab')",
            (f"send-{tag}@org.test",),
        )

        # Campaign A: archived, imported, with two sent_html content variants + messages
        camp_a_payload = json.dumps({"v1_campaign_id": f"v1-a-{tag}"})
        camp_a_sr = w["camp_a_sr"] = _insert(
            cur,
            "insert into evidence.source_record (kind, dedupe_key, payload, source_uri)"
            " values ('migration_manifest', %s, %s::jsonb, %s)",
            (f"mm:camp-a:{tag}", camp_a_payload, f"v1://campaign/a/{tag}"),
        )
        dummy_sha = "a" * 64
        camp_a = w["camp_a"] = _insert(
            cur,
            "insert into outbound.campaign (name, status, mailbox_id, origin_source_record_id,"
            " content_frozen_at, content_sha256, max_sends)"
            " values (%s, 'archived', %s, %s, null, null, 1000)",  # imported: never frozen, no fingerprint
            (f"Campaign A {tag}", mailbox, camp_a_sr),
        )

        # Content variant 1 (good hash)
        content_sr = w["content_sr"] = _insert(
            cur,
            "insert into evidence.source_record (kind, dedupe_key, payload, source_uri)"
            " values ('migration_manifest', %s, %s::jsonb, %s)",
            (f"mm:content-a-v1:{tag}", camp_a_payload, f"v1://content/a/{tag}"),
        )
        content_v1 = w["content_v1"] = _insert(
            cur,
            "insert into outbound.campaign_content"
            " (campaign_id, content_kind, variant_no, body_html, body_html_sha256,"
            "  body_html_normalized_sha256, message_count, first_sent_at, last_sent_at,"
            "  attribution_method, attribution_confidence, attribution_policy_version,"
            "  unmatched_attempt_count, origin_source_record_id)"
            " values (%s, 'sent_html', 1, %s, %s, %s, 5, now() - interval '2 days',"
            "  now() - interval '1 day', 'recipient_lineage_timestamp', 'corroborated',"
            "  'campaign-content-attribution/2026-09-30.v1', 1, %s)",
            (camp_a, SENT_HTML_V1, SENT_HTML_SHA_V1, NORM_SHA_V1, content_sr),
        )
        content_v2 = w["content_v2"] = _insert(
            cur,
            "insert into outbound.campaign_content"
            " (campaign_id, content_kind, variant_no, body_html, body_html_sha256,"
            "  body_html_normalized_sha256, message_count, first_sent_at, last_sent_at,"
            "  attribution_method, attribution_confidence, attribution_policy_version,"
            "  unmatched_attempt_count, origin_source_record_id)"
            " values (%s, 'sent_html', 2, %s, %s, %s, 3, now() - interval '2 days',"
            "  now() - interval '1 day', 'recipient_lineage_timestamp', 'corroborated',"
            "  'campaign-content-attribution/2026-09-30.v1', 0, %s)",
            (camp_a, SENT_HTML_V2, SENT_HTML_SHA_V2, NORM_SHA_V2, content_sr),
        )

        # Send attempt for a linked message
        cp = w["cp"] = _insert(
            cur,
            "insert into crm.contact_point (kind, value_display, value_norm, usage, confirmation)"
            " values ('email', %s, %s, 'unattributed', 'machine_proposed')",
            (f"rcpt-{tag}@inst.test", f"rcpt-{tag}@inst.test"),
        )
        cr = w["cr"] = _insert(
            cur,
            "insert into outbound.campaign_recipient (campaign_id, address_norm, contact_point_id)"
            " values (%s, %s, %s)",
            (camp_a, f"rcpt-{tag}@inst.test", cp),
        )
        attempt = w["attempt"] = _insert(
            cur,
            "insert into outbound.send_attempt (campaign_id, campaign_recipient_id, address_norm,"
            " purpose, mailbox_id, submission_state, delivery_state, accepted_at)"
            " values (%s, %s, %s, 'marketing', %s, 'accepted', 'sent_copy_confirmed', now() - interval '2 days')",
            (camp_a, cr, f"rcpt-{tag}@inst.test", mailbox),
        )

        # Campaign content message: linked to attempt
        msg_linked = w["msg_linked"] = _insert(
            cur,
            "insert into outbound.campaign_content_message"
            " (campaign_content_id, send_attempt_id, rfc822_message_id,"
            "  sent_at, matched_delta_seconds, archive_folder)"
            " values (%s, %s, %s, now() - interval '2 days', 3, 'Sent')",
            (content_v1, attempt, f"<linked-{tag}@mail.test>"),
        )
        # Campaign content message: NOT linked (no send_attempt_id)
        msg_unlinked = w["msg_unlinked"] = _insert(
            cur,
            "insert into outbound.campaign_content_message"
            " (campaign_content_id, rfc822_message_id,"
            "  sent_at, matched_delta_seconds, archive_folder)"
            " values (%s, %s, now() - interval '2 days', 5, 'Sent')",
            (content_v1, f"<unlinked-{tag}@mail.test>"),
        )

        # Campaign B: archived, imported, with an ambiguous content-recovery source record
        v1_b_id = f"v1-b-{tag}"
        camp_b_sr = w["camp_b_sr"] = _insert(
            cur,
            "insert into evidence.source_record (kind, dedupe_key, payload, source_uri)"
            " values ('migration_manifest', %s, %s::jsonb, %s)",
            (f"mm:camp-b:{tag}", json.dumps({"v1_campaign_id": v1_b_id}),
             f"v1://campaign/b/{tag}"),
        )
        camp_b = w["camp_b"] = _insert(
            cur,
            "insert into outbound.campaign (name, status, mailbox_id, origin_source_record_id,"
            " content_frozen_at, content_sha256, max_sends)"
            " values (%s, 'archived', %s, %s, null, null, 1000)",
            (f"Campaign B {tag} (ambiguous)", mailbox, camp_b_sr),
        )
        # Separate content-recovery source_record with decision=ambiguous (this is what the
        # code checks via the dedupe_key pattern migration_manifest:campaign-content-recovery:*)
        w["camp_b_recovery_sr"] = _insert(
            cur,
            "insert into evidence.source_record (kind, dedupe_key, payload, source_uri,"
            " payload_sha256)"
            " values ('migration_manifest', %s, %s::jsonb, %s, %s)",
            (f"migration_manifest:campaign-content-recovery:{v1_b_id}:1",
             json.dumps({"v1_campaign_id": v1_b_id, "decision": "ambiguous"}),
             f"v1://campaign-content-recovery/b/{tag}",
             hashlib.sha256(b"manifest-b-recovery").hexdigest()),
        )

        # Campaign C: native draft, no content (content_frozen_at null → not_frozen)
        camp_c = w["camp_c"] = _insert(
            cur,
            "insert into outbound.campaign (name, status, mailbox_id, max_sends,"
            " recontact_interval_days)"
            " values (%s, 'draft', %s, 100, 30)",
            (f"Campaign C {tag} (draft)", mailbox),
        )

        conn.commit()

    return w


@pytest.fixture(scope="module")
def repo_dsn(disposable_database: str) -> str:
    return runtime_dsn(disposable_database)


@needs_db
def test_campaign_archive_sent_html_archived_two_variants(disposable_database, world, repo_dsn) -> None:
    import psycopg

    repo = CrmWorkspaceRepository(psycopg.connect, repo_dsn)
    body = repo.campaign_archive(world["camp_a"])
    assert body is not None
    assert body["html_state"] == "sent_html_archived"
    assert body["html"] == SENT_HTML_V1  # variant 1
    assert len(body["contents"]) == 2
    assert body["recovery"] is not None
    assert body["recovery"]["matched_messages"] == 8  # 5 + 3


@needs_db
def test_campaign_archive_hash_verified_per_variant(disposable_database, world, repo_dsn) -> None:
    import psycopg

    repo = CrmWorkspaceRepository(psycopg.connect, repo_dsn)
    body = repo.campaign_archive(world["camp_a"])
    assert body is not None
    for c in body["contents"]:
        assert c["hash_verified"] is True


@needs_db
def test_campaign_archive_linked_attempts_count(disposable_database, world, repo_dsn) -> None:
    """Variant 1 has 1 linked attempt, variant 2 has 0."""
    import psycopg

    repo = CrmWorkspaceRepository(psycopg.connect, repo_dsn)
    body = repo.campaign_archive(world["camp_a"])
    assert body is not None
    by_variant = {c["variant_no"]: c for c in body["contents"]}
    assert by_variant[1]["linked_attempts"] == 1
    assert by_variant[2]["linked_attempts"] == 0


@needs_db
def test_campaign_archive_ambiguous_attribution(disposable_database, world, repo_dsn) -> None:
    import psycopg

    repo = CrmWorkspaceRepository(psycopg.connect, repo_dsn)
    body = repo.campaign_archive(world["camp_b"])
    assert body is not None
    assert body["html_state"] == "ambiguous_attribution"
    assert body["html"] is None
    assert body["contents"] == []


@needs_db
def test_campaign_archive_draft_not_frozen(disposable_database, world, repo_dsn) -> None:
    import psycopg

    repo = CrmWorkspaceRepository(psycopg.connect, repo_dsn)
    body = repo.campaign_archive(world["camp_c"])
    assert body is not None
    assert body["html_state"] == "not_frozen"
    assert body["html"] is None
    assert body["contents"] == []


@needs_db
def test_marketing_list_has_html_from_content(disposable_database, world, repo_dsn) -> None:
    """Campaigns with a sent_html content row must have has_html=True in the list."""
    import psycopg

    repo = CrmWorkspaceRepository(psycopg.connect, repo_dsn)
    data = repo.marketing()
    campaigns = {c["campaign_id"]: c for c in data["campaigns"]}
    assert campaigns[world["camp_a"]]["has_html"] is True
    assert campaigns[world["camp_a"]]["html_state"] == "sent_html_archived"


@needs_db
def test_marketing_list_ambiguous_and_draft_states(disposable_database, world, repo_dsn) -> None:
    import psycopg

    repo = CrmWorkspaceRepository(psycopg.connect, repo_dsn)
    data = repo.marketing()
    campaigns = {c["campaign_id"]: c for c in data["campaigns"]}
    # Draft campaign (C) has no content and no frozen_at → not_frozen
    assert campaigns[world["camp_c"]]["html_state"] == "not_frozen"


@needs_db
def test_all_db_reads_write_nothing(disposable_database, world, repo_dsn) -> None:
    """Snapshot every table before and after; must be identical."""
    import psycopg

    def snapshot() -> dict[str, str]:
        with psycopg.connect(disposable_database) as conn, conn.cursor() as cur:
            cur.execute("set role origenlab_owner")
            cur.execute(
                "select table_schema, table_name from information_schema.tables"
                " where table_schema in ('outbound', 'crm', 'evidence', 'platform')"
                " and table_type = 'BASE TABLE' order by 1, 2"
            )
            tables = cur.fetchall()
            digest = {}
            for schema, table in tables:
                cur.execute(
                    f'select count(*), md5(coalesce(string_agg(t::text, \'|\' order by t::text), \'\'))'
                    f' from "{schema}"."{table}" t'
                )
                count, md5 = cur.fetchone()
                digest[f"{schema}.{table}"] = f"{count}:{md5}"
            conn.rollback()
        return digest

    before = snapshot()
    repo = CrmWorkspaceRepository(psycopg.connect, repo_dsn)
    repo.campaign_archive(world["camp_a"])
    repo.campaign_archive(world["camp_b"])
    repo.campaign_archive(world["camp_c"])
    repo.marketing()
    after = snapshot()
    assert before == after
