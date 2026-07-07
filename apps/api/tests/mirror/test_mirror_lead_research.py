"""Tests for GET /mirror/leads/* (redacted Postgres mirror)."""

from __future__ import annotations

from typing import Any, Generator

import pytest
from fastapi.testclient import TestClient

from origenlab_api.main import create_app
from origenlab_api.settings import get_settings

from conftest import _patch_mirror_postgres
from fake_lead_conn import LeadFakeConn

_FORBIDDEN_RESPONSE_KEYS = frozenset(
    {
        "evidence_email_id",
        "transfer_id",
        "operation_id",
        "source_file",
        "gmail_url",
        "body",
        "input_file_name",
        "batch_key",
    }
)


def _collect_keys(obj: object, keys: set[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.add(str(k))
            _collect_keys(v, keys)
    elif isinstance(obj, list):
        for item in obj:
            _collect_keys(item, keys)


@pytest.fixture
def lead_mirror_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> Generator[TestClient, None, None]:
    get_settings.cache_clear()
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u:p@localhost:5432/scratch")
    sqlite = tmp_path / "emails.sqlite"
    sqlite.write_bytes(b"")
    monkeypatch.setenv("ORIGENLAB_SQLITE_PATH", str(sqlite))
    _patch_mirror_postgres(monkeypatch, LeadFakeConn())
    with TestClient(create_app()) as client:
        yield client
    get_settings.cache_clear()


def _assert_lead_disclaimer_spacing(disclaimer: str) -> None:
    assert "OrigenLab. Revisión" in disclaimer
    assert "OrigenLab.Revisión" not in disclaimer


def test_mirror_list_prospects_disclaimer_spacing(lead_mirror_client: TestClient) -> None:
    r = lead_mirror_client.get("/mirror/leads/prospects", params={"limit": 50})
    assert r.status_code == 200
    _assert_lead_disclaimer_spacing(r.json()["disclaimer"])


def test_mirror_summary_disclaimer_spacing(lead_mirror_client: TestClient) -> None:
    r = lead_mirror_client.get("/mirror/leads/summary")
    assert r.status_code == 200
    _assert_lead_disclaimer_spacing(r.json()["disclaimer"])


def test_mirror_list_prospects_shape(lead_mirror_client: TestClient) -> None:
    r = lead_mirror_client.get("/mirror/leads/prospects", params={"limit": 50})
    assert r.status_code == 200
    body = r.json()
    assert body["table_available"] is True
    assert body["read_only"] is True
    assert body["data_source"] == "postgres_mirror"
    _assert_lead_disclaimer_spacing(body["disclaimer"])
    assert "revisión humana" in body["disclaimer"].lower()
    assert body["total"] == 12
    assert len(body["items"]) == 12
    keys: set[str] = set()
    _collect_keys(body, keys)
    assert not (_FORBIDDEN_RESPONSE_KEYS & keys)


def test_include_blocked_false_hides_blocked(lead_mirror_client: TestClient) -> None:
    r = lead_mirror_client.get(
        "/mirror/leads/prospects",
        params={"include_blocked": False, "limit": 50},
    )
    assert r.status_code == 200
    emails = [i["email"] for i in r.json()["items"]]
    assert "blocked@blocked.cl" not in emails


def test_filter_classification_and_search(lead_mirror_client: TestClient) -> None:
    r = lead_mirror_client.get(
        "/mirror/leads/prospects",
        params={"classification": "net_new_safe_review", "limit": 50},
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 6
    assert any(i["organization_name"] == "Acme Labs" for i in items)

    r2 = lead_mirror_client.get("/mirror/leads/prospects", params={"q": "Hospital", "limit": 50})
    assert r2.status_code == 200
    assert len(r2.json()["items"]) == 1


def test_detail_returns_evidence_and_recommendation(lead_mirror_client: TestClient) -> None:
    r = lead_mirror_client.get("/mirror/leads/prospects/contacto-acme-cl")
    assert r.status_code == 200
    body = r.json()
    assert body["prospect"]["organization_name"] == "Acme Labs"
    assert len(body["evidence"]) >= 1
    assert body["recommendation"] is not None
    assert body["recommendation"]["suggested_body_preview"]


def test_summary_counts(lead_mirror_client: TestClient) -> None:
    r = lead_mirror_client.get("/mirror/leads/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 13
    assert body["net_new_safe"] == 3
    assert body["gmail_historico"] == 2
    assert body["followup_antiguo"] == 1
    assert body["caso_activo"] == 1
    assert body["blocked_count"] == 1
    assert body["ready_to_contact"] >= 1
    assert body["already_contacted"] >= 1
    assert body["needs_email_enrichment"] >= 0
    assert body["tender_opportunity"] >= 0
    assert body["review_history"] >= 0
    assert (
        body["ready_to_contact"]
        + body["needs_email_enrichment"]
        + body["tender_opportunity"]
        + body["review_history"]
        + body["already_contacted"]
        + body["blocked_count"]
        == body["total"]
    )


def test_list_prospects_include_commercial_action_bucket(lead_mirror_client: TestClient) -> None:
    r = lead_mirror_client.get("/mirror/leads/prospects", params={"limit": 5})
    assert r.status_code == 200
    item = r.json()["items"][0]
    assert item["commercial_action_bucket"]
    assert item["operational_next_action"]


def test_gmail_historico_not_in_net_new_filter(lead_mirror_client: TestClient) -> None:
    r = lead_mirror_client.get(
        "/mirror/leads/prospects",
        params={"classification": "net_new_safe_review", "limit": 50},
    )
    assert r.status_code == 200
    emails = {i["email"] for i in r.json()["items"]}
    assert "ana@gmailhist.cl" not in emails
    assert "old@followup.cl" not in emails


def test_filter_source_type_gmail_historico(lead_mirror_client: TestClient) -> None:
    r = lead_mirror_client.get(
        "/mirror/leads/prospects",
        params={"source_type": "gmail_historico", "limit": 50},
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 2
    assert all(i["source_type"] == "gmail_historico" for i in items)
    assert any(i["organization_name"] == "Gmail Histórico Co" for i in items)
    assert any(i["organization_name"] == "RedSalud" for i in items)


def test_contact_scope_contacted_excludes_deepsearch_only(lead_mirror_client: TestClient) -> None:
    r = lead_mirror_client.get(
        "/mirror/leads/prospects",
        params={"contact_scope": "contacted", "limit": 50},
    )
    assert r.status_code == 200
    orgs = {i["organization_name"] for i in r.json()["items"]}
    assert "Acme Labs" not in orgs
    assert "Gmail Histórico Co" in orgs
    assert "RedSalud" in orgs


def test_contact_scope_deepsearch_only(lead_mirror_client: TestClient) -> None:
    r = lead_mirror_client.get(
        "/mirror/leads/prospects",
        params={"contact_scope": "deepsearch", "limit": 50},
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert items
    assert all(i["source_type"] == "deepsearch" for i in items)
    assert any(i["organization_name"] == "Acme Labs" for i in items)


def test_contact_scope_search_finds_redsalud(lead_mirror_client: TestClient) -> None:
    r = lead_mirror_client.get(
        "/mirror/leads/prospects",
        params={"contact_scope": "contacted", "q": "red", "limit": 5},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    assert any("redsalud" in (i.get("domain") or "").lower() for i in body["items"])


def test_contact_scope_followup_sent_no_inbound(lead_mirror_client: TestClient) -> None:
    r = lead_mirror_client.get(
        "/mirror/leads/prospects",
        params={"contact_scope": "followup", "limit": 50},
    )
    assert r.status_code == 200
    for item in r.json()["items"]:
        assert (item.get("gmail_received_count") or 0) == 0
        assert item["is_blocked"] is False


def test_contact_scope_contacted_includes_outreach_exact_match(
    lead_mirror_client: TestClient,
) -> None:
    r = lead_mirror_client.get(
        "/mirror/leads/prospects",
        params={"contact_scope": "contacted", "q": "sidecar-exact", "limit": 50},
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["email"] == "ops@sidecar-exact.cl"
    assert (item.get("gmail_sent_count") or 0) == 0
    assert (item.get("gmail_received_count") or 0) == 0


def test_contact_scope_contacted_includes_institutional_domain_match(
    lead_mirror_client: TestClient,
) -> None:
    r = lead_mirror_client.get(
        "/mirror/leads/prospects",
        params={"contact_scope": "contacted", "q": "institutional", "limit": 50},
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["email"] == "new@institutional.cl"
    assert (items[0].get("gmail_sent_count") or 0) == 0


def test_contact_scope_contacted_excludes_public_webmail_domain_match(
    lead_mirror_client: TestClient,
) -> None:
    r = lead_mirror_client.get(
        "/mirror/leads/prospects",
        params={"contact_scope": "contacted", "q": "freemail", "limit": 50},
    )
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_contact_scope_followup_includes_outreach_contacted_no_inbound(
    lead_mirror_client: TestClient,
) -> None:
    r = lead_mirror_client.get(
        "/mirror/leads/prospects",
        params={"contact_scope": "followup", "q": "sidecar-followup", "limit": 50},
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["email"] == "followup@sidecar-followup.cl"
    assert (item.get("gmail_received_count") or 0) == 0
    assert item["is_blocked"] is False


def test_contact_scope_active_includes_outreach_replied(
    lead_mirror_client: TestClient,
) -> None:
    r = lead_mirror_client.get(
        "/mirror/leads/prospects",
        params={"contact_scope": "active", "q": "sidecar-replied", "limit": 50},
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["email"] == "active@sidecar-replied.cl"


def test_contact_scope_net_new_excludes_outreach_contacted(
    lead_mirror_client: TestClient,
) -> None:
    r = lead_mirror_client.get(
        "/mirror/leads/prospects",
        params={"contact_scope": "net_new", "limit": 50},
    )
    assert r.status_code == 200
    emails = {i["email"] for i in r.json()["items"]}
    assert "ops@sidecar-exact.cl" not in emails
    assert "new@institutional.cl" not in emails
    assert "buyer@gmail.com" in emails


def test_contact_scope_blocked_unchanged(lead_mirror_client: TestClient) -> None:
    r = lead_mirror_client.get(
        "/mirror/leads/prospects",
        params={"contact_scope": "blocked", "limit": 50},
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["email"] == "blocked@blocked.cl"


def test_filter_source_type_caso_activo(lead_mirror_client: TestClient) -> None:
    r = lead_mirror_client.get(
        "/mirror/leads/prospects",
        params={"source_type": "caso_activo", "limit": 50},
    )
    assert r.status_code == 200
    assert len(r.json()["items"]) == 1
    assert r.json()["items"][0]["status"] == "hold_personalizado"
