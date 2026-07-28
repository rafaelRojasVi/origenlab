"""Typed records for the commercial identity resolver (no opportunity-stage fields)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SourceIdentityRow:
    """One observed identity assertion from an existing evidence plane."""

    source_table: str
    source_record_id: str
    source_plane: str
    origin_plane: str
    email_raw: str | None = None
    display_name: str | None = None
    role: str | None = None
    organization_name: str | None = None
    domain_raw: str | None = None
    evidence_at: str | None = None  # observed timestamp or None — never invent build time
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class AccountRecord:
    account_id: str
    canonical_name: str
    normalized_name: str
    primary_domain: str | None
    first_evidence_at: str | None
    last_evidence_at: str | None
    identity_confidence: str
    identity_status: str
    aliases: dict[str, dict[str, Any]] = field(default_factory=dict)
    domains: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class ContactRecord:
    contact_id: str
    normalized_email: str
    display_name: str | None
    role: str | None
    account_id: str | None
    account_link_method: str | None
    first_evidence_at: str | None
    last_evidence_at: str | None
    identity_confidence: str
    identity_status: str
    email_domain: str | None = None


@dataclass
class EvidenceRecord:
    evidence_id: str
    subject_kind: str
    subject_id: str
    source_table: str
    source_record_id: str
    source_plane: str
    origin_plane: str
    evidence_type: str
    evidence_at: str | None
    matching_reason_code: str
    confidence: str
    detail_json: str | None = None


@dataclass
class ConflictRecord:
    conflict_id: str
    conflict_type: str
    reason_code: str
    subject_kind: str
    subject_keys_json: str
    evidence_pointers_json: str
    identity_status: str
    detail_json: str | None = None


@dataclass
class IdentityResolution:
    accounts: list[AccountRecord]
    contacts: list[ContactRecord]
    evidence: list[EvidenceRecord]
    conflicts: list[ConflictRecord]
    metrics: dict[str, Any]
