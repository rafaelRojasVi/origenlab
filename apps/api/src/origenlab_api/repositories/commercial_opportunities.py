"""Shared commercial-opportunity row mapping for SQLite/Postgres backends."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from origenlab_api.schemas.commercial_opportunities import (
    CommercialOpportunityConflict,
    CommercialOpportunityEvidence,
    CommercialOpportunityEvent,
    CommercialOpportunityItem,
)


def text_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        return json.loads(stripped)
    return value


def map_opportunity_row(row: dict[str, Any]) -> CommercialOpportunityItem:
    return CommercialOpportunityItem(
        opportunity_id=str(row["opportunity_id"]),
        record_kind=str(row["record_kind"]),
        account_id=text_value(row.get("account_id")),
        primary_contact_id=text_value(row.get("primary_contact_id")),
        contact_display_email=text_value(row.get("contact_display_email")),
        account_display_domain=text_value(row.get("account_display_domain")),
        source_kind=str(row["source_kind"]),
        source_key=str(row["source_key"]),
        deal_key=text_value(row.get("deal_key")),
        canonical_stage=str(row["canonical_stage"]),
        source_stage=str(row["source_stage"]),
        stage_reason_code=str(row["stage_reason_code"]),
        stage_confidence=str(row["stage_confidence"]),
        stage_is_current=bool(row["stage_is_current"]),
        stage_is_terminal=bool(row["stage_is_terminal"]),
        stage_evidence_at=text_value(row.get("stage_evidence_at")),
        stage_evidence_id=text_value(row.get("stage_evidence_id")),
        first_activity_at=text_value(row.get("first_activity_at")),
        last_activity_at=text_value(row.get("last_activity_at")),
        identity_link_status=str(row["identity_link_status"]),
        review_status=str(row["review_status"]),
        synced_at=text_value(row.get("synced_at")),
    )


def map_event_row(row: dict[str, Any]) -> CommercialOpportunityEvent:
    return CommercialOpportunityEvent(
        event_id=str(row["event_id"]),
        opportunity_id=str(row["opportunity_id"]),
        canonical_event_type=str(row["canonical_event_type"]),
        source_event_type=str(row["source_event_type"]),
        event_at=text_value(row.get("event_at")),
        source_table=str(row["source_table"]),
        source_record_id=str(row["source_record_id"]),
        source_email_id=row.get("source_email_id"),
        source_attachment_id=row.get("source_attachment_id"),
        confidence=str(row["confidence"]),
        operator_confirmed=bool(row["operator_confirmed"]),
        detail_json=json_value(row.get("detail_json")),
        synced_at=text_value(row.get("synced_at")),
    )


def map_evidence_row(row: dict[str, Any]) -> CommercialOpportunityEvidence:
    return CommercialOpportunityEvidence(
        evidence_id=str(row["evidence_id"]),
        opportunity_id=str(row["opportunity_id"]),
        subject_kind=str(row["subject_kind"]),
        source_table=str(row["source_table"]),
        source_record_id=str(row["source_record_id"]),
        evidence_type=str(row["evidence_type"]),
        evidence_at=text_value(row.get("evidence_at")),
        confidence=str(row["confidence"]),
        reason_code=str(row["reason_code"]),
        source_email_id=row.get("source_email_id"),
        source_attachment_id=row.get("source_attachment_id"),
        detail_json=json_value(row.get("detail_json")),
        synced_at=text_value(row.get("synced_at")),
    )


def map_conflict_row(row: dict[str, Any]) -> CommercialOpportunityConflict:
    return CommercialOpportunityConflict(
        conflict_id=str(row["conflict_id"]),
        opportunity_id=text_value(row.get("opportunity_id")),
        conflict_type=str(row["conflict_type"]),
        reason_code=str(row["reason_code"]),
        subject_keys_json=json_value(row["subject_keys_json"]),
        evidence_pointers_json=json_value(row["evidence_pointers_json"]),
        review_status=str(row["review_status"]),
        detail_json=json_value(row.get("detail_json")),
        synced_at=text_value(row.get("synced_at")),
    )
