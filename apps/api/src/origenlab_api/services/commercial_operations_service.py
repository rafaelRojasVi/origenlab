"""Commercial operations command service (ARCH-3B3)."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from uuid import uuid4

from origenlab_api.repositories.postgres.commercial_operations import (
    Activity,
    OperatorState,
    PostgresCommercialOperationsRepository,
    SalesOpportunity,
    Task,
)
from origenlab_api.settings import Settings


CONFIRMATION_STATUSES = frozenset(
    {
        "confirmed",
        "rejected",
        "needs_review",
    }
)

ACTIVITY_TYPES = frozenset(
    {
        "call",
        "whatsapp",
        "meeting",
        "email",
        "note",
        "quote",
        "follow_up",
        "other",
    }
)

IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")


TASK_PRIORITIES = frozenset(
    {
        "low",
        "normal",
        "high",
        "urgent",
    }
)


def _required_text(value: str, *, field: str, max_length: int) -> str:
    normalized = value.strip()

    if not normalized:
        raise ValueError(f"{field} must not be blank")

    if len(normalized) > max_length:
        raise ValueError(f"{field} exceeds maximum length {max_length}")

    return normalized


def _optional_text(
    value: str | None,
    *,
    field: str,
    max_length: int,
) -> str | None:
    if value is None:
        return None

    normalized = value.strip()

    if not normalized:
        raise ValueError(f"{field} must not be blank when provided")

    if len(normalized) > max_length:
        raise ValueError(f"{field} exceeds maximum length {max_length}")

    return normalized


def _require_context(
    *,
    opportunity_id: str | None,
    account_id: str | None,
    contact_id: str | None,
) -> tuple[str | None, str | None, str | None]:
    opportunity = _optional_text(
        opportunity_id,
        field="opportunity_id",
        max_length=128,
    )
    account = _optional_text(
        account_id,
        field="account_id",
        max_length=128,
    )
    contact = _optional_text(
        contact_id,
        field="contact_id",
        max_length=128,
    )

    if opportunity is None and account is None and contact is None:
        raise ValueError("At least one CRM context reference is required")

    return opportunity, account, contact


def _idempotency_key(value: str) -> str:
    normalized = _required_text(
        value,
        field="idempotency_key",
        max_length=200,
    )

    if IDEMPOTENCY_KEY_RE.fullmatch(normalized) is None:
        raise ValueError("idempotency_key contains unsupported characters")

    return normalized


def _fingerprint(
    command_kind: str,
    payload: dict[str, object],
) -> str:
    canonical = json.dumps(
        {
            "command_kind": command_kind,
            "payload": payload,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")

    return sha256(canonical).hexdigest()


def _aware_utc(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")

    return value.astimezone(timezone.utc)


class CommercialOperationsService:
    def __init__(
        self,
        settings: Settings,
        *,
        repository: PostgresCommercialOperationsRepository | None = None,
    ) -> None:
        self._repository = repository or PostgresCommercialOperationsRepository(
            settings
        )

    def set_opportunity_state(
        self,
        *,
        opportunity_id: str,
        confirmation_status: str,
        operator: str,
        manual_stage: str | None = None,
        owner_key: str | None = None,
        expected_version: int,
    ) -> OperatorState:
        normalized_id = _required_text(
            opportunity_id,
            field="opportunity_id",
            max_length=128,
        )

        status = confirmation_status.strip().lower()

        if status not in CONFIRMATION_STATUSES:
            raise ValueError(
                f"Unsupported confirmation_status: {confirmation_status!r}"
            )

        if expected_version < 0:
            raise ValueError("expected_version must be >= 0")

        return self._repository.upsert_operator_state(
            opportunity_id=normalized_id,
            confirmation_status=status,
            manual_stage=_optional_text(
                manual_stage,
                field="manual_stage",
                max_length=128,
            ),
            owner_key=_optional_text(
                owner_key,
                field="owner_key",
                max_length=320,
            ),
            operator=_required_text(
                operator,
                field="operator",
                max_length=320,
            ).lower(),
            expected_version=expected_version,
        )

    def create_activity(
        self,
        *,
        activity_type: str,
        occurred_at: datetime,
        summary: str,
        operator: str,
        idempotency_key: str,
        opportunity_id: str | None = None,
        account_id: str | None = None,
        contact_id: str | None = None,
        detail: str | None = None,
    ) -> Activity:
        activity = activity_type.strip().lower()

        if activity not in ACTIVITY_TYPES:
            raise ValueError(f"Unsupported activity_type: {activity_type!r}")

        opportunity, account, contact = _require_context(
            opportunity_id=opportunity_id,
            account_id=account_id,
            contact_id=contact_id,
        )

        occurred = _aware_utc(
            occurred_at,
            field="occurred_at",
        )
        normalized_summary = _required_text(
            summary,
            field="summary",
            max_length=500,
        )
        normalized_detail = _optional_text(
            detail,
            field="detail",
            max_length=10_000,
        )
        normalized_operator = _required_text(
            operator,
            field="operator",
            max_length=320,
        ).lower()
        normalized_key = _idempotency_key(idempotency_key)

        fingerprint = _fingerprint(
            "activity_create",
            {
                "opportunity_id": opportunity,
                "account_id": account,
                "contact_id": contact,
                "activity_type": activity,
                "occurred_at": occurred.isoformat(),
                "summary": normalized_summary,
                "detail": normalized_detail,
            },
        )

        return self._repository.create_activity(
            activity_id=f"act_{uuid4().hex}",
            opportunity_id=opportunity,
            account_id=account,
            contact_id=contact,
            activity_type=activity,
            occurred_at=occurred,
            summary=normalized_summary,
            detail=normalized_detail,
            operator=normalized_operator,
            idempotency_key=normalized_key,
            request_fingerprint=fingerprint,
        )

    def create_task(
        self,
        *,
        title: str,
        operator: str,
        idempotency_key: str,
        priority: str = "normal",
        due_at: datetime | None = None,
        owner_key: str | None = None,
        opportunity_id: str | None = None,
        account_id: str | None = None,
        contact_id: str | None = None,
    ) -> Task:
        normalized_priority = priority.strip().lower()

        if normalized_priority not in TASK_PRIORITIES:
            raise ValueError(f"Unsupported priority: {priority!r}")

        opportunity, account, contact = _require_context(
            opportunity_id=opportunity_id,
            account_id=account_id,
            contact_id=contact_id,
        )

        normalized_title = _required_text(
            title,
            field="title",
            max_length=500,
        )
        normalized_due_at = (
            _aware_utc(
                due_at,
                field="due_at",
            )
            if due_at is not None
            else None
        )
        normalized_owner = _optional_text(
            owner_key,
            field="owner_key",
            max_length=320,
        )
        normalized_operator = _required_text(
            operator,
            field="operator",
            max_length=320,
        ).lower()
        normalized_key = _idempotency_key(idempotency_key)

        fingerprint = _fingerprint(
            "task_create",
            {
                "opportunity_id": opportunity,
                "account_id": account,
                "contact_id": contact,
                "title": normalized_title,
                "priority": normalized_priority,
                "due_at": (
                    normalized_due_at.isoformat()
                    if normalized_due_at is not None
                    else None
                ),
                "owner_key": normalized_owner,
            },
        )

        return self._repository.create_task(
            task_id=f"task_{uuid4().hex}",
            opportunity_id=opportunity,
            account_id=account,
            contact_id=contact,
            title=normalized_title,
            priority=normalized_priority,
            due_at=normalized_due_at,
            owner_key=normalized_owner,
            operator=normalized_operator,
            idempotency_key=normalized_key,
            request_fingerprint=fingerprint,
        )

    def promote_sales_opportunity(
        self,
        *,
        source_opportunity_id: str,
        title: str,
        owner_key: str,
        operator: str,
        idempotency_key: str,
    ) -> SalesOpportunity:
        source_id = _required_text(
            source_opportunity_id,
            field="source_opportunity_id",
            max_length=128,
        )
        normalized_title = _required_text(
            title,
            field="title",
            max_length=500,
        )
        normalized_owner = _required_text(
            owner_key,
            field="owner_key",
            max_length=320,
        )
        normalized_operator = _required_text(
            operator,
            field="operator",
            max_length=320,
        ).lower()
        normalized_key = _idempotency_key(idempotency_key)

        fingerprint = _fingerprint(
            "sales_opportunity_promote",
            {
                "source_opportunity_id": source_id,
                "title": normalized_title,
                "owner_key": normalized_owner,
            },
        )

        return self._repository.promote_sales_opportunity(
            sales_opportunity_id=f"sales_{uuid4().hex}",
            source_opportunity_id=source_id,
            title=normalized_title,
            owner_key=normalized_owner,
            operator=normalized_operator,
            idempotency_key=normalized_key,
            request_fingerprint=fingerprint,
        )

    def complete_task(
        self,
        *,
        task_id: str,
        operator: str,
        expected_version: int,
    ) -> Task:
        if expected_version < 1:
            raise ValueError("expected_version must be >= 1")

        return self._repository.transition_task(
            task_id=_required_text(
                task_id,
                field="task_id",
                max_length=128,
            ),
            status="done",
            operator=_required_text(
                operator,
                field="operator",
                max_length=320,
            ).lower(),
            expected_version=expected_version,
        )

    def cancel_task(
        self,
        *,
        task_id: str,
        operator: str,
        expected_version: int,
    ) -> Task:
        if expected_version < 1:
            raise ValueError("expected_version must be >= 1")

        return self._repository.transition_task(
            task_id=_required_text(
                task_id,
                field="task_id",
                max_length=128,
            ),
            status="cancelled",
            operator=_required_text(
                operator,
                field="operator",
                max_length=320,
            ).lower(),
            expected_version=expected_version,
        )
