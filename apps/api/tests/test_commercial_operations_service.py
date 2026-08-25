"""ARCH-3B3 commercial operations service tests."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from origenlab_api.services.commercial_operations_service import (
    CommercialOperationsService,
)
from origenlab_api.settings import Settings


class FakeRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def upsert_operator_state(self, **kwargs: Any) -> Any:
        self.calls.append(("upsert_operator_state", kwargs))
        return kwargs

    def create_activity(self, **kwargs: Any) -> Any:
        self.calls.append(("create_activity", kwargs))
        return kwargs

    def create_task(self, **kwargs: Any) -> Any:
        self.calls.append(("create_task", kwargs))
        return kwargs

    def transition_task(self, **kwargs: Any) -> Any:
        self.calls.append(("transition_task", kwargs))
        return kwargs


def _service() -> tuple[CommercialOperationsService, FakeRepository]:
    repo = FakeRepository()
    service = CommercialOperationsService(
        Settings(),
        repository=repo,  # type: ignore[arg-type]
    )
    return service, repo


def test_set_opportunity_state_normalizes_operator_and_status() -> None:
    service, repo = _service()

    service.set_opportunity_state(
        opportunity_id=" opp_1 ",
        confirmation_status=" CONFIRMED ",
        operator=" Tatiana@OrigenLab.CL ",
        manual_stage=" quote_sent ",
        expected_version=1,
    )

    name, kwargs = repo.calls[-1]

    assert name == "upsert_operator_state"
    assert kwargs["opportunity_id"] == "opp_1"
    assert kwargs["confirmation_status"] == "confirmed"
    assert kwargs["operator"] == "tatiana@origenlab.cl"
    assert kwargs["manual_stage"] == "quote_sent"
    assert kwargs["expected_version"] == 1


def test_set_opportunity_state_rejects_invalid_status() -> None:
    service, _ = _service()

    with pytest.raises(ValueError, match="confirmation_status"):
        service.set_opportunity_state(
            opportunity_id="opp_1",
            confirmation_status="maybe",
            operator="tatiana@origenlab.cl",
            expected_version=0,
        )


def test_activity_requires_crm_context() -> None:
    service, _ = _service()

    with pytest.raises(ValueError, match="CRM context"):
        service.create_activity(
            activity_type="call",
            occurred_at=datetime.now(timezone.utc),
            summary="Called customer",
            operator="tatiana@origenlab.cl",
            idempotency_key="activity-context-1",
        )


def test_activity_rejects_naive_datetime() -> None:
    service, _ = _service()

    with pytest.raises(ValueError, match="timezone-aware"):
        service.create_activity(
            activity_type="call",
            occurred_at=datetime(2026, 8, 24, 10, 0),
            summary="Called customer",
            operator="tatiana@origenlab.cl",
            idempotency_key="activity-naive-1",
            contact_id="contact_1",
        )


def test_activity_generates_id_and_normalizes_values() -> None:
    service, repo = _service()

    service.create_activity(
        activity_type=" WHATSAPP ",
        occurred_at=datetime(
            2026,
            8,
            24,
            14,
            30,
            tzinfo=timezone.utc,
        ),
        summary="  Cliente pidió seguimiento  ",
        detail="  Llamar el martes  ",
        operator=" Tatiana@OrigenLab.CL ",
        idempotency_key="activity-create-1",
        opportunity_id=" opp_1 ",
    )

    name, kwargs = repo.calls[-1]

    assert name == "create_activity"
    assert kwargs["activity_id"].startswith("act_")
    assert kwargs["activity_type"] == "whatsapp"
    assert kwargs["summary"] == "Cliente pidió seguimiento"
    assert kwargs["detail"] == "Llamar el martes"
    assert kwargs["operator"] == "tatiana@origenlab.cl"


def test_task_requires_crm_context() -> None:
    service, _ = _service()

    with pytest.raises(ValueError, match="CRM context"):
        service.create_task(
            title="Follow up",
            operator="tatiana@origenlab.cl",
            idempotency_key="task-context-1",
        )


def test_task_defaults_to_normal_priority() -> None:
    service, repo = _service()

    service.create_task(
        title=" Follow up Bioeq ",
        operator="tatiana@origenlab.cl",
        idempotency_key="task-create-1",
        opportunity_id="opp_1",
    )

    name, kwargs = repo.calls[-1]

    assert name == "create_task"
    assert kwargs["task_id"].startswith("task_")
    assert kwargs["title"] == "Follow up Bioeq"
    assert kwargs["priority"] == "normal"


@pytest.mark.parametrize(
    "method,status",
    [
        ("complete_task", "done"),
        ("cancel_task", "cancelled"),
    ],
)
def test_task_terminal_commands_use_expected_version(
    method: str,
    status: str,
) -> None:
    service, repo = _service()

    getattr(service, method)(
        task_id=" task_1 ",
        operator=" Tatiana@OrigenLab.CL ",
        expected_version=3,
    )

    name, kwargs = repo.calls[-1]

    assert name == "transition_task"
    assert kwargs == {
        "task_id": "task_1",
        "status": status,
        "operator": "tatiana@origenlab.cl",
        "expected_version": 3,
    }


@pytest.mark.parametrize(
    ("method", "expected_version"),
    [
        ("complete_task", 0),
        ("cancel_task", -1),
    ],
)
def test_task_terminal_commands_reject_invalid_version(
    method: str,
    expected_version: int,
) -> None:
    service, repo = _service()

    with pytest.raises(
        ValueError,
        match="expected_version must be >= 1",
    ):
        getattr(service, method)(
            task_id="task_1",
            operator="tatiana@origenlab.cl",
            expected_version=expected_version,
        )

    assert repo.calls == []


def test_activity_fingerprint_is_stable_after_normalization() -> None:
    service, repo = _service()

    kwargs = {
        "activity_type": " CALL ",
        "occurred_at": datetime(
            2026,
            8,
            24,
            10,
            30,
            tzinfo=timezone.utc,
        ),
        "summary": " Customer call ",
        "operator": " Tatiana@OrigenLab.CL ",
        "idempotency_key": "activity-stable-key",
        "opportunity_id": " opp_1 ",
    }

    service.create_activity(**kwargs)
    first = repo.calls[-1][1]

    service.create_activity(**kwargs)
    second = repo.calls[-1][1]

    assert first["idempotency_key"] == "activity-stable-key"
    assert first["request_fingerprint"] == second["request_fingerprint"]
    assert len(first["request_fingerprint"]) == 64


def test_task_fingerprint_changes_when_normalized_request_changes() -> None:
    service, repo = _service()

    service.create_task(
        title="Follow up",
        operator="tatiana@origenlab.cl",
        idempotency_key="task-key",
        opportunity_id="opp_1",
        priority="normal",
    )
    first = repo.calls[-1][1]["request_fingerprint"]

    service.create_task(
        title="Follow up urgently",
        operator="tatiana@origenlab.cl",
        idempotency_key="task-key",
        opportunity_id="opp_1",
        priority="normal",
    )
    second = repo.calls[-1][1]["request_fingerprint"]

    assert first != second


def test_create_rejects_unsafe_idempotency_key() -> None:
    service, repo = _service()

    with pytest.raises(
        ValueError,
        match="idempotency_key",
    ):
        service.create_task(
            title="Follow up",
            operator="tatiana@origenlab.cl",
            idempotency_key="bad key with spaces",
            opportunity_id="opp_1",
        )

    assert repo.calls == []
