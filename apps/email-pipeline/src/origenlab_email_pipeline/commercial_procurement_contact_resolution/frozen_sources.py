"""Independently loaded frozen PR2/PR4 source projections for PR5E."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from origenlab_email_pipeline.commercial_procurement.sources import (
    assert_active_read_transaction,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.organization import (
    load_pr4_resolutions_by_procurement,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.safety import (
    parse_usable_email,
)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    assert_active_read_transaction(conn)
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return bool(row)


def _tok(value: str | None) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FrozenContactProjection:
    contact_id: str
    account_id: str
    email_digest: str | None
    has_usable_email: bool
    role_digest: str
    role_raw: str | None
    identity_status: str
    identity_confidence: str
    evidence_ids: tuple[str, ...]
    # In-process only — never included in fingerprints / share artifacts.
    email_norm: str | None = None

    def to_fingerprint_row(self) -> dict[str, Any]:
        return {
            "contact_id_token": _tok(self.contact_id),
            "account_id_token": _tok(self.account_id),
            "email_digest": self.email_digest,
            "has_usable_email": self.has_usable_email,
            "role_digest": self.role_digest,
            "identity_status": self.identity_status,
            "identity_confidence": self.identity_confidence,
            "evidence_id_tokens": sorted(_tok(e) for e in self.evidence_ids),
        }


@dataclass(frozen=True)
class FrozenEvidenceProjection:
    evidence_id: str
    subject_kind: str
    subject_id: str
    source_table: str
    source_record_id: str
    source_plane: str
    origin_plane: str
    evidence_type: str
    evidence_at: str
    matching_reason_code: str
    confidence: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_fingerprint_row(self) -> dict[str, Any]:
        return {
            "evidence_id_token": _tok(self.evidence_id),
            "subject_kind": self.subject_kind,
            "subject_id_token": _tok(self.subject_id),
            "source_table": self.source_table,
            "source_record_id_token": _tok(self.source_record_id),
            "source_plane": self.source_plane,
            "origin_plane": self.origin_plane,
            "evidence_type": self.evidence_type,
            "evidence_at_token": _tok(self.evidence_at),
            "matching_reason_code": self.matching_reason_code,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class FrozenPr4ResolutionProjection:
    resolution_id: str
    procurement_id: str
    resolution_status: str
    account_id: str | None
    link_route: str | None
    reason_code: str
    candidate_account_ids: tuple[str, ...]

    def to_fingerprint_row(self) -> dict[str, Any]:
        return {
            "resolution_id_token": _tok(self.resolution_id),
            "procurement_id_token": _tok(self.procurement_id),
            "resolution_status": self.resolution_status,
            "account_id_token": _tok(self.account_id),
            "link_route": self.link_route,
            "reason_code": self.reason_code,
            "candidate_account_id_tokens": sorted(
                _tok(a) for a in self.candidate_account_ids
            ),
        }


@dataclass(frozen=True)
class FrozenSourceIndex:
    """Independent PR2/PR4 projections loaded inside the pinned read transaction."""

    contacts_by_id: dict[str, FrozenContactProjection]
    evidence_by_id: dict[str, FrozenEvidenceProjection]
    contacts_by_account: dict[str, tuple[str, ...]]
    pr4_by_procurement: dict[str, FrozenPr4ResolutionProjection]
    known_account_ids: frozenset[str]
    source_fingerprint: str

    def contact_ids_for_account(self, account_id: str) -> tuple[str, ...]:
        return self.contacts_by_account.get(account_id, ())


def _role_digest(role: str | None) -> str:
    return hashlib.sha256((role or "").strip().encode("utf-8")).hexdigest()[:16]


def _email_digest(email: str | None) -> str | None:
    if not email:
        return None
    return hashlib.sha256(email.encode("utf-8")).hexdigest()


def load_frozen_source_index(
    conn: sqlite3.Connection,
    *,
    account_ids: Iterable[str],
    pr4_procurement_ids: Iterable[str],
    known_account_ids: frozenset[str],
) -> FrozenSourceIndex:
    """Load immutable PR2/PR4 projections independently of emitted PR5E rows."""
    assert_active_read_transaction(conn)
    wanted_accounts = sorted({a for a in account_ids if a})
    contacts_by_id: dict[str, FrozenContactProjection] = {}
    contacts_by_account: dict[str, list[str]] = {a: [] for a in wanted_accounts}
    evidence_by_id: dict[str, FrozenEvidenceProjection] = {}

    if wanted_accounts and _table_exists(conn, "commercial_identity_contact"):
        placeholders = ",".join("?" for _ in wanted_accounts)
        rows = conn.execute(
            f"""
            SELECT contact_id, normalized_email, role, account_id,
                   identity_confidence, identity_status
            FROM commercial_identity_contact
            WHERE account_id IN ({placeholders})
            ORDER BY contact_id
            """,
            tuple(wanted_accounts),
        )
        contact_rows = [dict(r) for r in rows]
        contact_ids = [str(r["contact_id"]) for r in contact_rows]

        evidence_by_contact: dict[str, list[dict[str, Any]]] = {
            cid: [] for cid in contact_ids
        }
        if contact_ids and _table_exists(conn, "commercial_identity_evidence"):
            eph = ",".join("?" for _ in contact_ids)
            ev_rows = conn.execute(
                f"""
                SELECT evidence_id, subject_kind, subject_id, source_table,
                       source_record_id, source_plane, origin_plane, evidence_type,
                       evidence_at, matching_reason_code, confidence
                FROM commercial_identity_evidence
                WHERE subject_kind = 'contact' AND subject_id IN ({eph})
                ORDER BY evidence_id
                """,
                tuple(contact_ids),
            )
            for er in ev_rows:
                d = dict(er)
                eid = str(d["evidence_id"])
                evidence_by_id[eid] = FrozenEvidenceProjection(
                    evidence_id=eid,
                    subject_kind=str(d.get("subject_kind") or ""),
                    subject_id=str(d.get("subject_id") or ""),
                    source_table=str(d.get("source_table") or ""),
                    source_record_id=str(d.get("source_record_id") or ""),
                    source_plane=str(d.get("source_plane") or ""),
                    origin_plane=str(d.get("origin_plane") or ""),
                    evidence_type=str(d.get("evidence_type") or ""),
                    evidence_at=str(d.get("evidence_at") or ""),
                    matching_reason_code=str(d.get("matching_reason_code") or ""),
                    confidence=str(d.get("confidence") or ""),
                )
                evidence_by_contact.setdefault(str(d["subject_id"]), []).append(d)

        for r in contact_rows:
            cid = str(r["contact_id"])
            aid = str(r["account_id"] or "")
            email = parse_usable_email(r.get("normalized_email"))
            role = r.get("role") if isinstance(r.get("role"), str) else None
            ev_ids = tuple(
                sorted(str(e["evidence_id"]) for e in evidence_by_contact.get(cid, []))
            )
            contacts_by_id[cid] = FrozenContactProjection(
                contact_id=cid,
                account_id=aid,
                email_digest=_email_digest(email),
                has_usable_email=bool(email),
                role_digest=_role_digest(role),
                role_raw=role,
                identity_status=str(r.get("identity_status") or ""),
                identity_confidence=str(r.get("identity_confidence") or ""),
                evidence_ids=ev_ids,
                email_norm=email,
            )
            contacts_by_account.setdefault(aid, []).append(cid)

    pr4_raw = load_pr4_resolutions_by_procurement(conn)
    wanted_pr4 = sorted({p for p in pr4_procurement_ids if p})
    pr4_by_procurement: dict[str, FrozenPr4ResolutionProjection] = {}
    for pid in wanted_pr4:
        row = pr4_raw.get(pid)
        if not row:
            continue
        pr4_by_procurement[pid] = FrozenPr4ResolutionProjection(
            resolution_id=str(row["resolution_id"]),
            procurement_id=str(row["procurement_id"]),
            resolution_status=str(row.get("resolution_status") or ""),
            account_id=(str(row["account_id"]) if row.get("account_id") else None),
            link_route=(str(row["link_route"]) if row.get("link_route") else None),
            reason_code=str(row.get("reason_code") or ""),
            candidate_account_ids=tuple(row.get("candidate_account_ids") or ()),
        )

    # Also retain full PR4 map entries used for organization resolution lookups.
    for pid, row in pr4_raw.items():
        if pid in pr4_by_procurement:
            continue
        pr4_by_procurement[pid] = FrozenPr4ResolutionProjection(
            resolution_id=str(row["resolution_id"]),
            procurement_id=str(row["procurement_id"]),
            resolution_status=str(row.get("resolution_status") or ""),
            account_id=(str(row["account_id"]) if row.get("account_id") else None),
            link_route=(str(row["link_route"]) if row.get("link_route") else None),
            reason_code=str(row.get("reason_code") or ""),
            candidate_account_ids=tuple(row.get("candidate_account_ids") or ()),
        )

    contacts_by_account_t = {
        k: tuple(sorted(v)) for k, v in contacts_by_account.items()
    }
    source_fingerprint = canonical_json_digest(
        {
            "contacts": sorted(
                (c.to_fingerprint_row() for c in contacts_by_id.values()),
                key=lambda r: r["contact_id_token"],
            ),
            "evidence": sorted(
                (e.to_fingerprint_row() for e in evidence_by_id.values()),
                key=lambda r: r["evidence_id_token"],
            ),
            "pr4": sorted(
                (p.to_fingerprint_row() for p in pr4_by_procurement.values()),
                key=lambda r: r["resolution_id_token"],
            ),
            "known_account_id_tokens": sorted(_tok(a) for a in known_account_ids),
        }
    )
    return FrozenSourceIndex(
        contacts_by_id=contacts_by_id,
        evidence_by_id=evidence_by_id,
        contacts_by_account=contacts_by_account_t,
        pr4_by_procurement=pr4_by_procurement,
        known_account_ids=known_account_ids,
        source_fingerprint=source_fingerprint,
    )


def pr4_mapping_for_organization(
    index: FrozenSourceIndex,
) -> dict[str, dict[str, Any]]:
    """Shape expected by ``resolve_organization_for_tender``."""
    out: dict[str, dict[str, Any]] = {}
    for pid, row in index.pr4_by_procurement.items():
        out[pid] = {
            "resolution_id": row.resolution_id,
            "procurement_id": row.procurement_id,
            "resolution_status": row.resolution_status,
            "account_id": row.account_id,
            "link_route": row.link_route,
            "reason_code": row.reason_code,
            "candidate_account_ids": row.candidate_account_ids,
            "candidate_account_ids_json": None,  # already parsed; do not re-parse
        }
    return out
