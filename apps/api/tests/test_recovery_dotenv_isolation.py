"""Recovery settings must ignore project dotenv; process env remains fail-closed."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from origenlab_api.backends.factory import validate_api_settings
from origenlab_api.settings import (
    Settings,
    build_settings,
    get_settings,
    recovery_mode_requested_from_environ,
)
from origenlab_api.sqlite_ro import existing_companions, fingerprint_path

REPO = Path(__file__).resolve().parents[1]
FAKE_PG = "postgresql://fake-user:fake-secret-pg@127.0.0.1:65432/fake_recovery_db"
FAKE_GMAIL = "/tmp/fake-gmail-oauth-client.json"
FAKE_TOKEN = "fake-api-auth-token-recovery-drill"
FAKE_OUTBOUND = "fake-outbound-credential"


def _build_candidate(path: Path, *, rows: int = 5) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE emails (id INTEGER PRIMARY KEY, date_iso TEXT, subject TEXT, "
        "source_file TEXT, folder TEXT, sender TEXT)"
    )
    conn.execute(
        "CREATE TABLE attachments (id INTEGER PRIMARY KEY, email_id INTEGER, filename TEXT)"
    )
    conn.execute(
        "CREATE TABLE email_mart_features (email_id INTEGER PRIMARY KEY, score REAL)"
    )
    for i in range(1, rows + 1):
        conn.execute(
            "INSERT INTO emails (id, date_iso, subject, source_file, folder, sender) "
            "VALUES (?,?,?,?,?,?)",
            (i, "2026-07-01T10:00:00Z", f"s{i}", "gmail:x", "Sent", "a@b.cl"),
        )
        conn.execute(
            "INSERT INTO email_mart_features (email_id, score) VALUES (?,?)",
            (i, 1.0),
        )
    conn.commit()
    conn.close()


def _write_manifest(path: Path, candidate: Path) -> None:
    fp = fingerprint_path(candidate)
    path.write_text(
        json.dumps(
            {
                "completed": True,
                "method": "VACUUM INTO",
                "destination_basename": candidate.name,
                "destination_size_bytes": fp.size_bytes,
                "verification": {
                    "quick_check": "ok",
                    "foreign_key_violation_count": 0,
                    "schema_fingerprint": "abc",
                    "critical_table_counts": {
                        "emails": 5,
                        "attachments": 0,
                        "email_mart_features": 5,
                        "conversations": None,
                    },
                },
            }
        ),
        encoding="utf-8",
    )


def _write_poison_dotenv(directory: Path) -> Path:
    env_path = directory / ".env"
    env_path.write_text(
        "\n".join(
            [
                f"ORIGENLAB_POSTGRES_URL={FAKE_PG}",
                f"ORIGENLAB_GMAIL_OAUTH_CLIENT_JSON={FAKE_GMAIL}",
                f"ORIGENLAB_API_AUTH_TOKEN={FAKE_TOKEN}",
                f"ORIGENLAB_OUTBOUND_CREDENTIAL={FAKE_OUTBOUND}",
                "ORIGENLAB_API_BACKEND=postgres",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return env_path


def _assert_no_secret_leak(text: str) -> None:
    for needle in (FAKE_PG, "fake-secret-pg", FAKE_GMAIL, FAKE_TOKEN, FAKE_OUTBOUND):
        assert needle not in text


def test_recovery_ignores_cwd_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    poison = tmp_path / "workdir"
    poison.mkdir()
    _write_poison_dotenv(poison)
    monkeypatch.chdir(poison)

    for key in (
        "ORIGENLAB_POSTGRES_URL",
        "ORIGENLAB_GMAIL_OAUTH_CLIENT_JSON",
        "ORIGENLAB_API_AUTH_TOKEN",
        "ORIGENLAB_OUTBOUND_CREDENTIAL",
        "ORIGENLAB_API_BACKEND",
        "ORIGENLAB_SQLITE_IMMUTABLE_RO",
        "ORIGENLAB_SQLITE_CONFIRM_OFFLINE_COPY",
        "ORIGENLAB_SQLITE_PATH",
        "ORIGENLAB_SQLITE_COMPACTION_MANIFEST",
        "ORIGENLAB_DISABLE_DOTENV",
    ):
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("ORIGENLAB_SQLITE_IMMUTABLE_RO", "1")
    monkeypatch.setenv("ORIGENLAB_SQLITE_CONFIRM_OFFLINE_COPY", "1")
    monkeypatch.setenv("ORIGENLAB_API_BACKEND", "sqlite")
    monkeypatch.setenv("ORIGENLAB_SQLITE_PATH", str(tmp_path / "candidate.sqlite"))

    get_settings.cache_clear()
    try:
        assert recovery_mode_requested_from_environ() is True
        settings = build_settings()
        assert settings.dotenv_disabled is True
        assert settings.postgres_configured() is False
        assert settings.postgres_url in (None, "")
        assert settings.api_auth_token in (None, "")
        assert settings.resolved_api_backend() == "sqlite"
        blob = json.dumps(settings.model_dump(mode="json"), default=str)
        _assert_no_secret_leak(blob)
        _assert_no_secret_leak(repr(settings))
    finally:
        get_settings.cache_clear()


def test_normal_settings_still_load_cwd_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    poison = tmp_path / "workdir"
    poison.mkdir()
    _write_poison_dotenv(poison)
    monkeypatch.chdir(poison)

    for key in (
        "ORIGENLAB_POSTGRES_URL",
        "ORIGENLAB_API_BACKEND",
        "ORIGENLAB_API_AUTH_TOKEN",
        "ORIGENLAB_SQLITE_IMMUTABLE_RO",
        "ORIGENLAB_SQLITE_CONFIRM_OFFLINE_COPY",
        "ORIGENLAB_DISABLE_DOTENV",
    ):
        monkeypatch.delenv(key, raising=False)

    get_settings.cache_clear()
    try:
        settings = build_settings()
        assert settings.dotenv_disabled is False
        assert settings.postgres_configured() is True
        assert settings.resolved_api_backend() == "postgres"
        assert settings.api_auth_token == FAKE_TOKEN
    finally:
        get_settings.cache_clear()


def test_explicit_process_postgres_still_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "candidate.sqlite"
    man = tmp_path / "m.json"
    _build_candidate(db)
    _write_manifest(man, db)

    monkeypatch.setenv("ORIGENLAB_SQLITE_IMMUTABLE_RO", "1")
    monkeypatch.setenv("ORIGENLAB_SQLITE_CONFIRM_OFFLINE_COPY", "1")
    monkeypatch.setenv("ORIGENLAB_API_BACKEND", "sqlite")
    monkeypatch.setenv("ORIGENLAB_SQLITE_PATH", str(db))
    monkeypatch.setenv("ORIGENLAB_SQLITE_COMPACTION_MANIFEST", str(man))
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", FAKE_PG)

    get_settings.cache_clear()
    try:
        settings = build_settings()
        assert settings.dotenv_disabled is True
        assert settings.postgres_configured() is True
        with pytest.raises(ValueError, match="ORIGENLAB_POSTGRES_URL") as excinfo:
            validate_api_settings(settings)
        _assert_no_secret_leak(str(excinfo.value))
    finally:
        get_settings.cache_clear()


def test_missing_recovery_flags_still_fail(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        sqlite_path=tmp_path / "x.sqlite",
        sqlite_immutable_ro=True,
        sqlite_confirm_offline_copy=False,
        api_backend="sqlite",
        postgres_url=None,
    )
    settings._dotenv_disabled = True
    with pytest.raises(ValueError, match="recovery admission failed"):
        validate_api_settings(settings)


def test_subprocess_recovery_startup_ignores_dotenv(tmp_path: Path) -> None:
    """Startup-level check: create_app in a poisoned cwd with recovery env."""
    work = tmp_path / "recovery_cwd"
    work.mkdir()
    _write_poison_dotenv(work)
    db = work / "candidate.sqlite"
    man = work / "candidate.sqlite.compaction.manifest.json"
    active = work / "active_current"
    active.mkdir()
    (active / "manifest.json").write_text("{}", encoding="utf-8")
    _build_candidate(db)
    _write_manifest(man, db)

    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("ORIGENLAB_")
        and key
        not in {
            "ALEMBIC_DATABASE_URL",
            "OPENAI_API_KEY",
            "CF_ACCESS_CLIENT_ID",
            "CF_ACCESS_CLIENT_SECRET",
            "CF_ACCESS_CLIENT_ID_PROXY",
            "CF_ACCESS_CLIENT_SECRET_PROXY",
        }
    }
    env.update(
        {
            "HOME": str(tmp_path / "home"),
            "ORIGENLAB_API_BACKEND": "sqlite",
            "ORIGENLAB_SQLITE_PATH": str(db),
            "ORIGENLAB_SQLITE_IMMUTABLE_RO": "1",
            "ORIGENLAB_SQLITE_CONFIRM_OFFLINE_COPY": "1",
            "ORIGENLAB_SQLITE_COMPACTION_MANIFEST": str(man),
            "ORIGENLAB_DISABLE_DOTENV": "1",
            "ORIGENLAB_DATA_ROOT": str(tmp_path / "home" / "data" / "origenlab-email"),
            "ORIGENLAB_ACTIVE_CURRENT": str(active),
            "ORIGENLAB_POSTGRES_URL": "",
            "ORIGENLAB_API_AUTH_TOKEN": "",
            "PYTHONPATH": str(REPO / "src"),
        }
    )
    (tmp_path / "home" / "data" / "origenlab-email" / "sqlite").mkdir(parents=True)
    prod_default = (
        tmp_path / "home" / "data" / "origenlab-email" / "sqlite" / "emails.sqlite"
    )
    _build_candidate(prod_default)

    code = r"""
import json
from origenlab_api.settings import get_settings
get_settings.cache_clear()
from origenlab_api.main import create_app
from fastapi.testclient import TestClient
app = create_app()
client = TestClient(app)
health = client.get("/health").json()
assert health["ok"] is True
assert health["recovery_mode"] is True
assert health["dotenv_disabled"] is True
assert health["postgres_configured"] is False
assert health["sqlite_immutable"] is True
assert health["sqlite_query_only"] is True
blob = json.dumps(health)
assert "fake-secret-pg" not in blob
assert "fake-api-auth-token" not in blob
assert "/home/" not in blob
status = client.get("/operator/status")
assert status.status_code == 200
body = status.json()
assert "fake-secret-pg" not in json.dumps(body)
print(json.dumps({"ok": True, "dotenv_disabled": health["dotenv_disabled"]}))
"""
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(work),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    combined = proc.stdout + proc.stderr
    _assert_no_secret_leak(combined)
    assert proc.returncode == 0, combined
    assert existing_companions(db) == []
    assert '"dotenv_disabled": true' in proc.stdout


def test_email_pipeline_load_settings_respects_recovery_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from origenlab_email_pipeline import config as ep_config

    poison = tmp_path / "ep"
    poison.mkdir()
    env_file = poison / ".env"
    env_file.write_text(
        "\n".join(
            [
                f"ORIGENLAB_POSTGRES_URL={FAKE_PG}",
                f"ORIGENLAB_GMAIL_OAUTH_CLIENT_JSON={FAKE_GMAIL}",
                f"ORIGENLAB_GMAIL_WORKSPACE_USER=fake@example.cl",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ep_config, "_repo_root", lambda: poison)

    for key in (
        "ORIGENLAB_POSTGRES_URL",
        "ORIGENLAB_GMAIL_OAUTH_CLIENT_JSON",
        "ORIGENLAB_GMAIL_WORKSPACE_USER",
        "ORIGENLAB_SQLITE_IMMUTABLE_RO",
        "ORIGENLAB_SQLITE_CONFIRM_OFFLINE_COPY",
        "ORIGENLAB_DISABLE_DOTENV",
    ):
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("ORIGENLAB_SQLITE_IMMUTABLE_RO", "1")
    monkeypatch.setenv("ORIGENLAB_SQLITE_CONFIRM_OFFLINE_COPY", "1")
    settings = ep_config.load_settings()
    # Recovery must not load_dotenv into os.environ or Settings from repo .env.
    assert os.environ.get("ORIGENLAB_POSTGRES_URL") in (None, "")
    assert os.environ.get("ORIGENLAB_GMAIL_OAUTH_CLIENT_JSON") in (None, "")
    assert settings.gmail_oauth_client_json in (None, "")
    assert settings.gmail_workspace_user in (None, "")
    _assert_no_secret_leak(repr(settings))


def test_disable_dotenv_alone_does_not_grant_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ORIGENLAB_SQLITE_IMMUTABLE_RO", raising=False)
    monkeypatch.delenv("ORIGENLAB_SQLITE_CONFIRM_OFFLINE_COPY", raising=False)
    monkeypatch.setenv("ORIGENLAB_DISABLE_DOTENV", "1")
    monkeypatch.setenv("ORIGENLAB_API_BACKEND", "sqlite")
    get_settings.cache_clear()
    try:
        settings = build_settings()
        assert settings.dotenv_disabled is True
        assert recovery_mode_requested_from_environ() is False
        assert validate_api_settings(settings) is None
    finally:
        get_settings.cache_clear()


def test_empty_optional_env_vars_are_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "candidate.sqlite"
    man = tmp_path / "m.json"
    _build_candidate(db)
    _write_manifest(man, db)
    home = tmp_path / "home"
    (home / "data" / "origenlab-email" / "sqlite").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ORIGENLAB_DATA_ROOT", str(home / "data" / "origenlab-email"))
    monkeypatch.setenv("ORIGENLAB_SQLITE_IMMUTABLE_RO", "1")
    monkeypatch.setenv("ORIGENLAB_SQLITE_CONFIRM_OFFLINE_COPY", "1")
    monkeypatch.setenv("ORIGENLAB_API_BACKEND", "sqlite")
    monkeypatch.setenv("ORIGENLAB_SQLITE_PATH", str(db))
    monkeypatch.setenv("ORIGENLAB_SQLITE_COMPACTION_MANIFEST", str(man))
    for key in (
        "ORIGENLAB_POSTGRES_URL",
        "ORIGENLAB_GMAIL_OAUTH_CLIENT_JSON",
        "ORIGENLAB_API_AUTH_TOKEN",
        "OPENAI_API_KEY",
    ):
        monkeypatch.setenv(key, "")

    get_settings.cache_clear()
    try:
        settings = build_settings()
        assert settings.dotenv_disabled is True
        validate_api_settings(settings)
    finally:
        get_settings.cache_clear()


def test_production_exclusion_does_not_mutate_environ(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from origenlab_api.sqlite_ro import (
        assert_not_production_sqlite,
        resolve_canonical_production_sqlite_path,
        sqlite_paths_equivalent,
    )

    home = tmp_path / "home"
    prod_dir = home / "data" / "origenlab-email" / "sqlite"
    prod_dir.mkdir(parents=True)
    prod = prod_dir / "emails.sqlite"
    cand = tmp_path / "candidate.sqlite"
    _build_candidate(prod)
    _build_candidate(cand)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ORIGENLAB_DATA_ROOT", str(home / "data" / "origenlab-email"))
    monkeypatch.setenv("ORIGENLAB_SQLITE_PATH", str(cand))
    monkeypatch.setenv("ORIGENLAB_SQLITE_IMMUTABLE_RO", "1")
    monkeypatch.setenv("ORIGENLAB_SQLITE_CONFIRM_OFFLINE_COPY", "1")

    settings = Settings(
        _env_file=None,
        sqlite_path=cand,
        sqlite_immutable_ro=True,
        sqlite_confirm_offline_copy=True,
        sqlite_compaction_manifest=tmp_path / "m.json",
        api_backend="sqlite",
        postgres_url=None,
    )
    settings._dotenv_disabled = True

    before = dict(os.environ)
    assert_not_production_sqlite(cand, settings=settings)
    after = dict(os.environ)
    assert before == after
    assert "ORIGENLAB_SQLITE_PATH" in after
    assert after["ORIGENLAB_SQLITE_PATH"] == str(cand)

    production = resolve_canonical_production_sqlite_path(home=home)
    assert sqlite_paths_equivalent(production, prod)
    assert not sqlite_paths_equivalent(cand, production)

    def _worker() -> dict[str, str]:
        snap = dict(os.environ)
        assert_not_production_sqlite(cand, settings=settings)
        return dict(os.environ)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: _worker(), range(16)))
    assert all(r == before for r in results)
    assert dict(os.environ) == before


def test_candidate_matching_production_aliases_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from origenlab_api.sqlite_ro import (
        SqliteAdmissionError,
        assert_not_production_sqlite,
    )

    home = tmp_path / "home"
    prod_dir = home / "data" / "origenlab-email" / "sqlite"
    prod_dir.mkdir(parents=True)
    prod = prod_dir / "emails.sqlite"
    _build_candidate(prod)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ORIGENLAB_DATA_ROOT", str(home / "data" / "origenlab-email"))
    monkeypatch.setenv("ORIGENLAB_SQLITE_IMMUTABLE_RO", "1")
    monkeypatch.setenv("ORIGENLAB_SQLITE_CONFIRM_OFFLINE_COPY", "1")

    settings = Settings(
        _env_file=None,
        sqlite_path=prod,
        sqlite_immutable_ro=True,
        sqlite_confirm_offline_copy=True,
        api_backend="sqlite",
        postgres_url=None,
    )
    settings._dotenv_disabled = True

    before = dict(os.environ)
    with pytest.raises(SqliteAdmissionError, match="production"):
        assert_not_production_sqlite(prod, settings=settings)
    assert dict(os.environ) == before

    link = tmp_path / "alias.sqlite"
    link.symlink_to(prod)
    with pytest.raises(SqliteAdmissionError, match="production"):
        assert_not_production_sqlite(link, settings=settings)
    assert dict(os.environ) == before

    hard = tmp_path / "hardlink.sqlite"
    os.link(prod, hard)
    with pytest.raises(SqliteAdmissionError, match="production"):
        assert_not_production_sqlite(hard, settings=settings)
    assert dict(os.environ) == before


def test_missing_production_path_still_fail_closed_on_path_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from origenlab_api.sqlite_ro import (
        SqliteAdmissionError,
        assert_not_production_sqlite,
        resolve_canonical_production_sqlite_path,
    )

    home = tmp_path / "home"
    prod = home / "data" / "origenlab-email" / "sqlite" / "emails.sqlite"
    # Directory exists but file does not.
    prod.parent.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ORIGENLAB_DATA_ROOT", str(home / "data" / "origenlab-email"))
    monkeypatch.setenv("ORIGENLAB_SQLITE_IMMUTABLE_RO", "1")
    monkeypatch.setenv("ORIGENLAB_SQLITE_CONFIRM_OFFLINE_COPY", "1")

    settings = Settings(
        _env_file=None,
        sqlite_path=prod,
        sqlite_immutable_ro=True,
        sqlite_confirm_offline_copy=True,
        api_backend="sqlite",
        postgres_url=None,
    )
    settings._dotenv_disabled = True

    before = dict(os.environ)
    assert resolve_canonical_production_sqlite_path(home=home) == prod.resolve()
    with pytest.raises(SqliteAdmissionError, match="production"):
        assert_not_production_sqlite(prod, settings=settings)
    assert dict(os.environ) == before


def test_settings_cache_does_not_cross_contaminate_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    poison = tmp_path / "workdir"
    poison.mkdir()
    _write_poison_dotenv(poison)
    monkeypatch.chdir(poison)

    for key in (
        "ORIGENLAB_POSTGRES_URL",
        "ORIGENLAB_API_BACKEND",
        "ORIGENLAB_API_AUTH_TOKEN",
        "ORIGENLAB_SQLITE_IMMUTABLE_RO",
        "ORIGENLAB_SQLITE_CONFIRM_OFFLINE_COPY",
        "ORIGENLAB_DISABLE_DOTENV",
    ):
        monkeypatch.delenv(key, raising=False)

    get_settings.cache_clear()
    normal = get_settings()
    assert normal.dotenv_disabled is False
    assert normal.postgres_configured() is True

    monkeypatch.setenv("ORIGENLAB_SQLITE_IMMUTABLE_RO", "1")
    monkeypatch.setenv("ORIGENLAB_SQLITE_CONFIRM_OFFLINE_COPY", "1")
    monkeypatch.setenv("ORIGENLAB_API_BACKEND", "sqlite")
    recovery = get_settings()
    assert recovery.dotenv_disabled is True
    assert recovery.postgres_configured() is False
    assert normal.postgres_configured() is True

    monkeypatch.delenv("ORIGENLAB_SQLITE_IMMUTABLE_RO", raising=False)
    monkeypatch.delenv("ORIGENLAB_SQLITE_CONFIRM_OFFLINE_COPY", raising=False)
    again = get_settings()
    assert again.dotenv_disabled is False
    assert again.postgres_configured() is True
    get_settings.cache_clear()


def test_explicit_gmail_process_env_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "candidate.sqlite"
    man = tmp_path / "m.json"
    _build_candidate(db)
    _write_manifest(man, db)
    home = tmp_path / "home"
    (home / "data" / "origenlab-email" / "sqlite").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ORIGENLAB_DATA_ROOT", str(home / "data" / "origenlab-email"))
    monkeypatch.setenv("ORIGENLAB_SQLITE_IMMUTABLE_RO", "1")
    monkeypatch.setenv("ORIGENLAB_SQLITE_CONFIRM_OFFLINE_COPY", "1")
    monkeypatch.setenv("ORIGENLAB_API_BACKEND", "sqlite")
    monkeypatch.setenv("ORIGENLAB_SQLITE_PATH", str(db))
    monkeypatch.setenv("ORIGENLAB_SQLITE_COMPACTION_MANIFEST", str(man))
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "")
    monkeypatch.setenv("ORIGENLAB_GMAIL_OAUTH_CLIENT_JSON", FAKE_GMAIL)

    get_settings.cache_clear()
    try:
        settings = build_settings()
        with pytest.raises(ValueError, match="ORIGENLAB_GMAIL_OAUTH_CLIENT_JSON") as exc:
            validate_api_settings(settings)
        _assert_no_secret_leak(str(exc.value))
    finally:
        get_settings.cache_clear()
