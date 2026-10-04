"""Marketing read route — ``v1_lane_campaigns`` field.

Pure in-process: mocks the repository so no database is needed.
Every value is invented; the repository is public.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER
from origenlab_api.v2.crm_workspace_routes import workspace_router
from origenlab_api.v2.identity import LocalDevIdentity, OperatorIdentity, OperatorLookup
from origenlab_api.v2.v1_lane_campaigns import (
    JSON_PATH,
    V1LaneCampaign,
    V1LaneValidationError,
    load_v1_lane_campaigns,
)

LOOPBACK = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"
HEADERS = {OPERATOR_EMAIL_HEADER: "op@example.test"}


def _operator(role: str = "admin") -> OperatorIdentity:
    return OperatorIdentity(
        operator_id="00000000-0000-4000-8000-000000000099",
        email_norm="op@example.test",
        display_name="Operador Ejemplo",
        role=role,
        status="active",
    )


class _Lookup(OperatorLookup):
    def __init__(self, op: OperatorIdentity) -> None:
        self._op = op

    def by_email(self, email_norm: str) -> OperatorIdentity:  # noqa: ANN001, ANN201
        return self._op


class _MarketingRepo:
    """Minimal mock: returns a bare marketing payload. No database needed."""

    def marketing(self) -> dict:
        return {
            "campaigns": [],
            "holds": {"all_campaigns": {"blocked": False, "block": None}, "legacy": []},
            "contact_controls": [],
            "replies_note": "sin respuestas",
            "storage": {"table": "outbound.campaign", "database": "origenlab_clean"},
        }


def _client(role: str = "admin") -> TestClient:
    app = FastAPI()
    app.include_router(workspace_router)
    app.state.crm_workspace = _MarketingRepo()
    app.state.v2_identity = LocalDevIdentity(LOOPBACK, _Lookup(_operator(role)))
    return TestClient(app)


# ---------------------------------------------------------------------------
# 1. The marketing route includes v1_lane_campaigns
# ---------------------------------------------------------------------------


def test_marketing_read_includes_v1_lane_campaigns_field() -> None:
    body = _client().get("/v2/workspace/marketing", headers=HEADERS).json()
    assert "v1_lane_campaigns" in body, "v1_lane_campaigns missing from /v2/workspace/marketing"


def test_marketing_v1_lane_campaigns_matches_committed_file() -> None:
    """The field in the response mirrors the committed JSON exactly."""
    body = _client().get("/v2/workspace/marketing", headers=HEADERS).json()
    campaigns = body["v1_lane_campaigns"]
    assert isinstance(campaigns, list)
    assert len(campaigns) == 1
    c = campaigns[0]
    assert c["key"] == "cyber-2026-10"
    assert c["name"] == "Cyber OrigenLab"
    assert c["channel"] == "v1"
    assert c["send_days"] == ["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09"]
    assert c["send_time"] == "09:30"
    assert c["promo_until"] == "2026-10-11"


def test_marketing_v1_lane_campaigns_is_empty_list_when_file_is_bad(tmp_path: Path, monkeypatch) -> None:
    """A bad file at runtime returns [] and never raises."""
    bad = tmp_path / "v1_lane_campaigns.json"
    bad.write_text('{"campaigns": [{"key": "INVALID KEY!!!"}]}', encoding="utf-8")
    monkeypatch.setattr("origenlab_api.v2.crm_workspace_routes.load_v1_lane_campaigns",
                        lambda: load_v1_lane_campaigns(bad))
    body = _client().get("/v2/workspace/marketing", headers=HEADERS).json()
    assert body["v1_lane_campaigns"] == []


def test_marketing_v1_lane_campaigns_all_roles_see_the_field() -> None:
    """The field is the same for viewer, sales and admin."""
    for role in ("viewer", "sales", "admin"):
        body = _client(role).get("/v2/workspace/marketing", headers=HEADERS).json()
        assert "v1_lane_campaigns" in body, f"v1_lane_campaigns missing for role={role}"
