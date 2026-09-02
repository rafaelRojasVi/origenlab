"""CRM-Q2 schema-layer tests: board-stage derivation and the workflow
fields on CustomerQuoteResponse."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from origenlab_api.repositories.postgres.customer_quotes import (
    CustomerQuote,
    CustomerQuoteBundle,
    CustomerQuoteDriveWorkspace,
    CustomerQuoteRevision,
)
from origenlab_api.schemas.customer_quotes import (
    BoardStage,
    CustomerQuoteResponse,
    derive_board_stage,
)


NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
QUOTE_ID = "quote_" + "a" * 32
SALES_ID = "sales_" + "b" * 32
OPERATOR = "tatiana@origenlab.cl"


@pytest.mark.parametrize(
    ("revision_status", "expected_stage"),
    [
        ("draft", "review"),
        ("adjustments_requested", "review"),
        ("pending_approval", "review"),
        ("approved", "approved_to_send"),
        ("sent", "sent_follow_up"),
    ],
)
def test_derive_board_stage_maps_every_reachable_revision_status(
    revision_status: str, expected_stage: str
) -> None:
    assert derive_board_stage(revision_status) == expected_stage


def test_preparation_is_no_longer_a_board_stage() -> None:
    """CRM-Q2B removes Preparación as a visible lane: draft and
    adjustments_requested now land in the same Revisión lane as
    pending_approval -- distinguished only by revision_status, never by a
    separate board column."""

    import typing

    assert "preparation" not in typing.get_args(BoardStage)
    assert set(typing.get_args(BoardStage)) == {
        "review",
        "approved_to_send",
        "sent_follow_up",
    }


def test_derive_board_stage_refuses_superseded() -> None:
    """'superseded' is never the *current* (latest) revision of a quote in
    this slice -- board_stage must fail loudly rather than silently guess a
    lane if it ever somehow received one."""

    with pytest.raises(ValueError):
        derive_board_stage("superseded")


def test_derive_board_stage_refuses_unknown_status() -> None:
    with pytest.raises(ValueError):
        derive_board_stage("nonexistent_status")


def _quote(**overrides: Any) -> CustomerQuote:
    values: dict[str, Any] = {
        "quote_id": QUOTE_ID,
        "sales_opportunity_id": SALES_ID,
        "quote_number": "01183-26",
        "serial": 1183,
        "issue_year": 2026,
        "document_number": "CN01183",
        "quote_origin": "generated",
        "status": "draft",
        "version": 3,
        "created_by": OPERATOR,
        "updated_by": OPERATOR,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return CustomerQuote(**values)


def _revision(**overrides: Any) -> CustomerQuoteRevision:
    values: dict[str, Any] = {
        "quote_id": QUOTE_ID,
        "revision_number": 1,
        "template_reference": None,
        "status": "approved",
        "created_by": OPERATOR,
        "created_at": NOW,
        "updated_by": OPERATOR,
        "updated_at": NOW,
    }
    values.update(overrides)
    return CustomerQuoteRevision(**values)


def _workspace(**overrides: Any) -> CustomerQuoteDriveWorkspace:
    values: dict[str, Any] = {
        "quote_id": QUOTE_ID,
        "provider": "google_drive",
        "provisioning_status": "ready",
        "folder_id": "folder-1",
        "folder_web_url": "https://drive.google.com/drive/folders/folder-1",
        "sheet_file_id": None,
        "sheet_web_url": None,
        "failure_category": None,
        "attempt_count": 0,
        "version": 1,
        "lease_expires_at": None,
        "requested_at": None,
        "completed_at": NOW,
        "created_by": OPERATOR,
        "updated_by": OPERATOR,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return CustomerQuoteDriveWorkspace(**values)


def test_response_exposes_board_stage_and_quote_origin_from_bundle() -> None:
    bundle = CustomerQuoteBundle(
        quote=_quote(quote_origin="adopted", serial=None, issue_year=None),
        revision=_revision(status="approved"),
        # An adopted workspace is folder_ready, never the fully-provisioned
        # 'ready' -- adoption performs no template/document step.
        workspace=_workspace(
            provisioning_status="folder_ready",
            sheet_file_id=None,
            sheet_web_url=None,
        ),
        sales_opportunity_title="Centrífuga CEAF",
    )

    response = CustomerQuoteResponse.from_bundle(bundle)

    assert response.quote_origin == "adopted"
    assert response.revision_status == "approved"
    assert response.board_stage == "approved_to_send"
    assert response.revision_updated_by == OPERATOR
    assert response.revision_updated_at == NOW
    assert response.drive_workspace.provisioning_status == "folder_ready"


def test_folder_ready_provisioning_status_is_accepted_and_not_retryable() -> None:
    bundle = CustomerQuoteBundle(
        quote=_quote(),
        revision=_revision(status="draft"),
        workspace=_workspace(
            provisioning_status="folder_ready",
            sheet_file_id=None,
            sheet_web_url=None,
            version=2,
        ),
        sales_opportunity_title="Centrífuga CEAF",
    )

    response = CustomerQuoteResponse.from_bundle(bundle)

    assert response.drive_workspace.provisioning_status == "folder_ready"
    assert response.drive_workspace.retryable is False
