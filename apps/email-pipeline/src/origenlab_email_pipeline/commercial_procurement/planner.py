"""Deterministic commercial procurement build planner (PR4) — dry-run only.

Produces the exact immutable rows a later persistence PR would insert.
Never mutates production SQLite, Gmail, or Postgres.
"""

from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from typing import Any

from origenlab_email_pipeline.commercial_procurement.coalesce import (
    coalesce_verified_tender_lines,
)
from origenlab_email_pipeline.commercial_procurement.constants import (
    BUILD_CONTRACT,
    CONFIDENCE_NONE,
    OPERATOR_ELIGIBLE_CONTEXTS,
    PROCUREMENT_PLAN_DIGEST_ALGORITHM,
    PROCUREMENT_CONTEXT_HISTORICAL,
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
    plan_digest,
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
from origenlab_email_pipeline.commercial_procurement.resolution import (
    assert_resolution_invariants,
    build_account_resolution,
)
from origenlab_email_pipeline.commercial_procurement.sources import (
    SourceSchemaError,
    load_chilecompra_source_lines,
    load_identity_fingerprint_meta,
    load_pr2_account_index,
)


class IdentityGateError(ValueError):
    """Persisted PR2 identity snapshot missing or wrong algorithm."""


class PlanValidationError(ValueError):
    """Internal plan invariants failed."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


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
    build_stamp: str | None = None,
) -> ProcurementPlan:
    """Pure planner: source lines + PR2 index → complete immutable build plan."""
    if not isinstance(as_of_date, date):
        raise TypeError("as_of_date must be a datetime.date")
    stamp = build_stamp or _utc_now_iso()
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

    # Unresolved source rows → conflicts + evidence (no signal)
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
                    }
                ),
                created_at=stamp,
            )
        )
        eid = stable_evidence_id(
            subject_kind="unresolved_source",
            subject_id=cid,
            source_table=(
                "lead_master"
                if line.get("raw_lead_join_status") != "raw_only"
                else "external_leads_raw"
            ),
            source_record_id=sid,
            evidence_type="tender_key",
            reason_code=reason,
        )
        if eid not in evidence_ids:
            evidence_ids.add(eid)
            evidence.append(
                EvidenceRow(
                    evidence_id=eid,
                    subject_kind="unresolved_source",
                    subject_id=cid,
                    source_system=SOURCE_CHILECOMPRA,
                    source_table=(
                        "lead_master"
                        if line.get("raw_lead_join_status") != "raw_only"
                        else "external_leads_raw"
                    ),
                    source_record_id=sid,
                    subject_key=sk,
                    evidence_type="tender_key",
                    evidence_at=line.get("first_seen_at"),
                    reason_code=reason,
                    detail_json=None,
                )
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

        # Constituent evidence
        for sid in sig["constituent_source_record_ids"]:
            eid = stable_evidence_id(
                subject_kind="signal",
                subject_id=procurement_id,
                source_table="lead_master",
                source_record_id=sid,
                evidence_type="constituent_source",
                reason_code="line_items_coalesced_to_tender"
                if int(sig["line_item_count"]) > 1
                else "verified_tender_source",
            )
            if eid not in evidence_ids:
                evidence_ids.add(eid)
                evidence.append(
                    EvidenceRow(
                        evidence_id=eid,
                        subject_kind="signal",
                        subject_id=procurement_id,
                        source_system=SOURCE_CHILECOMPRA,
                        source_table="lead_master",
                        source_record_id=sid,
                        subject_key=subject_key_for_source(
                            source_system=SOURCE_CHILECOMPRA, source_record_id=sid
                        ),
                        evidence_type="constituent_source",
                        evidence_at=sig.get("first_seen_at"),
                        reason_code=(
                            "line_items_coalesced_to_tender"
                            if int(sig["line_item_count"]) > 1
                            else "verified_tender_source"
                        ),
                        detail_json=None,
                    )
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
                        created_at=stamp,
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

        eid = stable_evidence_id(
            subject_kind="resolution",
            subject_id=resolution.resolution_id,
            source_table="commercial_identity_account",
            source_record_id=resolution.account_id or "none",
            evidence_type="account_resolution",
            reason_code=resolution.reason_code,
        )
        if eid not in evidence_ids:
            evidence_ids.add(eid)
            evidence.append(
                EvidenceRow(
                    evidence_id=eid,
                    subject_kind="resolution",
                    subject_id=resolution.resolution_id,
                    source_system=SOURCE_CHILECOMPRA,
                    source_table="commercial_identity_account",
                    source_record_id=resolution.account_id or "none",
                    subject_key=None,
                    evidence_type="account_resolution",
                    evidence_at=None,
                    reason_code=resolution.reason_code,
                    detail_json=canonical_json(
                        {
                            "link_route": resolution.link_route,
                            "resolution_status": resolution.resolution_status,
                            "auxiliary_reason_codes": list(resolution.auxiliary_reason_codes),
                            "notes": resolution.notes,
                        }
                    ),
                )
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
                        created_at=stamp,
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

    # Sort for stable plan ordering
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
        ("plan_digest_algorithm", PROCUREMENT_PLAN_DIGEST_ALGORITHM),
        ("metrics_json", canonical_json(metrics)),
    ]
    # Digest excludes plan_digest meta value to avoid circularity.
    digest_input = {
        "commercial_procurement_signal": [r.to_db_row() for r in signals_t],
        "commercial_procurement_account_resolution": [r.to_db_row() for r in resolutions_t],
        "commercial_procurement_evidence": [r.to_db_row() for r in evidence_t],
        "commercial_procurement_conflict": [r.to_db_row() for r in conflicts_t],
        "commercial_procurement_enrichment_candidate": [r.to_db_row() for r in enrichment_t],
        "commercial_procurement_build_meta": [
            {"meta_key": k, "meta_value": v} for k, v in meta_pairs
        ],
    }
    digest = plan_digest(table_rows=digest_input, algorithm=PROCUREMENT_PLAN_DIGEST_ALGORITHM)
    build_meta_final = tuple(
        sorted(
            [BuildMetaRow(meta_key=k, meta_value=v) for k, v in meta_pairs]
            + [BuildMetaRow(meta_key="plan_digest", meta_value=digest)],
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
        plan_digest=digest,
        identity_fingerprint=identity_fingerprint,
        identity_fingerprint_algorithm_version=identity_fingerprint_algorithm_version,
        as_of_date=as_of_date.isoformat(),
        run_context=run_context,
        metrics=metrics,
    )


def plan_procurement_from_connection(
    *,
    conn: sqlite3.Connection,
    as_of_date: date,
    run_context: str,
    build_stamp: str | None = None,
) -> ProcurementPlan:
    """Load sources + identity from an open read-only connection and plan."""
    meta = load_identity_fingerprint_meta(conn)
    identity_fp, identity_algo = _require_identity_fp_v2(meta)
    lines = load_chilecompra_source_lines(conn)
    index = load_pr2_account_index(conn)
    known = frozenset(
        r["account_id"]
        for r in conn.execute("SELECT account_id FROM commercial_identity_account")
    )
    return plan_procurement(
        source_lines=lines,
        account_index=index,
        identity_fingerprint=identity_fp,
        identity_fingerprint_algorithm_version=identity_algo,
        as_of_date=as_of_date,
        run_context=run_context,
        known_account_ids=known,
        build_stamp=build_stamp,
    )


__all__ = [
    "IdentityGateError",
    "PlanValidationError",
    "SourceSchemaError",
    "classify_source_outcomes",
    "plan_procurement",
    "plan_procurement_from_connection",
]
