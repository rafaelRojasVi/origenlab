"""ARCH-2B commercial opportunity list/detail API."""

# ruff: noqa: E402

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from origenlab_api.backends.factory import get_repository_bundle
from origenlab_api.main import create_app
from origenlab_api.repositories.postgres.commercial_opportunities import (
    PostgresCommercialOpportunityRepository,
)
from origenlab_api.repositories.sqlite.commercial_opportunities import (
    SqliteCommercialOpportunityRepository,
)
from origenlab_api.settings import Settings, get_settings


def _build_fixture(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE commercial_identity_account (
              account_id TEXT PRIMARY KEY,
              canonical_name TEXT NOT NULL,
              normalized_name TEXT NOT NULL,
              primary_domain TEXT
            );

            CREATE TABLE commercial_identity_contact (
              contact_id TEXT PRIMARY KEY,
              normalized_email TEXT NOT NULL UNIQUE,
              account_id TEXT
            );

            CREATE TABLE commercial_opportunity (
              opportunity_id TEXT PRIMARY KEY,
              record_kind TEXT NOT NULL,
              account_id TEXT,
              primary_contact_id TEXT,
              source_kind TEXT NOT NULL,
              source_key TEXT NOT NULL,
              commercial_deal_id INTEGER,
              deal_key TEXT,
              canonical_stage TEXT NOT NULL,
              source_stage TEXT NOT NULL,
              stage_reason_code TEXT NOT NULL,
              stage_confidence TEXT NOT NULL,
              stage_is_current INTEGER NOT NULL,
              stage_is_terminal INTEGER NOT NULL,
              stage_evidence_at TEXT,
              stage_evidence_id TEXT,
              first_activity_at TEXT,
              last_activity_at TEXT,
              identity_link_status TEXT NOT NULL,
              review_status TEXT NOT NULL
            );

            CREATE TABLE commercial_opportunity_event (
              event_id TEXT PRIMARY KEY,
              opportunity_id TEXT NOT NULL,
              canonical_event_type TEXT NOT NULL,
              source_event_type TEXT NOT NULL,
              event_at TEXT,
              source_table TEXT NOT NULL,
              source_record_id TEXT NOT NULL,
              source_email_id INTEGER,
              source_attachment_id INTEGER,
              confidence TEXT NOT NULL,
              operator_confirmed INTEGER NOT NULL,
              detail_json TEXT
            );

            CREATE TABLE commercial_opportunity_evidence (
              evidence_id TEXT PRIMARY KEY,
              opportunity_id TEXT NOT NULL,
              subject_kind TEXT NOT NULL,
              source_table TEXT NOT NULL,
              source_record_id TEXT NOT NULL,
              evidence_type TEXT NOT NULL,
              evidence_at TEXT,
              confidence TEXT NOT NULL,
              reason_code TEXT NOT NULL,
              source_email_id INTEGER,
              source_attachment_id INTEGER,
              detail_json TEXT
            );

            CREATE TABLE commercial_opportunity_conflict (
              conflict_id TEXT PRIMARY KEY,
              opportunity_id TEXT,
              conflict_type TEXT NOT NULL,
              reason_code TEXT NOT NULL,
              subject_keys_json TEXT NOT NULL,
              evidence_pointers_json TEXT NOT NULL,
              review_status TEXT NOT NULL,
              detail_json TEXT
            );
            """
        )

        conn.execute(
            """
            INSERT INTO commercial_identity_account
              (account_id, canonical_name, normalized_name, primary_domain)
            VALUES (?, ?, ?, ?)
            """,
            ("a_1", "Universidad Uno", "universidad uno", "uno.cl"),
        )
        conn.execute(
            """
            INSERT INTO commercial_identity_contact
              (contact_id, normalized_email, account_id)
            VALUES (?, ?, ?)
            """,
            ("c_1", "buyer@uno.cl", "a_1"),
        )

        opportunities = [
            (
                "o_old",
                "commercial_signal",
                "a_1",
                "c_1",
                "email",
                "email:10",
                None,
                None,
                "lead",
                "lead",
                "inquiry_seen",
                "medium",
                1,
                0,
                "2026-01-02T00:00:00+00:00",
                None,
                "2026-01-01T00:00:00+00:00",
                "2026-01-02T00:00:00+00:00",
                "linked",
                "clear",
            ),
            (
                "o_new",
                "commercial_signal",
                "a_1",
                "c_1",
                "email",
                "email:20",
                None,
                "deal:test",
                "qualified",
                "quote_requested",
                "quote_request",
                "high",
                1,
                0,
                "2026-02-03T00:00:00+00:00",
                "evd_1",
                "2026-02-01T00:00:00+00:00",
                "2026-02-03T00:00:00+00:00",
                "linked",
                "review",
            ),
        ]

        conn.executemany(
            """
            INSERT INTO commercial_opportunity VALUES (
              ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            opportunities,
        )

        conn.execute(
            """
            INSERT INTO commercial_opportunity_event VALUES (
              ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                "evt_1",
                "o_new",
                "quote_requested",
                "quote_requested",
                "2026-02-03T00:00:00+00:00",
                "emails",
                "20",
                20,
                None,
                "high",
                1,
                json.dumps({"subject": "cotizacion"}),
            ),
        )

        conn.execute(
            """
            INSERT INTO commercial_opportunity_evidence VALUES (
              ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                "evd_1",
                "o_new",
                "opportunity",
                "emails",
                "20",
                "quote_request",
                "2026-02-03T00:00:00+00:00",
                "high",
                "quote_request",
                20,
                None,
                json.dumps({"source": "email"}),
            ),
        )

        conn.execute(
            """
            INSERT INTO commercial_opportunity_conflict VALUES (
              ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                "conf_1",
                "o_new",
                "stage_conflict",
                "ambiguous_stage",
                json.dumps(["o_new"]),
                json.dumps(["evd_1"]),
                "review",
                json.dumps({"note": "manual review"}),
            ),
        )

        conn.commit()
    finally:
        conn.close()


def _client(tmp_path: Path) -> TestClient:
    db = tmp_path / "arch2b.sqlite"
    _build_fixture(db)

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        sqlite_path=db,
        api_backend="sqlite",
    )
    return TestClient(app)


def test_commercial_opportunities_list_and_identity_enrichment(
    tmp_path: Path,
) -> None:
    data = _client(tmp_path).get("/opportunities/commercial").json()

    assert data["meta"]["data_source"] == "sqlite_pr3"
    assert data["meta"]["read_only"] is True
    assert data["meta"]["count"] == 2
    assert data["meta"]["total_count"] == 2
    assert [row["opportunity_id"] for row in data["items"]] == [
        "o_new",
        "o_old",
    ]
    assert data["items"][0]["contact_display_email"] == "buyer@uno.cl"
    assert data["items"][0]["account_display_domain"] == "uno.cl"


def test_commercial_opportunities_filters_and_pagination(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)

    filtered = client.get("/opportunities/commercial?canonical_stage=qualified").json()
    assert filtered["meta"]["total_count"] == 1
    assert filtered["items"][0]["opportunity_id"] == "o_new"

    paged = client.get("/opportunities/commercial?limit=1&offset=1").json()
    assert paged["meta"]["count"] == 1
    assert paged["meta"]["total_count"] == 2
    assert paged["meta"]["limit"] == 1
    assert paged["meta"]["offset"] == 1
    assert paged["items"][0]["opportunity_id"] == "o_old"


def test_commercial_opportunity_detail_returns_graph(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/opportunities/commercial/o_new")
    assert response.status_code == 200

    data = response.json()
    assert data["meta"]["data_source"] == "sqlite_pr3"
    assert data["opportunity"]["opportunity_id"] == "o_new"
    assert data["opportunity"]["contact_display_email"] == "buyer@uno.cl"

    assert [item["event_id"] for item in data["events"]] == ["evt_1"]
    assert data["events"][0]["detail_json"] == {"subject": "cotizacion"}

    assert [item["evidence_id"] for item in data["evidence"]] == ["evd_1"]
    assert data["evidence"][0]["detail_json"] == {"source": "email"}

    assert [item["conflict_id"] for item in data["conflicts"]] == ["conf_1"]
    assert data["conflicts"][0]["subject_keys_json"] == ["o_new"]
    assert data["conflicts"][0]["evidence_pointers_json"] == ["evd_1"]


def test_commercial_opportunity_detail_not_found(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/opportunities/commercial/missing")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_commercial_opportunity_query_validation(tmp_path: Path) -> None:
    client = _client(tmp_path)

    assert client.get("/opportunities/commercial?limit=0").status_code == 422
    assert client.get("/opportunities/commercial?limit=201").status_code == 422
    assert client.get("/opportunities/commercial?offset=-1").status_code == 422


def test_commercial_repository_backend_selection(tmp_path: Path) -> None:
    db = tmp_path / "arch2b.sqlite"
    _build_fixture(db)

    sqlite_bundle = get_repository_bundle(
        Settings(sqlite_path=db, api_backend="sqlite")
    )
    assert isinstance(
        sqlite_bundle.commercial_opportunity,
        SqliteCommercialOpportunityRepository,
    )

    postgres_bundle = get_repository_bundle(
        Settings(
            api_backend="postgres",
            postgres_url="postgresql://127.0.0.1:5432/test",
        )
    )
    assert isinstance(
        postgres_bundle.commercial_opportunity,
        PostgresCommercialOpportunityRepository,
    )
