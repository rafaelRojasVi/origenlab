"""Plane A — read-only PR4 commercial_procurement_* evidence."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.commercial_identity.builder import (
    CommercialIdentityPathError,
    require_explicit_sqlite_path,
)
from origenlab_email_pipeline.commercial_procurement.builder import (
    _read_semantic_rows_from_db,
)
from origenlab_email_pipeline.commercial_procurement.constants import (
    BUILD_CONTRACT,
    SCHEMA_VERSION,
)
from origenlab_email_pipeline.commercial_procurement.ids import semantic_plan_digest
from origenlab_email_pipeline.commercial_procurement.schema import (
    SchemaIncompatibilityError,
    assert_schema_compatible,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.constants import (
    EVIDENCE_PLANE_PR4,
    REQUIRED_PR4_META_KEYS,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
    Pr4PlaneBundle,
    ProcurementEvidenceRef,
    UnresolvedProcurementEvidence,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.normalize import (
    accept_pr4_signal_identity,
    normalize_tender_timestamp,
    normalized_status_meaning,
    stable_content_id,
)


class Pr4PlaneError(ValueError):
    """Incompatible or incomplete PR4 plane."""


def open_pr4_readonly(sqlite_path: Path) -> sqlite3.Connection:
    path = require_explicit_sqlite_path(sqlite_path)
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def _rows(conn: sqlite3.Connection, sql: str) -> tuple[dict[str, Any], ...]:
    return tuple(dict(r) for r in conn.execute(sql))


def _verify_pr4_contents(conn: sqlite3.Connection, build_meta: dict[str, str]) -> str:
    """Recompute persisted semantic digest and validate grain pointers."""
    semantic_rows = _read_semantic_rows_from_db(conn)
    pk_map = {
        "commercial_procurement_signal": "procurement_id",
        "commercial_procurement_account_resolution": "resolution_id",
        "commercial_procurement_evidence": "evidence_id",
        "commercial_procurement_conflict": "conflict_id",
        "commercial_procurement_enrichment_candidate": "candidate_id",
    }
    for table, rows in list(semantic_rows.items()):
        if table not in pk_map:
            continue
        semantic_rows[table] = sorted(rows, key=lambda r: str(r[pk_map[table]]))

    readback = semantic_plan_digest(table_rows=semantic_rows)
    expected = build_meta["semantic_plan_digest"]
    if readback != expected:
        raise Pr4PlaneError(
            "persisted PR4 semantic digest mismatch vs build_meta.semantic_plan_digest"
        )

    signals = semantic_rows.get("commercial_procurement_signal") or []
    resolutions = semantic_rows.get("commercial_procurement_account_resolution") or []
    if len(signals) != len(resolutions):
        raise Pr4PlaneError("PR4 signal/resolution cardinality mismatch")

    proc_ids = [str(s["procurement_id"]) for s in signals]
    if len(proc_ids) != len(set(proc_ids)):
        raise Pr4PlaneError("duplicate PR4 procurement_id")

    grains = [
        (str(s["source_system"]), str(s["canonical_tender_key"])) for s in signals
    ]
    if len(grains) != len(set(grains)):
        raise Pr4PlaneError("duplicate PR4 canonical tender grain")

    res_by_proc = [str(r["procurement_id"]) for r in resolutions]
    if len(res_by_proc) != len(set(res_by_proc)):
        raise Pr4PlaneError("duplicate PR4 account_resolution procurement_id")
    signal_set = set(proc_ids)
    for pid in res_by_proc:
        if pid not in signal_set:
            raise Pr4PlaneError("account_resolution procurement_id missing from signals")

    for ev in semantic_rows.get("commercial_procurement_evidence") or []:
        kind = str(ev.get("subject_kind") or "")
        sid = str(ev.get("subject_id") or "")
        if kind == "signal" and sid not in signal_set:
            raise Pr4PlaneError("PR4 evidence subject signal missing")
        if kind == "resolution":
            if not any(str(r["resolution_id"]) == sid for r in resolutions):
                raise Pr4PlaneError("PR4 evidence subject resolution missing")

    for conf in semantic_rows.get("commercial_procurement_conflict") or []:
        pid = conf.get("procurement_id")
        if pid is not None and str(pid) not in signal_set:
            raise Pr4PlaneError("PR4 conflict procurement_id missing")

    return readback


def _identity_diagnostic(signals: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    """Redacted breakdown of Plane A identity / cross-source eligibility."""
    kind_counts: Counter[str] = Counter()
    empty_counts: Counter[str] = Counter()
    shape_counts: Counter[str] = Counter()
    length_bucket: Counter[str] = Counter()
    delim: Counter[str] = Counter()
    reject: Counter[str] = Counter()
    for sig in signals:
        kind = str(sig.get("tender_key_kind") or "")
        kind_counts[kind or "missing"] += 1
        key, _kind, reason, cross, _ns = accept_pr4_signal_identity(
            raw_key=sig.get("canonical_tender_key"),
            tender_key_kind=sig.get("tender_key_kind"),
        )
        if key is None:
            empty_counts["empty_or_rejected"] += 1
            reject[reason or "unknown"] += 1
            length_bucket["n/a"] += 1
            delim["n/a"] += 1
            shape_counts["n/a"] += 1
            continue
        empty_counts["nonempty"] += 1
        shape_counts["mp_shape" if cross else "not_mp_shape"] += 1
        n = len(key)
        if n <= 8:
            length_bucket["1-8"] += 1
        elif n <= 16:
            length_bucket["9-16"] += 1
        elif n <= 24:
            length_bucket["17-24"] += 1
        else:
            length_bucket["25+"] += 1
        parts = key.split("-")
        delim[f"hyphen_parts_{len(parts)}"] += 1
        if cross:
            reject["accepted_cross_source_eligible"] += 1
        else:
            reject["accepted_pr4_only_not_mp_regex"] += 1
            if len(parts) == 3:
                a, b, c = parts
                letters = "".join(ch for ch in c if ch.isalpha())
                digits = "".join(ch for ch in c if ch.isdigit())
                delim[
                    f"suffix_letters_{len(letters)}_digits_{len(digits)}"
                ] += 1
    return {
        "tender_key_kind": dict(kind_counts),
        "empty_vs_nonempty": dict(empty_counts),
        "exact_mp_codigo_externo_shape": dict(shape_counts),
        "normalized_length_bucket": dict(length_bucket),
        "delimiter_component_shape": dict(sorted(delim.items())),
        "acceptance_reason": dict(reject),
        "note": (
            "Former 1758-row gap was verified codigo_externo keys whose suffix "
            "digit length is 3 (not the stricter PR5B \\d{2} CodigoExterno regex). "
            "They remain valid Plane A pr4_only tenders."
        ),
    }


def load_pr4_plane(sqlite_path: Path) -> Pr4PlaneBundle:
    conn = open_pr4_readonly(sqlite_path)
    try:
        schema = assert_schema_compatible(conn)
        if schema.get("status") == "absent":
            raise Pr4PlaneError("PR4 commercial_procurement schema absent")
        meta_rows = _rows(
            conn, "SELECT meta_key, meta_value FROM commercial_procurement_build_meta"
        )
        build_meta = {str(r["meta_key"]): str(r["meta_value"]) for r in meta_rows}
        missing = [k for k in REQUIRED_PR4_META_KEYS if k not in build_meta or not build_meta[k]]
        if missing:
            raise Pr4PlaneError(f"missing required PR4 fingerprints/meta: {missing}")
        if build_meta.get("schema_version") != SCHEMA_VERSION:
            raise Pr4PlaneError(
                f"incompatible PR4 schema_version={build_meta.get('schema_version')!r}"
            )
        if build_meta.get("build_contract") != BUILD_CONTRACT:
            raise Pr4PlaneError(
                f"incompatible PR4 build_contract={build_meta.get('build_contract')!r}"
            )
        readback = _verify_pr4_contents(conn, build_meta)
        signals = _rows(
            conn,
            "SELECT * FROM commercial_procurement_signal ORDER BY procurement_id",
        )
        resolutions = _rows(
            conn,
            "SELECT * FROM commercial_procurement_account_resolution "
            "ORDER BY resolution_id",
        )
        evidence = _rows(
            conn,
            "SELECT * FROM commercial_procurement_evidence ORDER BY evidence_id",
        )
        conflicts = _rows(
            conn,
            "SELECT * FROM commercial_procurement_conflict ORDER BY conflict_id",
        )
        enrichment = _rows(
            conn,
            "SELECT * FROM commercial_procurement_enrichment_candidate "
            "ORDER BY candidate_id",
        )
        return Pr4PlaneBundle(
            signals=signals,
            account_resolutions=resolutions,
            evidence_rows=evidence,
            conflict_rows=conflicts,
            enrichment_rows=enrichment,
            build_meta=build_meta,
            source_fingerprint=build_meta["source_fingerprint"],
            build_plan_fingerprint=build_meta["build_plan_fingerprint"],
            semantic_plan_digest=build_meta["semantic_plan_digest"],
            readback_semantic_digest=readback,
            schema_version=build_meta["schema_version"],
            build_contract=build_meta["build_contract"],
            identity_fingerprint=build_meta["identity_fingerprint"],
            as_of_date=build_meta["as_of_date"],
            schema_status=str(schema.get("status") or "ok"),
            identity_diagnostic=_identity_diagnostic(signals),
        )
    except SchemaIncompatibilityError as exc:
        raise Pr4PlaneError(str(exc)) from exc
    except CommercialIdentityPathError:
        raise
    finally:
        conn.close()


def pr4_signals_to_evidence(
    pr4: Pr4PlaneBundle,
) -> tuple[list[ProcurementEvidenceRef], list[UnresolvedProcurementEvidence]]:
    """Map every PR4 signal to an evidence ref or typed unresolved row."""
    resolution_by_proc = {
        str(r["procurement_id"]): r for r in pr4.account_resolutions
    }
    evidence_by_proc: dict[str, list[str]] = defaultdict(list)
    for ev in pr4.evidence_rows:
        if str(ev.get("subject_kind") or "") == "signal":
            evidence_by_proc[str(ev.get("subject_id") or "")].append(
                str(ev.get("evidence_id") or "")
            )
    conflict_by_proc: dict[str, list[str]] = defaultdict(list)
    for conf in pr4.conflict_rows:
        pid = conf.get("procurement_id")
        if pid is not None:
            conflict_by_proc[str(pid)].append(str(conf.get("conflict_id") or ""))

    refs: list[ProcurementEvidenceRef] = []
    unresolved: list[UnresolvedProcurementEvidence] = []

    for sig in pr4.signals:
        proc_id = str(sig["procurement_id"])
        key, kind, reason, cross, namespace = accept_pr4_signal_identity(
            raw_key=sig.get("canonical_tender_key"),
            tender_key_kind=sig.get("tender_key_kind"),
        )
        if key is None or kind is None or reason is not None or namespace is None:
            unresolved.append(
                UnresolvedProcurementEvidence(
                    unresolved_id=stable_content_id(
                        "unresolved",
                        {
                            "plane": EVIDENCE_PLANE_PR4,
                            "procurement_id": proc_id,
                            "reason": reason or "pr4_canonical_identity_corrupt",
                        },
                    ),
                    evidence_plane=EVIDENCE_PLANE_PR4,
                    source_kind="pr4",
                    endpoint_kind="pr4_persisted_signal",
                    source_record_id=proc_id,
                    snapshot_id=None,
                    acquisition_instance_id=None,
                    observation_id=None,
                    unresolved_reason=reason or "pr4_canonical_identity_corrupt",
                    canonical_candidate_kind=None,
                    canonical_tender_key_candidate=None,
                    source_native_tender_key=None,
                    tender_key_kind=kind,
                    identity_namespace=None,
                    pr4_procurement_id=proc_id,
                    reason_codes=(reason or "pr4_canonical_identity_corrupt",),
                    source_payload_digest=sig.get("constituent_lines_fp"),
                    acquired_at_utc=None,
                )
            )
            continue

        res = resolution_by_proc.get(proc_id)
        constituents_raw = sig.get("constituent_source_ids_json") or "[]"
        try:
            source_ids = json.loads(constituents_raw)
        except json.JSONDecodeError:
            source_ids = []
        if not isinstance(source_ids, list):
            source_ids = []
        constituent_ids = tuple(str(x) for x in source_ids if str(x).strip())
        source_record_id = constituent_ids[0] if constituent_ids else proc_id

        pub_raw = sig.get("publication_at")
        close_raw = sig.get("close_at")
        pub_nt = normalize_tender_timestamp(pub_raw)
        close_nt = normalize_tender_timestamp(close_raw)
        ts_reasons = tuple(
            r for r in (pub_nt.reason, close_nt.reason) if r
        )
        status_code = sig.get("status_code")
        status_name = sig.get("status_name")

        ref_id = stable_content_id(
            "evidence_ref",
            {
                "plane": EVIDENCE_PLANE_PR4,
                "procurement_id": proc_id,
                "identity_namespace": namespace,
                "canonical_tender_key": key,
                "tender_key_kind": kind,
                "source_fingerprint": pr4.source_fingerprint,
            },
        )
        refs.append(
            ProcurementEvidenceRef(
                evidence_ref_id=ref_id,
                evidence_plane=EVIDENCE_PLANE_PR4,
                source_kind="pr4",
                endpoint_kind="pr4_persisted_signal",
                source_record_id=source_record_id,
                canonical_tender_key=key,
                tender_key_kind=kind,
                identity_namespace=namespace,
                cross_source_join_eligible=cross,
                snapshot_id=None,
                acquisition_instance_id=None,
                page_id=None,
                observation_id=None,
                acquired_at_utc=None,
                source_status_code=status_code,
                source_status_name=status_name,
                source_status_value=status_name,
                source_status_system="chilecompra_pr4",
                normalized_status_meaning=normalized_status_meaning(
                    status_code, status_name
                ),
                publication_timestamp_raw=pub_raw,
                close_timestamp_raw=close_raw,
                publication_timestamp_utc=pub_nt.utc_iso,
                close_timestamp_utc=close_nt.utc_iso,
                publication_santiago_date=(
                    pub_nt.santiago_date.isoformat() if pub_nt.santiago_date else None
                ),
                close_santiago_date=(
                    close_nt.santiago_date.isoformat() if close_nt.santiago_date else None
                ),
                publication_precision=pub_nt.precision if pub_raw else None,
                close_precision=close_nt.precision if close_raw else None,
                timestamp_parse_reasons=ts_reasons,
                buyer_display_raw=sig.get("buyer_name_raw"),
                buyer_source_id=sig.get("buyer_domain_norm"),
                title_raw=sig.get("title"),
                source_payload_digest=sig.get("constituent_lines_fp"),
                source_fingerprint=pr4.source_fingerprint,
                normalized_semantic_digest=pr4.semantic_plan_digest,
                field_provenance={
                    "status": "pr4_signal",
                    "close": "pr4_signal",
                    "publication": "pr4_signal",
                    "buyer_display": "pr4_signal",
                    "buyer_source_id": "pr4_signal",
                    "title": "pr4_signal",
                    "account_resolution_pointer": (
                        str(res["resolution_id"]) if res else "absent"
                    ),
                },
                reason_codes=(
                    "pr4_persisted_signal",
                    *(
                        ("pr4_not_cross_source_mp_shape",)
                        if not cross
                        else ("pr4_cross_source_eligible",)
                    ),
                ),
                source_rank_class="pr4",
                has_status=bool(status_code or status_name),
                has_close=bool(close_raw),
                has_publication=bool(pub_raw),
                has_buyer_display=bool(sig.get("buyer_name_raw")),
                has_buyer_source_id=bool(sig.get("buyer_domain_norm")),
                has_title=bool(sig.get("title")),
                pr4_procurement_id=proc_id,
                pr4_account_resolution_id=(
                    str(res["resolution_id"]) if res else None
                ),
                pr4_evidence_ids=tuple(sorted(evidence_by_proc.get(proc_id, []))),
                pr4_conflict_ids=tuple(sorted(conflict_by_proc.get(proc_id, []))),
                constituent_source_ids=constituent_ids,
                page_completeness="complete",
            )
        )
    return refs, unresolved
