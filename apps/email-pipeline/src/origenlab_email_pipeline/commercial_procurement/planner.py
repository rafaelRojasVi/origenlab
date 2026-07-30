"""Deterministic commercial procurement build planner (PR4) — dry-run only.

Produces the exact immutable rows a later persistence PR would insert.
Never mutates production SQLite, Gmail, or Postgres.
"""

from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from datetime import date
from typing import Any

from origenlab_email_pipeline.commercial_procurement.coalesce import (
    coalesce_verified_tender_lines,
)
from origenlab_email_pipeline.commercial_procurement.constants import (
    BUILD_CONTRACT,
    CONFIDENCE_NONE,
    FIELD_ORIGIN_ABSENT,
    FIELD_ORIGIN_LEAD,
    FIELD_ORIGIN_RAW,
    OPERATOR_ELIGIBLE_CONTEXTS,
    PROCUREMENT_CONTEXT_HISTORICAL,
    PROCUREMENT_MATERIALIZATION_DIGEST_ALGORITHM,
    PROCUREMENT_SEMANTIC_PLAN_DIGEST_ALGORITHM,
    REASON_BUYER_ACCOUNT_NOT_FOUND,
    REASON_LINE_FIELD_CONFLICT,
    REASON_TENDER_IDENTIFIER_MISSING,
    REASON_TENDER_KEY_UNRESOLVED,
    REQUIRED_IDENTITY_FINGERPRINT_ALGORITHM,
    RESOLUTION_LINKED,
    RESOLVER_BUILD_CONTRACT_VERSION,
    ROUTE_AMBIGUOUS_MULTI_ACCOUNT,
    ROUTE_NAME_DOMAIN_CONFLICT,
    SCHEMA_VERSION,
    SOURCE_CHILECOMPRA,
    TENDER_KEY_MISSING,
    TRANSACTION_CONTRACT,
)
from origenlab_email_pipeline.commercial_procurement.fingerprint import (
    conflict_id_for_source_row,
    procurement_build_plan_fingerprint,
    procurement_source_fingerprint,
    source_line_semantic_payload,
)
from origenlab_email_pipeline.commercial_procurement.ids import (
    canonical_json,
    materialization_digest,
    semantic_plan_digest,
    stable_enrichment_candidate_id,
    stable_evidence_id,
    stable_conflict_id_for_signal,
    stable_procurement_id,
    subject_key_for_source,
)
from origenlab_email_pipeline.commercial_procurement.link_routes import (
    AccountIndex,
    classify_account_link_route,
)
from origenlab_email_pipeline.commercial_procurement.models import (
    AccountResolutionRow,
    BuildMetaRow,
    ConflictRow,
    EnrichmentCandidateRow,
    EvidenceRow,
    ProcurementPlan,
    SignalRow,
)
from origenlab_email_pipeline.commercial_procurement.provenance import origins_for_evidence
from origenlab_email_pipeline.commercial_procurement.resolution import (
    assert_resolution_invariants,
    build_account_resolution,
)
from origenlab_email_pipeline.commercial_procurement.sources import (
    SourceSchemaError,
    build_source_pointer_registry,
    load_chilecompra_source_lines,
    load_identity_fingerprint_meta,
    load_known_account_ids,
    load_pr2_account_index,
    load_pr3_immutability_sentinel,
)


class IdentityGateError(ValueError):
    """Persisted PR2 identity snapshot missing or wrong algorithm."""


class PlanValidationError(ValueError):
    """Internal plan invariants failed."""


def _research_field_for_reason(reason: str) -> str:
    if "ambiguous" in reason or reason.endswith("ambiguous"):
        return "account_disambiguation"
    if "contact" in reason or "email" in reason:
        return "contact"
    if "status" in reason or "date" in reason:
        return "status_or_dates"
    if "tender" in reason:
        return "tender_id"
    if "conflict" in reason:
        return "line_field_reconciliation"
    return "domain"


def _operator_eligible(
    *,
    procurement_context: str,
    linked: bool,
    has_ambiguity: bool,
    status_code: str | None,
) -> bool:
    if procurement_context in OPERATOR_ELIGIBLE_CONTEXTS:
        return True
    if has_ambiguity and procurement_context != PROCUREMENT_CONTEXT_HISTORICAL:
        return True
    if not linked and procurement_context == "unknown" and status_code:
        return True
    return False


def _signal_confidence(sig: dict[str, Any]) -> str:
    if sig.get("buyer_name_norm") and (sig.get("status_code") or sig.get("status_name")):
        if sig.get("buyer_domain") or sig.get("email_norm"):
            return "high"
        return "medium"
    if sig.get("buyer_name_norm") or sig.get("status_code"):
        return "low"
    return CONFIDENCE_NONE


def _require_identity_fp_v2(meta: dict[str, str | None]) -> tuple[str, str]:
    algo = meta.get("identity_fingerprint_algorithm_version")
    fp = meta.get("identity_fingerprint")
    if not fp:
        raise IdentityGateError("persisted PR2 identity_fingerprint is missing")
    if algo != REQUIRED_IDENTITY_FINGERPRINT_ALGORITHM:
        raise IdentityGateError(
            f"require {REQUIRED_IDENTITY_FINGERPRINT_ALGORITHM}; got {algo!r}"
        )
    return fp, algo


def _add_evidence(
    *,
    evidence: list[EvidenceRow],
    evidence_ids: set[str],
    subject_kind: str,
    subject_id: str,
    source_table: str,
    source_record_id: str,
    evidence_type: str,
    reason_code: str,
    source_system: str | None = SOURCE_CHILECOMPRA,
    subject_key: str | None = None,
    evidence_at: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    if not source_record_id:
        raise PlanValidationError(
            f"empty source_record_id for evidence {subject_kind}/{evidence_type}"
        )
    eid = stable_evidence_id(
        subject_kind=subject_kind,
        subject_id=subject_id,
        source_table=source_table,
        source_record_id=source_record_id,
        evidence_type=evidence_type,
        reason_code=reason_code,
    )
    if eid in evidence_ids:
        return
    evidence_ids.add(eid)
    evidence.append(
        EvidenceRow(
            evidence_id=eid,
            subject_kind=subject_kind,
            subject_id=subject_id,
            source_system=source_system,
            source_table=source_table,
            source_record_id=source_record_id,
            subject_key=subject_key,
            evidence_type=evidence_type,
            evidence_at=evidence_at,
            reason_code=reason_code,
            detail_json=canonical_json(detail) if detail is not None else None,
        )
    )


def _emit_field_evidence(
    *,
    evidence: list[EvidenceRow],
    evidence_ids: set[str],
    subject_kind: str,
    subject_id: str,
    line: dict[str, Any],
    evidence_type: str,
    origin: str,
    reason_code: str,
) -> None:
    sk = subject_key_for_source(
        source_system=SOURCE_CHILECOMPRA,
        source_record_id=str(line.get("source_record_id") or ""),
    )
    at = line.get("first_seen_at")
    for table in origins_for_evidence(origin):
        if table == "external_leads_raw":
            if not line.get("has_raw_source") or not line.get("raw_source_record_id"):
                continue
            rid = str(line["raw_source_record_id"])
        else:
            if not line.get("has_lead_source") or not line.get("lead_source_record_id"):
                continue
            rid = str(line["lead_source_record_id"])
        _add_evidence(
            evidence=evidence,
            evidence_ids=evidence_ids,
            subject_kind=subject_kind,
            subject_id=subject_id,
            source_table=table,
            source_record_id=rid,
            evidence_type=evidence_type,
            reason_code=reason_code,
            subject_key=sk,
            evidence_at=at,
            detail={"field_origin": origin},
        )


def _emit_line_plane_evidence(
    *,
    evidence: list[EvidenceRow],
    evidence_ids: set[str],
    subject_kind: str,
    subject_id: str,
    line: dict[str, Any],
    reason_prefix: str,
) -> None:
    """Emit field/source-plane evidence for one source outcome line."""
    sk = subject_key_for_source(
        source_system=SOURCE_CHILECOMPRA,
        source_record_id=str(line.get("source_record_id") or ""),
    )
    at = line.get("first_seen_at")

    if line.get("has_raw_source") and line.get("raw_source_record_id"):
        _add_evidence(
            evidence=evidence,
            evidence_ids=evidence_ids,
            subject_kind=subject_kind,
            subject_id=subject_id,
            source_table="external_leads_raw",
            source_record_id=str(line["raw_source_record_id"]),
            evidence_type="raw_source_membership",
            reason_code=f"{reason_prefix}_raw_membership",
            subject_key=sk,
            evidence_at=at,
            detail={"join_status": line.get("raw_lead_join_status")},
        )
        if line.get("raw_json_malformed"):
            _add_evidence(
                evidence=evidence,
                evidence_ids=evidence_ids,
                subject_kind=subject_kind,
                subject_id=subject_id,
                source_table="external_leads_raw",
                source_record_id=str(line["raw_source_record_id"]),
                evidence_type="raw_json_malformed",
                reason_code="raw_json_malformed",
                subject_key=sk,
                evidence_at=at,
            )

    if line.get("has_lead_source") and line.get("lead_source_record_id"):
        _add_evidence(
            evidence=evidence,
            evidence_ids=evidence_ids,
            subject_kind=subject_kind,
            subject_id=subject_id,
            source_table="lead_master",
            source_record_id=str(line["lead_source_record_id"]),
            evidence_type="lead_source_membership",
            reason_code=f"{reason_prefix}_lead_membership",
            subject_key=sk,
            evidence_at=at,
            detail={"join_status": line.get("raw_lead_join_status")},
        )

    field_specs = (
        ("tender_key", "origin_tender_key", "tender_key", "tender_key"),
        ("title", "origin_title", "title", "title"),
        ("status_code", "origin_status_code", "status_code", "status_code"),
        ("status_name", "origin_status_name", "status_name", "status_name"),
        ("publication_date", "origin_publication_date", "publication_date", "publication_date"),
        ("close_date", "origin_close_date", "close_date", "close_date"),
        ("buyer_institution", "origin_buyer_display", "buyer_name_norm", "normalized_buyer_institution"),
        ("buyer_domain", "origin_buyer_domain", "buyer_domain", "normalized_buyer_domain"),
        ("contact_email", "origin_contact_email", "email_norm", "contact_email"),
        ("contact_email_domain", "origin_email_domain", "email_domain", "contact_email_domain"),
        ("region", "origin_region", "region", "region"),
    )
    for etype, origin_key, value_key, reason in field_specs:
        origin = str(line.get(origin_key) or "absent")
        if origin == "absent":
            continue
        if value_key == "tender_key" and not line.get("tender_key"):
            continue
        if value_key != "tender_key" and not line.get(value_key) and origin != "conflict":
            continue
        _emit_field_evidence(
            evidence=evidence,
            evidence_ids=evidence_ids,
            subject_kind=subject_kind,
            subject_id=subject_id,
            line=line,
            evidence_type=etype,
            origin=origin,
            reason_code=reason,
        )


def _default_raw_field_origin(line: dict[str, Any], present: bool) -> str:
    if not present:
        return FIELD_ORIGIN_ABSENT
    if line.get("has_raw_source"):
        return FIELD_ORIGIN_RAW
    return FIELD_ORIGIN_ABSENT


def _default_buyer_field_origin(line: dict[str, Any], present: bool) -> str:
    """Synthetic fixtures historically attributed buyer fields to lead_master when present."""
    if not present:
        return FIELD_ORIGIN_ABSENT
    if line.get("has_lead_source"):
        return FIELD_ORIGIN_LEAD
    if line.get("has_raw_source"):
        return FIELD_ORIGIN_RAW
    return FIELD_ORIGIN_ABSENT


def _normalize_source_line_provenance(line: dict[str, Any]) -> dict[str, Any]:
    """Fill source-plane provenance when callers omit explicit flags."""
    out = dict(line)
    join = str(out.get("raw_lead_join_status") or "matched")
    sid = str(out.get("source_record_id") or "").strip() or None
    if "has_raw_source" not in out:
        out["has_raw_source"] = join in {"matched", "raw_only"} and bool(
            out.get("raw_json_valid") or out.get("raw_json") is not None or join == "matched"
        )
        if join == "lead_only":
            out["has_raw_source"] = False
        if join == "raw_only":
            out["has_raw_source"] = True
        if join == "matched":
            out["has_raw_source"] = True
    if "has_lead_source" not in out:
        out["has_lead_source"] = join in {"matched", "lead_only"}
    if "raw_source_record_id" not in out:
        out["raw_source_record_id"] = sid if out.get("has_raw_source") else None
    if "lead_source_record_id" not in out:
        out["lead_source_record_id"] = sid if out.get("has_lead_source") else None
    if "raw_json_malformed" not in out:
        out["raw_json_malformed"] = bool(
            out.get("raw_json_valid") is False and join in {"matched", "raw_only"}
        )
    # Default field origins for synthetic / incomplete lines (production source load sets these).
    if "origin_title" not in out:
        out["origin_title"] = _default_raw_field_origin(out, bool(out.get("title")))
    if "origin_status_code" not in out:
        out["origin_status_code"] = _default_raw_field_origin(out, bool(out.get("status_code")))
    if "origin_status_name" not in out:
        out["origin_status_name"] = _default_raw_field_origin(out, bool(out.get("status_name")))
    if "origin_publication_date" not in out:
        out["origin_publication_date"] = _default_raw_field_origin(
            out, bool(out.get("publication_date"))
        )
    if "origin_close_date" not in out:
        out["origin_close_date"] = _default_raw_field_origin(out, bool(out.get("close_date")))
    if "origin_tender_key" not in out:
        if out.get("tender_key") and out.get("has_raw_source"):
            out["origin_tender_key"] = FIELD_ORIGIN_RAW
        elif out.get("tender_key") and out.get("has_lead_source"):
            out["origin_tender_key"] = FIELD_ORIGIN_LEAD
        else:
            out["origin_tender_key"] = FIELD_ORIGIN_ABSENT
    if "origin_buyer_display" not in out:
        out["origin_buyer_display"] = _default_buyer_field_origin(
            out, bool(out.get("buyer_display") or out.get("buyer_name_norm"))
        )
    if "origin_buyer_domain" not in out:
        out["origin_buyer_domain"] = _default_buyer_field_origin(out, bool(out.get("buyer_domain")))
    if "origin_contact_email" not in out:
        out["origin_contact_email"] = _default_buyer_field_origin(out, bool(out.get("email_norm")))
    if "origin_email_domain" not in out:
        out["origin_email_domain"] = _default_buyer_field_origin(out, bool(out.get("email_domain")))
    if "origin_region" not in out:
        out["origin_region"] = _default_buyer_field_origin(out, bool(out.get("region")))
    return out


def classify_source_outcomes(
    lines: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    verified: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for line in lines:
        if line.get("verified") and line.get("tender_key"):
            verified.append(line)
        else:
            unresolved.append(line)
    return {"verified": verified, "unresolved": unresolved, "all": list(lines)}


def plan_procurement(
    *,
    source_lines: list[dict[str, Any]],
    account_index: AccountIndex,
    identity_fingerprint: str,
    identity_fingerprint_algorithm_version: str,
    as_of_date: date,
    run_context: str,
    known_account_ids: frozenset[str] | None = None,
    materialization_stamp: str | None = None,
    generated_at_utc: str | None = None,
) -> ProcurementPlan:
    """Pure planner: source lines + PR2 index → complete immutable build plan.

    ``materialization_stamp`` is optional and only fills volatile ``created_at``
    fields for an exact-materialization digest. It must never default to wall-clock.
    Semantic digest ignores ``created_at`` entirely.
    """
    if not isinstance(as_of_date, date):
        raise TypeError("as_of_date must be a datetime.date")
    conflict_created_at = materialization_stamp or ""
    source_lines = [_normalize_source_line_provenance(x) for x in source_lines]
    pointer_registry = build_source_pointer_registry(source_lines)

    outcomes = classify_source_outcomes(source_lines)
    verified_lines = outcomes["verified"]
    unresolved_lines = outcomes["unresolved"]

    source_payloads = [source_line_semantic_payload(x) for x in outcomes["all"]]
    source_fp = procurement_source_fingerprint(source_line_payloads=source_payloads)
    build_fp = procurement_build_plan_fingerprint(
        source_fingerprint=source_fp["fingerprint"],
        identity_fingerprint=identity_fingerprint,
        as_of_date=as_of_date.isoformat(),
    )

    by_verified: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for line in verified_lines:
        by_verified[(str(line["tender_key_kind"]), str(line["tender_key"]))].append(line)

    signals: list[SignalRow] = []
    resolutions: list[AccountResolutionRow] = []
    evidence: list[EvidenceRow] = []
    conflicts: list[ConflictRow] = []
    enrichment: list[EnrichmentCandidateRow] = []
    evidence_ids: set[str] = set()
    conflict_ids: set[str] = set()
    enrichment_ids: set[str] = set()
    procurement_ids: set[str] = set()
    resolution_ids: set[str] = set()

    route_counter: Counter[str] = Counter()
    resolution_counter: Counter[str] = Counter()
    context_counter: Counter[str] = Counter()
    conflict_reason_counter: Counter[str] = Counter()
    enrichment_reason_counter: Counter[str] = Counter()
    linked_accounts: set[str] = set()
    operator_eligible_n = 0

    for line in sorted(
        unresolved_lines,
        key=lambda x: (str(x.get("source_record_id") or ""), str(x.get("first_seen_at") or "")),
    ):
        sid = str(line.get("source_record_id") or "")
        reason = (
            REASON_TENDER_KEY_UNRESOLVED
            if line.get("tender_key_kind") != TENDER_KEY_MISSING
            else REASON_TENDER_IDENTIFIER_MISSING
        )
        cid = conflict_id_for_source_row(
            source_system=SOURCE_CHILECOMPRA,
            source_record_id=sid,
            reason_code=reason,
        )
        if cid in conflict_ids:
            raise PlanValidationError(f"duplicate conflict_id {cid}")
        conflict_ids.add(cid)
        sk = subject_key_for_source(source_system=SOURCE_CHILECOMPRA, source_record_id=sid)
        conflict_reason_counter[reason] += 1
        conflicts.append(
            ConflictRow(
                conflict_id=cid,
                procurement_id=None,
                source_system=SOURCE_CHILECOMPRA,
                source_record_id=sid,
                subject_kind="unresolved_source",
                subject_key=sk,
                account_id=None,
                reason_code=reason,
                confidence=CONFIDENCE_NONE,
                detail_json=canonical_json(
                    {
                        "tender_key": line.get("tender_key") or "",
                        "tender_key_kind": line.get("tender_key_kind") or "",
                        "raw_lead_join_status": line.get("raw_lead_join_status"),
                        "has_raw_source": bool(line.get("has_raw_source")),
                        "has_lead_source": bool(line.get("has_lead_source")),
                        "raw_json_malformed": bool(line.get("raw_json_malformed")),
                    }
                ),
                created_at=conflict_created_at,
            )
        )
        _emit_line_plane_evidence(
            evidence=evidence,
            evidence_ids=evidence_ids,
            subject_kind="unresolved_source",
            subject_id=cid,
            line=line,
            reason_prefix="unresolved",
        )

    for (key_kind, tender_key), lines in sorted(by_verified.items(), key=lambda kv: kv[0]):
        agg = coalesce_verified_tender_lines(
            tender_key=tender_key,
            tender_key_kind=key_kind,
            lines=lines,
            as_of_date=as_of_date,
        )
        sig = agg["signal"]
        procurement_id = stable_procurement_id(
            source_system=SOURCE_CHILECOMPRA,
            canonical_tender_key=tender_key,
        )
        if procurement_id in procurement_ids:
            raise PlanValidationError(f"duplicate procurement_id {procurement_id}")
        procurement_ids.add(procurement_id)

        context_counter[sig["procurement_context"]] += 1
        conf = _signal_confidence(sig)
        review = "needs_review" if sig.get("line_conflicts") else "ok"
        signal_row = SignalRow(
            procurement_id=procurement_id,
            source_system=SOURCE_CHILECOMPRA,
            canonical_tender_key=tender_key,
            tender_key_kind=key_kind,
            buyer_name_raw=sig.get("buyer_display"),
            buyer_name_norm=sig.get("buyer_name_norm"),
            buyer_domain_norm=sig.get("buyer_domain"),
            buyer_email_norm=sig.get("email_norm"),
            region=sig.get("region"),
            title=sig.get("title"),
            status_code=sig.get("status_code"),
            status_name=sig.get("status_name"),
            publication_at=sig.get("publication_date_parsed"),
            close_at=sig.get("close_date_parsed"),
            procurement_context=sig["procurement_context"],
            context_reason_code=sig["context_reason_code"],
            confidence=conf,
            line_item_count=int(sig["line_item_count"]),
            constituent_source_ids_json=canonical_json(sig["constituent_source_record_ids"]),
            constituent_lines_fp=sig["constituent_semantic_lines_fingerprint"],
            first_seen_at=sig.get("first_seen_at"),
            last_seen_at=sig.get("last_seen_at"),
            review_status=review,
        )
        signals.append(signal_row)

        lines_by_sid = {
            str(x.get("source_record_id") or ""): x
            for x in lines
            if x.get("source_record_id")
        }
        for sid in sig["constituent_source_record_ids"]:
            line = lines_by_sid.get(sid)
            if line is None:
                raise PlanValidationError(f"missing constituent line for {sid}")
            _emit_line_plane_evidence(
                evidence=evidence,
                evidence_ids=evidence_ids,
                subject_kind="signal",
                subject_id=procurement_id,
                line=line,
                reason_prefix=(
                    "coalesced" if int(sig["line_item_count"]) > 1 else "verified"
                ),
            )

        for c in agg["conflicts"]:
            field = str(c.get("field") or "")
            cid = stable_conflict_id_for_signal(
                procurement_id=procurement_id,
                reason_code=REASON_LINE_FIELD_CONFLICT,
                detail_key=field,
            )
            if cid not in conflict_ids:
                conflict_ids.add(cid)
                conflict_reason_counter[REASON_LINE_FIELD_CONFLICT] += 1
                conflicts.append(
                    ConflictRow(
                        conflict_id=cid,
                        procurement_id=procurement_id,
                        source_system=SOURCE_CHILECOMPRA,
                        source_record_id=None,
                        subject_kind="line_conflict",
                        subject_key=None,
                        account_id=None,
                        reason_code=REASON_LINE_FIELD_CONFLICT,
                        confidence="medium",
                        detail_json=canonical_json(c),
                        created_at=conflict_created_at,
                    )
                )

        result = classify_account_link_route(
            index=account_index,
            buyer_name_norm=sig.get("buyer_name_norm"),
            buyer_domain=sig.get("buyer_domain"),
            email_domain=sig.get("email_domain"),
            email_norm=sig.get("email_norm"),
            weak_public_unit_name=bool(sig.get("weak_public_unit_name")),
        )
        resolution = build_account_resolution(
            procurement_id=procurement_id, result=result
        )
        assert_resolution_invariants(resolution)
        if resolution.resolution_id in resolution_ids:
            raise PlanValidationError(f"duplicate resolution_id {resolution.resolution_id}")
        resolution_ids.add(resolution.resolution_id)
        route_counter[result.route] += 1
        resolution_counter[resolution.resolution_status] += 1
        linked = resolution.resolution_status == RESOLUTION_LINKED
        if linked and resolution.account_id:
            linked_accounts.add(resolution.account_id)
            if known_account_ids is not None and resolution.account_id not in known_account_ids:
                raise PlanValidationError(
                    f"linked account_id {resolution.account_id} missing from PR2 identity"
                )
        if known_account_ids is not None:
            for cand in resolution.candidate_account_ids:
                if cand not in known_account_ids:
                    raise PlanValidationError(
                        f"candidate account_id {cand} missing from PR2 identity"
                    )

        resolutions.append(
            AccountResolutionRow(
                resolution_id=resolution.resolution_id,
                procurement_id=procurement_id,
                resolution_status=resolution.resolution_status,
                account_id=resolution.account_id,
                link_route=resolution.link_route,
                confidence=resolution.confidence,
                reason_code=resolution.reason_code,
                auto_link_allowed=int(resolution.auto_link_allowed),
                review_status=resolution.review_status,
                candidate_account_ids_json=canonical_json(list(resolution.candidate_account_ids)),
            )
        )

        _add_evidence(
            evidence=evidence,
            evidence_ids=evidence_ids,
            subject_kind="resolution",
            subject_id=resolution.resolution_id,
            source_table="commercial_identity_account",
            source_record_id=resolution.account_id or "none",
            evidence_type="account_resolution",
            reason_code=resolution.reason_code,
            detail={
                "link_route": resolution.link_route,
                "resolution_status": resolution.resolution_status,
                "auxiliary_reason_codes": list(resolution.auxiliary_reason_codes),
                "notes": resolution.notes,
            },
        )

        has_ambiguity = result.route in {
            ROUTE_AMBIGUOUS_MULTI_ACCOUNT,
            ROUTE_NAME_DOMAIN_CONFLICT,
        }
        if has_ambiguity:
            cid = stable_conflict_id_for_signal(
                procurement_id=procurement_id,
                reason_code=resolution.reason_code,
                detail_key=result.route,
            )
            if cid not in conflict_ids:
                conflict_ids.add(cid)
                conflict_reason_counter[resolution.reason_code] += 1
                conflicts.append(
                    ConflictRow(
                        conflict_id=cid,
                        procurement_id=procurement_id,
                        source_system=SOURCE_CHILECOMPRA,
                        source_record_id=None,
                        subject_kind="resolution_conflict",
                        subject_key=None,
                        account_id=None,
                        reason_code=resolution.reason_code,
                        confidence=resolution.confidence,
                        detail_json=canonical_json(
                            {
                                "link_route": result.route,
                                "candidate_account_ids": list(resolution.candidate_account_ids),
                            }
                        ),
                        created_at=conflict_created_at,
                    )
                )

        reasons: list[str] = []
        if not linked:
            reasons.append(result.reason_code or REASON_BUYER_ACCOUNT_NOT_FOUND)
        reasons.extend(result.auxiliary_reason_codes)
        if sig.get("line_conflicts"):
            reasons.append(REASON_LINE_FIELD_CONFLICT)

        for reason in sorted(set(reasons)):
            research = _research_field_for_reason(reason)
            priority = 0
            if int(sig["line_item_count"]) > 1:
                priority += 1
            if sig.get("buyer_domain") or sig.get("email_norm"):
                priority += 1
            if sig["procurement_context"] in OPERATOR_ELIGIBLE_CONTEXTS:
                priority += 2
            eligible = _operator_eligible(
                procurement_context=sig["procurement_context"],
                linked=linked,
                has_ambiguity=has_ambiguity,
                status_code=sig.get("status_code"),
            )
            if (
                not linked
                and sig["procurement_context"] == PROCUREMENT_CONTEXT_HISTORICAL
                and not has_ambiguity
                and reason == (result.reason_code or REASON_BUYER_ACCOUNT_NOT_FOUND)
            ):
                eligible = False
            qid = stable_enrichment_candidate_id(
                subject_id=procurement_id,
                reason_code=reason,
                research_field=research,
            )
            if qid in enrichment_ids:
                continue
            enrichment_ids.add(qid)
            enrichment_reason_counter[reason] += 1
            if eligible:
                operator_eligible_n += 1
            enrichment.append(
                EnrichmentCandidateRow(
                    candidate_id=qid,
                    procurement_id=procurement_id,
                    source_system=SOURCE_CHILECOMPRA,
                    source_record_id=None,
                    buyer_name_raw=sig.get("buyer_display"),
                    account_id=None,
                    reason_code=reason,
                    confidence=resolution.confidence,
                    recommended_research_field=research,
                    priority=priority,
                    operator_queue_eligible=int(eligible),
                    candidate_account_ids_json=(
                        canonical_json(list(resolution.candidate_account_ids))
                        if resolution.candidate_account_ids
                        else None
                    ),
                )
            )

    signals_t = tuple(sorted(signals, key=lambda r: r.procurement_id))
    resolutions_t = tuple(sorted(resolutions, key=lambda r: r.resolution_id))
    evidence_t = tuple(sorted(evidence, key=lambda r: r.evidence_id))
    conflicts_t = tuple(sorted(conflicts, key=lambda r: r.conflict_id))
    enrichment_t = tuple(sorted(enrichment, key=lambda r: r.candidate_id))

    if len(resolutions_t) != len(signals_t):
        raise PlanValidationError("resolution cardinality must equal signal count")

    metrics: dict[str, Any] = {
        "source_outcome_count": len(outcomes["all"]),
        "verified_source_line_count": len(verified_lines),
        "unresolved_source_row_count": len(unresolved_lines),
        "signal_count": len(signals_t),
        "resolution_count": len(resolutions_t),
        "resolution_distribution": dict(sorted(resolution_counter.items())),
        "route_distribution": dict(sorted(route_counter.items())),
        "procurement_context_distribution": dict(sorted(context_counter.items())),
        "evidence_count": len(evidence_t),
        "conflict_count": len(conflicts_t),
        "conflict_distribution": dict(sorted(conflict_reason_counter.items())),
        "enrichment_candidate_count": len(enrichment_t),
        "enrichment_distribution": dict(sorted(enrichment_reason_counter.items())),
        "operator_queue_eligible_count": operator_eligible_n,
        "unique_linked_accounts": len(linked_accounts),
        "linked_resolutions": resolution_counter.get(RESOLUTION_LINKED, 0),
        "unlinked_resolutions": resolution_counter.get("unlinked", 0),
        "ambiguous_resolutions": resolution_counter.get("ambiguous", 0),
        "refused_resolutions": resolution_counter.get("refused", 0),
    }

    semantic_rows = {
        "commercial_procurement_signal": [r.to_db_row() for r in signals_t],
        "commercial_procurement_account_resolution": [r.to_db_row() for r in resolutions_t],
        "commercial_procurement_evidence": [r.to_db_row() for r in evidence_t],
        "commercial_procurement_conflict": [r.to_db_row() for r in conflicts_t],
        "commercial_procurement_enrichment_candidate": [r.to_db_row() for r in enrichment_t],
    }
    digest = semantic_plan_digest(table_rows=semantic_rows)

    meta_pairs = [
        ("schema_version", SCHEMA_VERSION),
        ("build_contract", BUILD_CONTRACT),
        ("resolver_build_contract_version", RESOLVER_BUILD_CONTRACT_VERSION),
        ("transaction_contract", TRANSACTION_CONTRACT),
        ("as_of_date", as_of_date.isoformat()),
        ("as_of_timezone", "UTC_calendar_date"),
        ("run_context", run_context),
        ("mode", "dry-run"),
        ("applied", "false"),
        ("source_fingerprint_algorithm", source_fp["algorithm"]),
        ("source_fingerprint", source_fp["fingerprint"]),
        ("source_fingerprint_components_json", canonical_json(source_fp["components"])),
        ("build_plan_fingerprint_algorithm", build_fp["algorithm"]),
        ("build_plan_fingerprint", build_fp["fingerprint"]),
        ("identity_fingerprint_algorithm_version", identity_fingerprint_algorithm_version),
        ("identity_fingerprint", identity_fingerprint),
        ("semantic_plan_digest_algorithm", PROCUREMENT_SEMANTIC_PLAN_DIGEST_ALGORITHM),
        ("semantic_plan_digest", digest),
        ("metrics_json", canonical_json(metrics)),
    ]
    if generated_at_utc:
        meta_pairs.append(("generated_at_utc", generated_at_utc))

    mat_digest: str | None = None
    if materialization_stamp:
        full_rows = dict(semantic_rows)
        full_rows["commercial_procurement_build_meta"] = [
            {"meta_key": k, "meta_value": v} for k, v in meta_pairs
        ]
        mat_digest = materialization_digest(table_rows=full_rows)
        meta_pairs.append(
            ("materialization_digest_algorithm", PROCUREMENT_MATERIALIZATION_DIGEST_ALGORITHM)
        )
        meta_pairs.append(("materialization_digest", mat_digest))
        meta_pairs.append(("materialization_stamp", materialization_stamp))

    build_meta_final = tuple(
        sorted(
            [BuildMetaRow(meta_key=k, meta_value=v) for k, v in meta_pairs],
            key=lambda r: r.meta_key,
        )
    )

    return ProcurementPlan(
        signals=signals_t,
        resolutions=resolutions_t,
        evidence=evidence_t,
        conflicts=conflicts_t,
        enrichment_candidates=enrichment_t,
        build_meta=build_meta_final,
        source_fingerprint=source_fp["fingerprint"],
        source_fingerprint_components=source_fp["components"],
        build_plan_fingerprint=build_fp["fingerprint"],
        semantic_plan_digest=digest,
        identity_fingerprint=identity_fingerprint,
        identity_fingerprint_algorithm_version=identity_fingerprint_algorithm_version,
        as_of_date=as_of_date.isoformat(),
        run_context=run_context,
        metrics=metrics,
        generated_at_utc=generated_at_utc,
        materialization_digest=mat_digest,
        source_pointer_registry=pointer_registry,
    )


def plan_procurement_from_connection(
    *,
    conn: sqlite3.Connection,
    as_of_date: date,
    run_context: str,
    materialization_stamp: str | None = None,
    generated_at_utc: str | None = None,
) -> tuple[ProcurementPlan, dict[str, Any]]:
    """Load sources + identity from an open read-only connection and plan.

    Returns ``(plan, snapshot_meta)`` where snapshot_meta includes PR3 sentinel
    and optional data_version diagnostics captured inside the caller's txn.
    """
    meta = load_identity_fingerprint_meta(conn)
    identity_fp, identity_algo = _require_identity_fp_v2(meta)
    lines = load_chilecompra_source_lines(conn)
    index = load_pr2_account_index(conn)
    known = load_known_account_ids(conn)
    pr3 = load_pr3_immutability_sentinel(conn)
    plan = plan_procurement(
        source_lines=lines,
        account_index=index,
        identity_fingerprint=identity_fp,
        identity_fingerprint_algorithm_version=identity_algo,
        as_of_date=as_of_date,
        run_context=run_context,
        known_account_ids=known,
        materialization_stamp=materialization_stamp,
        generated_at_utc=generated_at_utc,
    )
    return plan, {"known_account_ids": known, "pr3_sentinel": pr3}


__all__ = [
    "IdentityGateError",
    "PlanValidationError",
    "SourceSchemaError",
    "classify_source_outcomes",
    "plan_procurement",
    "plan_procurement_from_connection",
]
