"""GET /contacts/{email} — read-only contact intelligence."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from origenlab_api.main import create_app
from origenlab_api.settings import Settings

_CONTACTO_SENT = "gmail:contacto@origenlab.cl/[Gmail]/Enviados"


def _client(tmp_path: Path, db: Path) -> TestClient:
    active = tmp_path / "current"
    active.mkdir(parents=True)
    (active / "manifest.json").write_text(
        json.dumps(
            {
                "canonical_files": ["do_not_repeat_master.csv"],
                "campaign_mode": "equipment_first",
            }
        ),
        encoding="utf-8",
    )
    (active / "do_not_repeat_master.csv").write_text(
        "email_norm,reason\nknown@cliente.cl,dnr\n",
        encoding="utf-8",
    )

    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE contact_master (
            email TEXT PRIMARY KEY,
            contact_name_best TEXT,
            domain TEXT,
            organization_name_guess TEXT,
            first_seen_at TEXT,
            last_seen_at TEXT,
            total_emails INTEGER
        );
        CREATE TABLE organization_master (
            domain TEXT PRIMARY KEY,
            organization_name_guess TEXT,
            first_seen_at TEXT,
            last_seen_at TEXT,
            total_emails INTEGER
        );
        CREATE TABLE outreach_contact_state (
            contact_email_norm TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            first_contacted_at TEXT,
            last_contacted_at TEXT,
            source TEXT,
            notes TEXT,
            updated_at TEXT NOT NULL,
            updated_by TEXT,
            lead_id INTEGER
        );
        CREATE TABLE contact_email_suppression (
            email TEXT PRIMARY KEY,
            suppression_reason_code TEXT,
            suppression_reason_text TEXT,
            suppression_source TEXT,
            last_bounced_at TEXT,
            updated_at TEXT NOT NULL,
            updated_by TEXT
        );
        CREATE TABLE contact_domain_suppression (
            domain_norm TEXT PRIMARY KEY,
            suppression_reason_text TEXT,
            updated_at TEXT NOT NULL,
            updated_by TEXT
        );
        CREATE TABLE emails (
            id INTEGER PRIMARY KEY,
            date_iso TEXT,
            source_file TEXT,
            folder TEXT,
            sender TEXT,
            recipients TEXT,
            subject TEXT
        );
        """
    )
    conn.execute(
        """
        INSERT INTO contact_master VALUES (
            'known@cliente.cl', 'Known Client', 'cliente.cl', 'Cliente SA',
            '2026-01-01', '2026-05-19', 12
        )
        """
    )
    conn.execute(
        """
        INSERT INTO organization_master VALUES (
            'cliente.cl', 'Cliente SA', '2026-01-01', '2026-05-19', 50
        )
        """
    )
    conn.execute(
        """
        INSERT INTO outreach_contact_state VALUES (
            'known@cliente.cl', 'contacted', '2026-04-01', '2026-05-18',
            'test_source', 'note', '2026-05-18', 'operator', NULL
        )
        """,
    )
    conn.execute(
        """
        INSERT INTO contact_email_suppression VALUES (
            'blocked@x.cl', 'bounce', 'hard', 'test', NULL, '2026-05-01', 'op'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO contact_domain_suppression VALUES (
            'blocked-domain.cl', 'org policy', '2026-05-01', 'op'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO emails (date_iso, source_file, folder, sender, recipients, subject)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "2026-05-18T10:00:00-04:00",
            _CONTACTO_SENT,
            "[Gmail]/Enviados",
            "contacto@origenlab.cl",
            "Known Client <known@cliente.cl>",
            "Cotización equipo",
        ),
    )
    conn.commit()
    conn.close()

    app = create_app()
    from origenlab_api.settings import get_settings

    app.dependency_overrides[get_settings] = lambda: Settings(
        sqlite_path=db,
        active_current=active,
    )
    return TestClient(app)


def test_contact_detail_known_contact(tmp_path: Path) -> None:
    db = tmp_path / "intel.sqlite"
    client = _client(tmp_path, db)
    r = client.get("/contacts/known@cliente.cl")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["meta"]["data_source"] == "sqlite"
    assert data["meta"]["read_only"] is True
    assert data["contact"]["normalized_email"] == "known@cliente.cl"
    assert data["contact"]["name"] == "Known Client"
    assert data["contact"]["message_count"] == 12
    assert data["outreach"]["state"] == "contacted"
    assert data["outreach"]["do_not_repeat"] is True
    assert data["sent_history"]["sent_count"] == 1
    assert data["sent_history"]["latest_subject"] == "Cotización equipo"
    assert "body" not in json.dumps(data)


def test_contact_detail_unknown_valid_email_returns_200(tmp_path: Path) -> None:
    db = tmp_path / "intel.sqlite"
    client = _client(tmp_path, db)
    r = client.get("/contacts/unknown@elsewhere.cl")
    assert r.status_code == 200
    data = r.json()
    assert data["contact"]["normalized_email"] == "unknown@elsewhere.cl"
    assert data["contact"]["message_count"] == 0
    assert data["outreach"]["state"] is None
    assert any("contact_master" in w for w in data["warnings"])


def test_contact_detail_invalid_email_422(tmp_path: Path) -> None:
    db = tmp_path / "intel.sqlite"
    client = _client(tmp_path, db)
    r = client.get("/contacts/not-an-email")
    assert r.status_code == 422


def test_contact_detail_suppression_flags(tmp_path: Path) -> None:
    db = tmp_path / "intel.sqlite"
    client = _client(tmp_path, db)
    blocked = client.get("/contacts/blocked@x.cl").json()
    assert blocked["outreach"]["suppressed_email"] is True

    domain_blocked = client.get("/contacts/user@blocked-domain.cl").json()
    assert domain_blocked["outreach"]["suppressed_domain"] is True


def test_contact_detail_missing_sqlite_reduced_mode(tmp_path: Path) -> None:
    active = tmp_path / "current"
    active.mkdir()
    (active / "manifest.json").write_text("{}", encoding="utf-8")
    app = create_app()
    from origenlab_api.settings import get_settings

    app.dependency_overrides[get_settings] = lambda: Settings(
        sqlite_path=tmp_path / "missing.sqlite",
        active_current=active,
    )
    client = TestClient(app)
    data = client.get("/contacts/anyone@cliente.cl").json()
    assert data["meta"]["reduced_mode"] is True
    assert data["contact"]["normalized_email"] == "anyone@cliente.cl"


def test_load_manifest_returns_empty_dict_when_file_missing(tmp_path: Path) -> None:
    from origenlab_api.repositories.contact import _load_manifest

    active = tmp_path / "current"
    active.mkdir()
    assert _load_manifest(active) == {}


def test_load_manifest_returns_empty_dict_on_malformed_json(tmp_path: Path) -> None:
    from origenlab_api.repositories.contact import _load_manifest

    active = tmp_path / "current"
    active.mkdir()
    (active / "manifest.json").write_text("{not valid json", encoding="utf-8")
    assert _load_manifest(active) == {}


def test_load_manifest_returns_parsed_dict_on_valid_json(tmp_path: Path) -> None:
    from origenlab_api.repositories.contact import _load_manifest

    active = tmp_path / "current"
    active.mkdir()
    (active / "manifest.json").write_text(
        json.dumps({"canonical_files": ["a.csv"]}), encoding="utf-8"
    )
    assert _load_manifest(active) == {"canonical_files": ["a.csv"]}
