"""ARCH-2A safety tests for the PR3 SQLite -> Postgres projection."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import origenlab_email_pipeline.commercial_opportunity_postgres_mirror as mirror


def _valid_meta() -> dict[str, str]:
    return {
        "schema_version": mirror.SCHEMA_VERSION,
        "build_contract": mirror.BUILD_CONTRACT,
        "built_at": "2026-08-22T00:00:00+00:00",
        "run_context": mirror.RUN_CONTEXT_PRODUCTION_APPLY,
        "identity_fingerprint": "identity-fp",
        "identity_fingerprint_algorithm_version": (
            mirror.REQUIRED_IDENTITY_FINGERPRINT_ALGORITHM_VERSION
        ),
        "identity_fingerprint_match_status": "matched",
        "opportunity_source_fingerprint": "source-fp",
        "opportunity_source_fingerprint_algorithm_version": (
            mirror.OPPORTUNITY_SOURCE_FINGERPRINT_ALGORITHM_VERSION
        ),
        "metrics_json": "{}",
    }


def _valid_payload() -> dict[str, Any]:
    return {
        "meta": _valid_meta(),
        "freshness": {
            "identity_fingerprint": "identity-fp",
            "opportunity_source_fingerprint": "source-fp",
        },
        "opportunities": [
            {
                "opportunity_id": "o_1",
                "record_kind": "explicit_opportunity",
                "account_id": "a_1",
                "primary_contact_id": "c_1",
                "contact_display_email": "buyer@example.com",
                "account_display_domain": "example.com",
                "source_kind": "commercial_deal",
                "source_key": "deal-1",
                "deal_key": "deal-1",
                "canonical_stage": "quote_sent",
                "source_stage": "quoted",
                "stage_reason_code": "deal_status",
                "stage_confidence": "operator_confirmed",
                "stage_is_current": True,
                "stage_is_terminal": False,
                "stage_evidence_at": "2026-08-22T00:00:00+00:00",
                "stage_evidence_id": "e_1",
                "first_activity_at": "2026-08-20T00:00:00+00:00",
                "last_activity_at": "2026-08-22T00:00:00+00:00",
                "identity_link_status": "linked",
                "review_status": "ok",
            }
        ],
        "events": [
            {
                "event_id": "evt_1",
                "opportunity_id": "o_1",
            }
        ],
        "evidence": [
            {
                "evidence_id": "e_1",
                "opportunity_id": "o_1",
            }
        ],
        "conflicts": [
            {
                "conflict_id": "x_1",
                "opportunity_id": "o_1",
            }
        ],
    }


def test_validate_build_meta_accepts_current_production_contract() -> None:
    mirror.validate_build_meta(_valid_meta())


@pytest.mark.parametrize(
    ("key", "bad_value"),
    [
        ("schema_version", "commercial_opportunity_v0"),
        ("build_contract", "opportunity_stage_read_model_v1"),
        ("run_context", "production_dry_run"),
        ("identity_fingerprint_match_status", "mismatched"),
        ("identity_fingerprint_algorithm_version", "identity_fp_v1"),
        (
            "opportunity_source_fingerprint_algorithm_version",
            "opportunity_source_fp_v0",
        ),
    ],
)
def test_validate_build_meta_rejects_incompatible_snapshot(
    key: str,
    bad_value: str,
) -> None:
    meta = _valid_meta()
    meta[key] = bad_value

    with pytest.raises(mirror.CommercialOpportunityMirrorSafetyError):
        mirror.validate_build_meta(meta)


def test_validate_build_meta_rejects_missing_required_metadata() -> None:
    meta = _valid_meta()
    del meta["identity_fingerprint"]

    with pytest.raises(
        mirror.CommercialOpportunityMirrorSafetyError,
        match="metadata missing",
    ):
        mirror.validate_build_meta(meta)


def test_validate_source_freshness_accepts_matching_live_fingerprints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mirror, "load_source_identity_rows", lambda conn: ["identity"])
    monkeypatch.setattr(mirror, "resolve_identity", lambda rows: object())
    monkeypatch.setattr(
        mirror,
        "identity_resolution_fingerprint",
        lambda resolution: "identity-fp",
    )
    monkeypatch.setattr(
        mirror,
        "load_opportunity_sources",
        lambda conn: {
            "deals": [],
            "events": [],
            "documents": [],
            "payments": [],
            "signals": [],
            "contact_master": [],
        },
    )
    monkeypatch.setattr(
        mirror,
        "opportunity_source_fingerprint",
        lambda **kwargs: "source-fp",
    )

    result = mirror.validate_source_freshness(object(), _valid_meta())  # type: ignore[arg-type]

    assert result == {
        "identity_fingerprint": "identity-fp",
        "opportunity_source_fingerprint": "source-fp",
    }


def test_validate_source_freshness_rejects_stale_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mirror, "load_source_identity_rows", lambda conn: [])
    monkeypatch.setattr(mirror, "resolve_identity", lambda rows: object())
    monkeypatch.setattr(
        mirror,
        "identity_resolution_fingerprint",
        lambda resolution: "changed-identity-fp",
    )
    monkeypatch.setattr(
        mirror,
        "load_opportunity_sources",
        lambda conn: {
            "deals": [],
            "events": [],
            "documents": [],
            "payments": [],
            "signals": [],
            "contact_master": [],
        },
    )
    monkeypatch.setattr(
        mirror,
        "opportunity_source_fingerprint",
        lambda **kwargs: "source-fp",
    )

    with pytest.raises(
        mirror.CommercialOpportunityMirrorSafetyError,
        match="live identity fingerprint",
    ):
        mirror.validate_source_freshness(object(), _valid_meta())  # type: ignore[arg-type]


def test_validate_source_freshness_rejects_stale_opportunity_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mirror, "load_source_identity_rows", lambda conn: [])
    monkeypatch.setattr(mirror, "resolve_identity", lambda rows: object())
    monkeypatch.setattr(
        mirror,
        "identity_resolution_fingerprint",
        lambda resolution: "identity-fp",
    )
    monkeypatch.setattr(
        mirror,
        "load_opportunity_sources",
        lambda conn: {
            "deals": [],
            "events": [],
            "documents": [],
            "payments": [],
            "signals": [],
            "contact_master": [],
        },
    )
    monkeypatch.setattr(
        mirror,
        "opportunity_source_fingerprint",
        lambda **kwargs: "changed-source-fp",
    )

    with pytest.raises(
        mirror.CommercialOpportunityMirrorSafetyError,
        match="live opportunity source fingerprint",
    ):
        mirror.validate_source_freshness(object(), _valid_meta())  # type: ignore[arg-type]


def test_validate_payload_integrity_accepts_valid_graph() -> None:
    mirror.validate_payload_integrity(_valid_payload())


def test_validate_payload_integrity_rejects_duplicate_opportunity_ids() -> None:
    payload = _valid_payload()
    payload["opportunities"].append(dict(payload["opportunities"][0]))

    with pytest.raises(
        mirror.CommercialOpportunityMirrorSafetyError,
        match="Duplicate deterministic IDs",
    ):
        mirror.validate_payload_integrity(payload)


def test_validate_payload_integrity_rejects_orphan_event() -> None:
    payload = _valid_payload()
    payload["events"][0]["opportunity_id"] = "missing"

    with pytest.raises(
        mirror.CommercialOpportunityMirrorSafetyError,
        match="orphan event",
    ):
        mirror.validate_payload_integrity(payload)


def test_validate_payload_integrity_rejects_orphan_evidence() -> None:
    payload = _valid_payload()
    payload["evidence"][0]["opportunity_id"] = "missing"

    with pytest.raises(
        mirror.CommercialOpportunityMirrorSafetyError,
        match="orphan evidence",
    ):
        mirror.validate_payload_integrity(payload)


def test_validate_payload_integrity_rejects_orphan_non_null_conflict() -> None:
    payload = _valid_payload()
    payload["conflicts"][0]["opportunity_id"] = "missing"

    with pytest.raises(
        mirror.CommercialOpportunityMirrorSafetyError,
        match="orphan conflict",
    ):
        mirror.validate_payload_integrity(payload)


def test_validate_payload_integrity_allows_null_conflict_opportunity() -> None:
    payload = _valid_payload()
    payload["conflicts"][0]["opportunity_id"] = None

    mirror.validate_payload_integrity(payload)


def test_validate_payload_integrity_rejects_missing_stage_evidence() -> None:
    payload = _valid_payload()
    payload["opportunities"][0]["stage_evidence_id"] = "missing-evidence"

    with pytest.raises(
        mirror.CommercialOpportunityMirrorSafetyError,
        match="stage_evidence_id",
    ):
        mirror.validate_payload_integrity(payload)


def test_json_value_rejects_malformed_json() -> None:
    with pytest.raises(
        mirror.CommercialOpportunityMirrorSafetyError,
        match="Malformed JSON",
    ):
        mirror._json_value("{not-json", field_name="test.detail_json")


def test_safe_replace_allows_first_projection() -> None:
    mirror.assert_safe_replace(
        {"opportunity": 9577},
        {"opportunity": 0},
    )


def test_safe_replace_allows_normal_refresh() -> None:
    mirror.assert_safe_replace(
        {"opportunity": 9580},
        {"opportunity": 9577},
    )


def test_safe_replace_rejects_nonempty_postgres_replaced_by_zero() -> None:
    with pytest.raises(
        mirror.CommercialOpportunityMirrorSafetyError,
        match="source opportunities=0",
    ):
        mirror.assert_safe_replace(
            {"opportunity": 0},
            {"opportunity": 9577},
        )


def test_safe_replace_rejects_catastrophic_collapse() -> None:
    with pytest.raises(
        mirror.CommercialOpportunityMirrorSafetyError,
        match="ratio=",
    ):
        mirror.assert_safe_replace(
            {"opportunity": 100},
            {"opportunity": 9577},
        )


class _FakeCursor:
    def __init__(
        self,
        executed: list[str],
        existing_counts: dict[str, int],
    ) -> None:
        self.executed = executed
        self.existing_counts = existing_counts
        self.last_sql = ""

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(
        self,
        sql: str,
        params: object = None,
    ) -> None:
        del params
        self.last_sql = " ".join(str(sql).split())
        self.executed.append(self.last_sql)

    def fetchone(self) -> tuple[int]:
        if "information_schema.tables" in self.last_sql:
            return (1,)

        for table, count in self.existing_counts.items():
            if f"FROM commercial.{table}" in self.last_sql:
                return (count,)

        return (1,)


class _FakeConnection:
    def __init__(
        self,
        executed: list[str],
        existing_counts: dict[str, int],
    ) -> None:
        self.executed = executed
        self.existing_counts = existing_counts
        self.committed = False
        self.rolled_back = False

    def __enter__(self) -> "_FakeConnection":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self.executed, self.existing_counts)

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


class _FakePsycopg:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection

    def connect(
        self,
        pg_url: str,
        *,
        autocommit: bool,
    ) -> _FakeConnection:
        del pg_url, autocommit
        return self.connection


def _collapse_payload() -> dict[str, Any]:
    payload = _valid_payload()

    payload["opportunities"] = [
        {
            **payload["opportunities"][0],
            "opportunity_id": f"o_{index}",
            "stage_evidence_id": None,
        }
        for index in range(100)
    ]

    payload["events"] = []
    payload["evidence"] = []
    payload["conflicts"] = []

    return payload


def test_collapse_gate_fires_before_any_postgres_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed: list[str] = []
    connection = _FakeConnection(
        executed,
        {
            "opportunity": 9577,
            "opportunity_event": 7,
            "opportunity_evidence": 14,
            "opportunity_conflict": 2703,
        },
    )

    monkeypatch.setattr(
        mirror,
        "load_commercial_opportunity_mirror_payload",
        lambda sqlite_path: _collapse_payload(),
    )
    monkeypatch.setattr(mirror, "psycopg", _FakePsycopg(connection))
    monkeypatch.setattr(mirror, "Json", lambda value: value)

    with pytest.raises(
        mirror.CommercialOpportunityMirrorSafetyError,
        match="ratio=",
    ):
        mirror.sync_commercial_opportunity_postgres_mirror(
            "postgresql://scratch/test",
            Path("/tmp/fake.sqlite"),
            dry_run=False,
        )

    delete_statements = [
        sql for sql in executed if sql.upper().startswith("DELETE FROM COMMERCIAL.")
    ]

    assert delete_statements == []
    assert connection.committed is False


def test_migration_keeps_sqlite_local_deal_id_out_of_projection() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/20260822_0031_commercial_opportunity_mirror.py"
    )
    text = migration.read_text(encoding="utf-8")

    assert "commercial_deal_id" not in text
    assert "deal_key TEXT" in text
    assert "ON DELETE CASCADE" in text
    assert "ON DELETE SET NULL" in text


def test_migration_exposes_minimal_api_views() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/20260822_0031_commercial_opportunity_mirror.py"
    )
    text = migration.read_text(encoding="utf-8")

    assert "CREATE VIEW api.v_commercial_opportunity AS" in text
    assert "CREATE VIEW api.v_commercial_opportunity_event AS" in text
    assert "CREATE VIEW api.v_commercial_opportunity_evidence AS" in text
    assert "CREATE VIEW api.v_commercial_opportunity_conflict AS" in text


def test_dashboard_commercial_opportunity_flag_is_opt_in() -> None:
    from origenlab_email_pipeline.dashboard_postgres_sync import build_parser

    parser = build_parser()

    default = parser.parse_args([])
    assert default.include_commercial_opportunities is False

    enabled = parser.parse_args(["--include-commercial-opportunities"])
    assert enabled.include_commercial_opportunities is True


def test_dashboard_expected_alembic_head_includes_arch2a() -> None:
    from origenlab_email_pipeline.dashboard_postgres_sync import (
        EXPECTED_ALEMBIC_HEAD,
    )

    assert EXPECTED_ALEMBIC_HEAD == "20260901_0042"


def test_safe_replace_rejects_zero_source_on_first_projection() -> None:
    with pytest.raises(
        mirror.CommercialOpportunityMirrorSafetyError,
        match="source opportunities=0",
    ):
        mirror.assert_safe_replace(
            {"opportunity": 0},
            {"opportunity": 0},
        )
