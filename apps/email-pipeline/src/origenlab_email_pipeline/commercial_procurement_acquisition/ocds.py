"""Official ChileCompra OCDS package parser and monthly assembler (no network).

Records policy B:
- Prefer historical releases when present.
- Emit compiledRelease only when its (ocid, release.id) is not already among
  historical releases.
- Never silently flatten compiled and nested releases into one indistinguishable list.
"""

from __future__ import annotations

from typing import Any

from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.constants import (
    ACQUISITION_CONTRACT_VERSION,
    ENDPOINT_OCDS_MONTHLY_MONTH,
    ENDPOINT_OCDS_MONTHLY_RANGE,
    ENDPOINT_PATH_OCDS_MONTH_TEMPLATE,
    ENDPOINT_PATH_OCDS_TEMPLATE,
    OCDS_MAX_PAGE_SIZE,
    PARSER_VERSION,
    QUERY_CONTRACT_VERSION,
    RELEASE_KIND_COMPILED,
    RELEASE_KIND_HISTORICAL,
    SOURCE_KIND_OCDS,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.fingerprint import (
    acquisition_page_id,
    acquisition_query_id,
    acquisition_source_fingerprint,
    acquisition_normalized_semantic_digest,
    payload_digests,
    procurement_line_observation_id,
    procurement_snapshot_id,
    procurement_source_observation_id,
    procurement_tender_observation_id,
    query_identity_from_model,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.identity import (
    ocds_canonical_candidate,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.models import (
    AcquisitionPage,
    AcquisitionQuery,
    AcquisitionSnapshot,
    ProcurementLineObservation,
    ProcurementSourceObservation,
    ProcurementTenderObservation,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.redaction import (
    sanitize_error_message,
    sanitize_mapping,
)


class OcdsParseError(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(sanitize_error_message(message))


def plan_ocds_ranges(
    *,
    year: int,
    month: int,
    source_reported_total: int,
    page_size: int = OCDS_MAX_PAGE_SIZE,
) -> list[dict[str, int]]:
    """Deterministic non-overlapping [start, end] ranges (1-indexed inclusive)."""
    if page_size < 1 or page_size > OCDS_MAX_PAGE_SIZE:
        raise OcdsParseError(f"page_size must be 1..{OCDS_MAX_PAGE_SIZE}")
    if source_reported_total < 0:
        raise OcdsParseError("source_reported_total must be >= 0")
    if not (1 <= month <= 12):
        raise OcdsParseError("month must be 1..12")
    if source_reported_total == 0:
        return []
    ranges: list[dict[str, int]] = []
    start = 1
    while start <= source_reported_total:
        end = min(start + page_size - 1, source_reported_total)
        ranges.append({"year": year, "month": month, "start": start, "end": end})
        start = end + 1
    return ranges


def detect_range_anomalies(
    planned: list[dict[str, int]],
    observed: list[dict[str, Any]],
) -> list[str]:
    """Detect overlapping, missing, duplicate, or mismatched observed ranges."""
    issues: list[str] = []
    seen: set[tuple[int, int]] = set()
    for obs in observed:
        key = (int(obs["start"]), int(obs["end"]))
        if key in seen:
            issues.append(f"duplicate_page:{key[0]}-{key[1]}")
        seen.add(key)
    ordered = sorted(seen)
    for i in range(1, len(ordered)):
        prev_s, prev_e = ordered[i - 1]
        cur_s, cur_e = ordered[i]
        if cur_s <= prev_e:
            issues.append(f"overlapping_range:{prev_s}-{prev_e}|{cur_s}-{cur_e}")
    planned_set = {(r["start"], r["end"]) for r in planned}
    for s, e in planned_set - seen:
        issues.append(f"missing_range:{s}-{e}")
    for s, e in seen - planned_set:
        issues.append(f"unplanned_range:{s}-{e}")
    return issues


def build_ocds_query(
    *,
    year: int,
    month: int,
    range_start: int,
    range_end: int,
) -> AcquisitionQuery:
    if not (1 <= month <= 12):
        raise OcdsParseError("month must be 1..12")
    if range_start < 1:
        raise OcdsParseError("range_start must be >= 1")
    if range_end < range_start:
        raise OcdsParseError("range_end must be >= range_start")
    width = range_end - range_start + 1
    if width > OCDS_MAX_PAGE_SIZE:
        raise OcdsParseError("OCDS range wider than 1000")
    path = ENDPOINT_PATH_OCDS_TEMPLATE.format(
        year=year, month=month, start=range_start, end=range_end
    )
    identity = {
        "source_kind": SOURCE_KIND_OCDS,
        "endpoint_kind": ENDPOINT_OCDS_MONTHLY_RANGE,
        "query_contract_version": QUERY_CONTRACT_VERSION,
        "estado": None,
        "fecha_ddmmaaaa": None,
        "tender_code": None,
        "year": year,
        "month": month,
        "range_start": range_start,
        "range_end": range_end,
        "endpoint_path": path,
    }
    return AcquisitionQuery(
        acquisition_query_id=acquisition_query_id(identity),
        source_kind=SOURCE_KIND_OCDS,
        endpoint_kind=ENDPOINT_OCDS_MONTHLY_RANGE,
        query_contract_version=QUERY_CONTRACT_VERSION,
        estado=None,
        fecha_ddmmaaaa=None,
        tender_code=None,
        year=year,
        month=month,
        range_start=range_start,
        range_end=range_end,
        endpoint_path=path,
    )


def build_ocds_month_query(*, year: int, month: int) -> AcquisitionQuery:
    """Month-scope query identity — no page range; widths live on child range queries."""
    if not (1 <= month <= 12):
        raise OcdsParseError("month must be 1..12")
    path = ENDPOINT_PATH_OCDS_MONTH_TEMPLATE.format(year=year, month=month)
    identity = {
        "source_kind": SOURCE_KIND_OCDS,
        "endpoint_kind": ENDPOINT_OCDS_MONTHLY_MONTH,
        "query_contract_version": QUERY_CONTRACT_VERSION,
        "estado": None,
        "fecha_ddmmaaaa": None,
        "tender_code": None,
        "year": year,
        "month": month,
        "range_start": None,
        "range_end": None,
        "endpoint_path": path,
    }
    return AcquisitionQuery(
        acquisition_query_id=acquisition_query_id(identity),
        source_kind=SOURCE_KIND_OCDS,
        endpoint_kind=ENDPOINT_OCDS_MONTHLY_MONTH,
        query_contract_version=QUERY_CONTRACT_VERSION,
        estado=None,
        fecha_ddmmaaaa=None,
        tender_code=None,
        year=year,
        month=month,
        range_start=None,
        range_end=None,
        endpoint_path=path,
    )


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return None
    text = str(value).strip()
    return text or None


def _party_name(obj: Any) -> str | None:
    if isinstance(obj, dict):
        return _as_str(obj.get("name") or obj.get("legalName"))
    return _as_str(obj)


def _party_id(obj: Any) -> str | None:
    if isinstance(obj, dict):
        return _as_str(obj.get("id"))
    return None


def _release_identity_key(release: dict[str, Any]) -> tuple[str, str]:
    return (
        (_as_str(release.get("ocid")) or "").casefold(),
        (_as_str(release.get("id")) or "").casefold(),
    )


def _rejected_entry(
    *,
    ordinal: int,
    reason_code: str,
    digest_payload: dict[str, Any],
) -> dict[str, str]:
    return {
        "ordinal": str(ordinal),
        "reason_code": reason_code,
        "entry_digest": canonical_json_digest(digest_payload),
    }


def _validate_and_extract_typed_releases(
    payload: dict[str, Any],
) -> tuple[
    str | None,
    list[tuple[dict[str, Any], str, str | None]],
    list[dict[str, str]],
]:
    """Validate releases/records collections before typed extraction.

    Returns (malformed_reason, typed_releases, rejected_entries).
    malformed_reason set → treat page as malformed_response.
    """
    has_releases = "releases" in payload
    has_records = "records" in payload

    if has_releases:
        releases = payload.get("releases")
        if not isinstance(releases, list):
            return "invalid_releases_collection_type", [], []
        typed: list[tuple[dict[str, Any], str, str | None]] = []
        rejected: list[dict[str, str]] = []
        for ordinal, entry in enumerate(releases):
            if not isinstance(entry, dict):
                rejected.append(
                    _rejected_entry(
                        ordinal=ordinal,
                        reason_code="release_entry_not_object",
                        digest_payload={
                            "ordinal": ordinal,
                            "type": type(entry).__name__,
                        },
                    )
                )
                continue
            typed.append((entry, RELEASE_KIND_HISTORICAL, None))
        return None, typed, rejected

    if has_records:
        records = payload.get("records")
        if not isinstance(records, list):
            return "invalid_records_collection_type", [], []
        typed = []
        rejected = []
        for ordinal, rec in enumerate(records):
            if not isinstance(rec, dict):
                rejected.append(
                    _rejected_entry(
                        ordinal=ordinal,
                        reason_code="record_entry_not_object",
                        digest_payload={
                            "ordinal": ordinal,
                            "type": type(rec).__name__,
                        },
                    )
                )
                continue
            record_id = _as_str(rec.get("id"))
            historical: list[dict[str, Any]] = []
            nested = rec.get("releases")
            if isinstance(nested, list):
                for nested_i, r in enumerate(nested):
                    if isinstance(r, dict):
                        historical.append(r)
                    else:
                        rejected.append(
                            _rejected_entry(
                                ordinal=ordinal,
                                reason_code="nested_release_entry_not_object",
                                digest_payload={
                                    "record_ordinal": ordinal,
                                    "nested_ordinal": nested_i,
                                    "type": type(r).__name__,
                                },
                            )
                        )
            seen = {_release_identity_key(r) for r in historical}
            for r in historical:
                typed.append((r, RELEASE_KIND_HISTORICAL, record_id))
            compiled = rec.get("compiledRelease")
            if isinstance(compiled, dict):
                key = _release_identity_key(compiled)
                if key not in seen:
                    typed.append((compiled, RELEASE_KIND_COMPILED, record_id))
        return None, typed, rejected

    if "ocid" in payload and ("tender" in payload or "id" in payload):
        return None, [(payload, RELEASE_KIND_HISTORICAL, None)], []
    return "not_an_ocds_package_or_release_envelope", [], []


def _extract_typed_releases(
    payload: dict[str, Any],
) -> list[tuple[dict[str, Any], str, str | None]]:
    """Compatibility wrapper — prefer _validate_and_extract_typed_releases."""
    _reason, typed, _rejected = _validate_and_extract_typed_releases(payload)
    return typed


def _items_from_tender(tender: dict[str, Any]) -> list[dict[str, Any]]:
    items = tender.get("items")
    if not isinstance(items, list):
        return []
    return [i for i in items if isinstance(i, dict)]


def _sort_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda i: (
            _as_str(i.get("id")) or "",
            (_as_str(i.get("description")) or "").casefold(),
            canonical_json_digest(i),
        ),
    )


def _bounded_related_processes(release: dict[str, Any]) -> tuple[dict[str, str], ...]:
    """Canonical OCDS location is release.relatedProcesses (not tender)."""
    raw = release.get("relatedProcesses")
    if not isinstance(raw, list):
        return ()
    rows: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        row = {
            "id": _as_str(item.get("id")) or "",
            "relationship": "",
            "title": _as_str(item.get("title")) or "",
            "scheme": _as_str(item.get("scheme")) or "",
            "identifier": _as_str(item.get("identifier")) or "",
            "uri": _as_str(item.get("uri")) or "",
        }
        rel = item.get("relationship")
        if isinstance(rel, list):
            row["relationship"] = ",".join(str(x) for x in rel if x is not None)
        elif rel is not None:
            row["relationship"] = str(rel)
        rows.append(sanitize_mapping(row))
    return tuple(
        sorted(rows, key=lambda r: (r["id"], r["identifier"], r["title"], r["uri"]))
    )


def _additional_classifications(item: dict[str, Any]) -> tuple[dict[str, str], ...]:
    raw = item.get("additionalClassifications")
    if not isinstance(raw, list):
        return ()
    rows: list[dict[str, str]] = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        rows.append(
            {
                "scheme": _as_str(c.get("scheme")) or "",
                "id": _as_str(c.get("id")) or "",
                "description": _as_str(c.get("description")) or "",
            }
        )
    return tuple(sorted(rows, key=lambda r: (r["scheme"], r["id"], r["description"])))


def _tag_values(release: dict[str, Any]) -> tuple[str, ...]:
    tags = release.get("tag")
    if not isinstance(tags, list):
        return ()
    vals = [_as_str(t) for t in tags]
    return tuple(sorted(v for v in vals if v))


def _malformed_page(
    *,
    query: AcquisitionQuery,
    payload: Any,
    original_bytes: bytes | str | None,
    http_status: int | None,
    message: str,
    acquired_at_utc: str | None = None,
) -> AcquisitionPage:
    raw_digest, parser_digest, bytes_digest = payload_digests(
        payload, original_bytes=original_bytes
    )
    range_pos = {"start": query.range_start, "end": query.range_end}
    return AcquisitionPage(
        page_id=acquisition_page_id(
            acquisition_query_id_value=query.acquisition_query_id,
            range_position=range_pos,
            raw_canonical_json_digest=raw_digest,
        ),
        source_kind=SOURCE_KIND_OCDS,
        endpoint_kind=ENDPOINT_OCDS_MONTHLY_RANGE,
        acquisition_query_id=query.acquisition_query_id,
        range_position=range_pos,
        acquired_at_utc=acquired_at_utc,
        raw_canonical_json_digest=raw_digest,
        original_bytes_digest=bytes_digest,
        parser_input_digest=parser_digest,
        response_item_count=0,
        source_reported_total=None,
        http_status=http_status,
        parser_status="malformed",
        error_classification="malformed_response",
        completeness_status="malformed_response",
        envelope_meta={},
        error_message=sanitize_error_message(message),
    )


def _emit_release_observations(
    release: dict[str, Any],
    *,
    release_kind: str,
    record_id: str | None,
    snapshot_id: str,
    page_id: str,
    package_id: str | None,
) -> tuple[
    ProcurementSourceObservation,
    ProcurementTenderObservation,
    list[ProcurementLineObservation],
]:
    ocid = _as_str(release.get("ocid"))
    release_id = _as_str(release.get("id"))
    tender_obj = release.get("tender") if isinstance(release.get("tender"), dict) else {}
    tender_id_src = _as_str(tender_obj.get("id")) if tender_obj else None
    cand = ocds_canonical_candidate(
        ocid=ocid,
        release_id=release_id,
        tender_id=tender_id_src,
        release_kind=release_kind,
    )
    status_value = _as_str(tender_obj.get("status")) if tender_obj else None
    buyer = release.get("buyer")
    procuring = tender_obj.get("procuringEntity") if tender_obj else None
    buyer_name = _party_name(buyer) or _party_name(procuring)
    buyer_id = _party_id(buyer) or _party_id(procuring)
    period = (
        tender_obj.get("tenderPeriod")
        if isinstance(tender_obj.get("tenderPeriod"), dict)
        else {}
    )
    pub = _as_str(release.get("date")) or _as_str(period.get("startDate"))
    close = _as_str(period.get("endDate"))
    method = _as_str(tender_obj.get("procurementMethod")) if tender_obj else None
    method_details = (
        _as_str(tender_obj.get("procurementMethodDetails")) if tender_obj else None
    )
    related = _bounded_related_processes(release)
    tags = _tag_values(release)
    raw_digest = canonical_json_digest(release)
    obs_id = procurement_source_observation_id(
        source_kind=SOURCE_KIND_OCDS,
        endpoint_kind=ENDPOINT_OCDS_MONTHLY_RANGE,
        source_native_key=cand.source_native_tender_key,
        package_id=package_id,
        release_id=release_id,
        release_kind=release_kind,
        raw_payload_digest=raw_digest,
    )
    source = ProcurementSourceObservation(
        observation_id=obs_id,
        snapshot_id=snapshot_id,
        source_kind=SOURCE_KIND_OCDS,
        endpoint_kind=ENDPOINT_OCDS_MONTHLY_RANGE,
        source_native_key=cand.source_native_tender_key,
        source_native_tender_key=cand.source_native_tender_key,
        canonical_tender_key_candidate=cand.canonical_tender_key_candidate,
        canonical_candidate_kind=cand.canonical_candidate_kind,
        canonical_candidate_reason=cand.canonical_candidate_reason,
        source_status_code=None,
        source_status_name=status_value,
        source_status_system="ocds",
        source_status_value=status_value,
        publication_timestamp_raw=pub,
        close_timestamp_raw=close,
        buyer_display_raw=buyer_name,
        buyer_source_id=buyer_id,
        package_id=package_id,
        release_id=release_id,
        ocid=ocid,
        record_id=record_id,
        release_kind=release_kind,
        release_tags=tags,
        raw_payload_digest=raw_digest,
        parser_version=PARSER_VERSION,
        provenance_reason_codes=(f"ocds_{release_kind}",),
        page_id=page_id,
    )
    tender_key = cand.source_native_tender_key
    tender_obs_id = procurement_tender_observation_id(
        source_observation_id=obs_id, normalized_tender_key=tender_key
    )
    stage = tags[0] if tags else None
    value_obj = tender_obj.get("value") if isinstance(tender_obj.get("value"), dict) else {}
    tender = ProcurementTenderObservation(
        tender_observation_id=tender_obs_id,
        source_observation_id=obs_id,
        source_kind=SOURCE_KIND_OCDS,
        normalized_tender_key=tender_key,
        source_native_tender_key=cand.source_native_tender_key,
        canonical_tender_key_candidate=cand.canonical_tender_key_candidate,
        canonical_candidate_kind=cand.canonical_candidate_kind,
        canonical_candidate_reason=cand.canonical_candidate_reason,
        title=_as_str(tender_obj.get("title")) if tender_obj else None,
        description=_as_str(tender_obj.get("description")) if tender_obj else None,
        buyer_display=buyer_name,
        buyer_source_id=buyer_id,
        publication_timestamp_raw=pub,
        close_timestamp_raw=close,
        source_status_code=None,
        source_status_name=status_value,
        source_process_stage=stage,
        region=None,
        currency=_as_str(value_obj.get("currency")),
        estimated_value=_as_str(value_obj.get("amount")),
        procurement_method=method,
        procurement_method_details=method_details,
        related_processes=related,
        field_provenance={
            "canonical_tender_key_candidate": "tender.id when MP CodigoExterno shape",
            "source_status_value": "tender.status",
            "close_timestamp_raw": "tender.tenderPeriod.endDate",
            "procurement_method": "tender.procurementMethod",
            "related_processes": "release.relatedProcesses",
            "note": "OCDS fields are evidence only; not PR5 eligibility",
        },
    )
    lines: list[ProcurementLineObservation] = []
    items = _sort_items(_items_from_tender(tender_obj or {}))
    seen: dict[str, int] = {}
    for ordinal, item in enumerate(items):
        native = _as_str(item.get("id"))
        if native:
            seen[native] = seen.get(native, 0) + 1
            if seen[native] > 1:
                native = f"{native}#occ{seen[native]}"
        classification = item.get("classification")
        class_id = None
        if isinstance(classification, dict):
            class_id = _as_str(classification.get("id"))
        unit = item.get("unit")
        unit_name = _as_str(unit.get("name") if isinstance(unit, dict) else unit)
        desc = _as_str(item.get("description"))
        addl = _additional_classifications(item)
        line_id = procurement_line_observation_id(
            tender_observation_id=tender_obs_id,
            source_native_line_id=native,
            ordinal=ordinal,
            description=desc,
        )
        lines.append(
            ProcurementLineObservation(
                line_observation_id=line_id,
                tender_observation_id=tender_obs_id,
                source_native_line_id=native,
                description=desc,
                product=None,
                category=class_id,
                unspsc_or_classification=class_id,
                additional_classifications=addl,
                quantity=_as_str(item.get("quantity")),
                unit=unit_name,
                ordinal=ordinal,
                field_provenance={
                    "source_native_line_id": "item.id",
                    "unspsc_or_classification": "item.classification.id",
                    "additional_classifications": "item.additionalClassifications",
                },
            )
        )
    return source, tender, lines


def parse_ocds_package(
    payload: Any,
    *,
    query: AcquisitionQuery | None = None,
    snapshot_id: str = "pending",
    acquired_at_utc: str | None = None,
    original_bytes: bytes | str | None = None,
    http_status: int | None = 200,
    year: int = 2026,
    month: int = 1,
    range_start: int = 1,
    range_end: int = 1,
) -> tuple[
    AcquisitionQuery,
    AcquisitionPage,
    list[ProcurementSourceObservation],
    list[ProcurementTenderObservation],
    list[ProcurementLineObservation],
    dict[str, Any],
]:
    """Parse a single OCDS page/package. Empty single page → empty_page (not terminal)."""
    query = query or build_ocds_query(
        year=year, month=month, range_start=range_start, range_end=range_end
    )
    if not isinstance(payload, dict):
        page = _malformed_page(
            query=query,
            payload=payload,
            original_bytes=original_bytes,
            http_status=http_status,
            message="payload must be a JSON object",
            acquired_at_utc=acquired_at_utc,
        )
        return query, page, [], [], [], {"error": page.error_message}

    raw_digest, parser_digest, bytes_digest = payload_digests(
        payload, original_bytes=original_bytes
    )
    malformed_reason, typed, rejected = _validate_and_extract_typed_releases(payload)
    if malformed_reason is not None:
        page = _malformed_page(
            query=query,
            payload=payload,
            original_bytes=original_bytes,
            http_status=http_status,
            message=malformed_reason,
            acquired_at_utc=acquired_at_utc,
        )
        return query, page, [], [], [], {
            "error": page.error_message,
            "rejected_entry_count": 0,
        }

    package_uri = _as_str(payload.get("uri"))
    publisher = payload.get("publisher")
    package_id = package_uri or (
        _as_str(publisher.get("name")) if isinstance(publisher, dict) else None
    )
    published = _as_str(payload.get("publishedDate") or payload.get("publicationDate"))

    # empty list → empty_page; mixed/all-invalid list → partial_page_failure;
    # all valid → complete.
    if rejected and typed:
        completeness = "partial_page_failure"
        parser_status = "partial"
        error_classification = "partial_page_failure"
    elif rejected and not typed:
        # Documented rule: valid collection shape but every entry rejected.
        completeness = "partial_page_failure"
        parser_status = "partial"
        error_classification = "partial_page_failure"
    elif not typed:
        completeness = "empty_page"
        parser_status = "ok"
        error_classification = None
    else:
        completeness = "complete"
        parser_status = "ok"
        error_classification = None

    page = AcquisitionPage(
        page_id=acquisition_page_id(
            acquisition_query_id_value=query.acquisition_query_id,
            range_position={"start": query.range_start, "end": query.range_end},
            raw_canonical_json_digest=raw_digest,
        ),
        source_kind=SOURCE_KIND_OCDS,
        endpoint_kind=ENDPOINT_OCDS_MONTHLY_RANGE,
        acquisition_query_id=query.acquisition_query_id,
        range_position={"start": query.range_start, "end": query.range_end},
        acquired_at_utc=acquired_at_utc,
        raw_canonical_json_digest=raw_digest,
        original_bytes_digest=bytes_digest,
        parser_input_digest=parser_digest,
        response_item_count=len(typed),
        source_reported_total=None,
        http_status=http_status,
        parser_status=parser_status,
        error_classification=error_classification,
        completeness_status=completeness,
        envelope_meta={
            "uri": _as_str(payload.get("uri")),
            "version": _as_str(payload.get("version")),
            "publishedDate": published,
            "package_id": package_id,
        },
        error_message=None,
    )

    sources: list[ProcurementSourceObservation] = []
    tenders: list[ProcurementTenderObservation] = []
    lines: list[ProcurementLineObservation] = []
    for release, release_kind, record_id in typed:
        src, tender, line_rows = _emit_release_observations(
            release,
            release_kind=release_kind,
            record_id=record_id,
            snapshot_id=snapshot_id,
            page_id=page.page_id,
            package_id=package_id,
        )
        sources.append(src)
        tenders.append(tender)
        lines.extend(line_rows)

    diagnostics = {
        "release_count": len(typed),
        "line_count": len(lines),
        "rejected_entry_count": len(rejected),
        "rejected_entries": rejected,
        "status_mapping": "source_status_system=ocds; not PR5 eligibility",
        "package_publishedDate": published,
        "records_policy": "B_historical_preferred_compiled_if_unique",
        "single_page_empty_status": "empty_page",
        "all_invalid_entries_rule": "partial_page_failure",
    }
    return query, page, sources, tenders, lines, diagnostics


def _classify_monthly_completeness(
    *,
    planned: list[dict[str, int]],
    pages: list[AcquisitionPage],
    anomalies: list[str],
    source_reported_total: int | None,
    total_items: int,
) -> str:
    ordered = sorted(
        pages, key=lambda p: (int(p.range_position["start"]), int(p.range_position["end"]))
    )
    if any(p.completeness_status == "malformed_response" for p in ordered):
        return "malformed_response"
    if any(p.completeness_status == "partial_page_failure" for p in ordered):
        return "partial_page_failure"
    if any(p.parser_status not in {"ok"} for p in ordered):
        return "partial_page_failure"
    if any(a.startswith("duplicate_page:") for a in anomalies):
        return "duplicate_page"
    if any(a.startswith("overlapping_range:") for a in anomalies):
        return "overlapping_range"
    if any(a.startswith("missing_range:") for a in anomalies) or any(
        a.startswith("unplanned_range:") for a in anomalies
    ):
        return "incomplete_range"
    if len(ordered) != len(planned):
        return "incomplete_range"

    # Empty page that is not a valid terminal trailing empty → incomplete.
    empty_idxs = [
        i
        for i, p in enumerate(ordered)
        if p.response_item_count == 0 or p.completeness_status == "empty_page"
    ]
    terminal_empty = False
    if empty_idxs:
        # Only the final page may be empty for terminal classification.
        if empty_idxs != [len(ordered) - 1]:
            return "incomplete_range"
        last = ordered[-1]
        prior = ordered[:-1]
        if prior and all(p.completeness_status == "complete" for p in prior):
            start_last = int(last.range_position["start"])
            if source_reported_total is not None:
                prior_items = sum(p.response_item_count for p in prior)
                if (
                    prior_items == source_reported_total
                    and last.response_item_count == 0
                    and start_last == source_reported_total + 1
                ):
                    terminal_empty = True
                elif (
                    prior_items == source_reported_total
                    and last.response_item_count == 0
                    and start_last > source_reported_total
                ):
                    terminal_empty = True
            elif all(
                p.response_item_count
                == int(p.range_position["end"]) - int(p.range_position["start"]) + 1
                for p in prior
            ):
                terminal_empty = True
        if not terminal_empty:
            return "incomplete_range"
        return "terminal_empty_page"

    # Undersized non-empty pages relative to requested width.
    for p in ordered:
        start = int(p.range_position.get("start") or 0)
        end = int(p.range_position.get("end") or 0)
        expected = end - start + 1
        if expected > 0 and 0 < p.response_item_count < expected:
            return "incomplete_range"

    if source_reported_total is not None and total_items != source_reported_total:
        return "source_total_mismatch"

    return "complete"


def _validate_month_assembly_inputs(
    *,
    planned_ranges: list[dict[str, int]],
    parsed_pages: list[
        tuple[
            AcquisitionQuery,
            AcquisitionPage,
            list[ProcurementSourceObservation],
            list[ProcurementTenderObservation],
            list[ProcurementLineObservation],
            dict[str, Any],
        ]
    ],
    year: int,
    month: int,
    source_reported_total: int | None = None,
) -> list[dict[str, int]]:
    """Validate planned ranges and every child query/page pair. Raises OcdsParseError."""
    if not planned_ranges:
        raise OcdsParseError("planned_ranges required")

    years = {int(r["year"]) for r in planned_ranges}
    months = {int(r["month"]) for r in planned_ranges}
    if len(years) != 1 or len(months) != 1:
        raise OcdsParseError("planned_ranges must share one year and month")
    if int(next(iter(years))) != year or int(next(iter(months))) != month:
        raise OcdsParseError("planned_ranges year/month must match month scope")

    planned_keys = [(int(r["start"]), int(r["end"])) for r in planned_ranges]
    if len(planned_keys) != len(set(planned_keys)):
        raise OcdsParseError("duplicate planned range")

    ordered_planned = sorted(
        planned_ranges,
        key=lambda r: (int(r["start"]), int(r["end"])),
    )
    for r in ordered_planned:
        width = int(r["end"]) - int(r["start"]) + 1
        if width < 1 or width > OCDS_MAX_PAGE_SIZE:
            raise OcdsParseError("planned range width invalid")
        if int(r["start"]) < 1:
            raise OcdsParseError("planned range_start must be >= 1")

    if int(ordered_planned[0]["start"]) != 1:
        raise OcdsParseError("planned ranges must start at 1")

    for i in range(1, len(ordered_planned)):
        prev_end = int(ordered_planned[i - 1]["end"])
        cur_start = int(ordered_planned[i]["start"])
        if cur_start <= prev_end:
            raise OcdsParseError("planned ranges overlap")
        if cur_start != prev_end + 1:
            raise OcdsParseError("planned ranges have a gap")

    if source_reported_total is not None:
        if source_reported_total < 0:
            raise OcdsParseError("source_reported_total must be >= 0")
        last_start = int(ordered_planned[-1]["start"])
        last_end = int(ordered_planned[-1]["end"])
        if source_reported_total == 0:
            raise OcdsParseError("nonzero planned ranges inconsistent with source_reported_total=0")
        if last_start == source_reported_total + 1:
            # Trailing empty probe beyond total — prior coverage must end at total.
            if len(ordered_planned) < 2:
                raise OcdsParseError("terminal empty probe requires prior coverage")
            if int(ordered_planned[-2]["end"]) != source_reported_total:
                raise OcdsParseError(
                    "terminal empty probe prior coverage must end at source_reported_total"
                )
        elif last_end < source_reported_total:
            raise OcdsParseError("planned coverage incomplete vs source_reported_total")
        elif last_end > source_reported_total and last_start <= source_reported_total:
            raise OcdsParseError("planned coverage extends past source_reported_total")
        elif last_end != source_reported_total and last_start != source_reported_total + 1:
            raise OcdsParseError("planned coverage inconsistent with source_reported_total")

    planned_set = {(int(r["start"]), int(r["end"])) for r in ordered_planned}
    seen_page_ids: set[str] = set()
    seen_ranges: set[tuple[int, int]] = set()

    for query, page, *_rest in parsed_pages:
        if query.source_kind != SOURCE_KIND_OCDS:
            raise OcdsParseError("child query source_kind must be chilecompra_ocds")
        if page.source_kind != SOURCE_KIND_OCDS:
            raise OcdsParseError("child page source_kind must be chilecompra_ocds")
        if query.endpoint_kind != ENDPOINT_OCDS_MONTHLY_RANGE:
            raise OcdsParseError("child query must use ocds_lista_agno_mes_range")
        if page.endpoint_kind != ENDPOINT_OCDS_MONTHLY_RANGE:
            raise OcdsParseError("child page endpoint_kind must be ocds_lista_agno_mes_range")
        if query.year != year or query.month != month:
            raise OcdsParseError("child query year/month must match month scope")
        recomputed = acquisition_query_id(query.identity_payload())
        if recomputed != query.acquisition_query_id:
            raise OcdsParseError("child query acquisition_query_id does not match identity")
        if page.acquisition_query_id != query.acquisition_query_id:
            raise OcdsParseError("page acquisition_query_id must equal tuple query ID")
        if page.range_position.get("start") != query.range_start or page.range_position.get(
            "end"
        ) != query.range_end:
            raise OcdsParseError("page range_position must equal query range_start/range_end")
        key = (int(query.range_start or 0), int(query.range_end or 0))
        if key not in planned_set:
            raise OcdsParseError("observed range not in planned_ranges")
        if page.page_id in seen_page_ids:
            raise OcdsParseError("duplicate child page")
        if key in seen_ranges:
            raise OcdsParseError("duplicate child page range")
        seen_page_ids.add(page.page_id)
        seen_ranges.add(key)

    return ordered_planned


def _empty_month_snapshot(
    *,
    year: int,
    month: int,
    fixture_origin: str,
    materialized_at_utc: str | None,
) -> AcquisitionSnapshot:
    from datetime import datetime, timezone

    month_query = build_ocds_month_query(year=year, month=month)
    identity = query_identity_from_model(month_query)
    completeness = "complete"
    source_fp = acquisition_source_fingerprint(
        source_kind=SOURCE_KIND_OCDS,
        query_identity=identity,
        pages=[],
        completeness_status=completeness,
    )
    semantic = acquisition_normalized_semantic_digest(
        source_observations=[],
        tender_observations=[],
        line_observations=[],
        parser_version=PARSER_VERSION,
        contract_version=ACQUISITION_CONTRACT_VERSION,
    )
    snap_id = procurement_snapshot_id(
        acquisition_query_id_value=month_query.acquisition_query_id,
        source_fingerprint=source_fp,
    )
    materialized = materialized_at_utc or datetime.now(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    return AcquisitionSnapshot(
        snapshot_id=snap_id,
        query=month_query,
        pages=(),
        source_observations=(),
        tender_observations=(),
        line_observations=(),
        completeness_status=completeness,
        parser_version=PARSER_VERSION,
        contract_version=ACQUISITION_CONTRACT_VERSION,
        fixture_origin=fixture_origin,
        diagnostics={
            "planned_ranges": [],
            "anomalies": [],
            "source_reported_total": 0,
            "total_items": 0,
            "page_count": 0,
            "zero_record_month": True,
            "month_scope_endpoint_kind": ENDPOINT_OCDS_MONTHLY_MONTH,
            "child_endpoint_kind": ENDPOINT_OCDS_MONTHLY_RANGE,
        },
        source_fingerprint=source_fp,
        normalized_semantic_digest=semantic,
        materialized_at_utc=materialized,
    )


def build_ocds_month_snapshot(
    *,
    planned_ranges: list[dict[str, int]],
    parsed_pages: list[
        tuple[
            AcquisitionQuery,
            AcquisitionPage,
            list[ProcurementSourceObservation],
            list[ProcurementTenderObservation],
            list[ProcurementLineObservation],
            dict[str, Any],
        ]
    ],
    source_reported_total: int | None = None,
    fixture_origin: str = "synthetic_official_shape",
    materialized_at_utc: str | None = None,
    year: int | None = None,
    month: int | None = None,
) -> AcquisitionSnapshot:
    """Assemble a multi-page monthly OCDS snapshot from offline parse results.

    Snapshot identity uses build_ocds_month_query (no page width). Child pages
    remain ocds_lista_agno_mes_range with widths ≤ 1000.
    Input page order does not affect fingerprints (pages sorted by range).

    Zero-record months: planned_ranges=[], parsed_pages=[], source_reported_total=0,
    with explicit year/month → complete empty month snapshot.
    """
    if not planned_ranges:
        if (
            source_reported_total == 0
            and not parsed_pages
            and year is not None
            and month is not None
        ):
            return _empty_month_snapshot(
                year=int(year),
                month=int(month),
                fixture_origin=fixture_origin,
                materialized_at_utc=materialized_at_utc,
            )
        if source_reported_total is None:
            raise OcdsParseError("empty planned_ranges require source_reported_total=0")
        if source_reported_total != 0:
            raise OcdsParseError("empty planned_ranges require source_reported_total=0")
        if parsed_pages:
            raise OcdsParseError("empty planned_ranges cannot include parsed_pages")
        if year is None or month is None:
            raise OcdsParseError("empty planned_ranges require explicit year and month")
        raise OcdsParseError("planned_ranges required")

    if year is None or month is None:
        scope_year = int(planned_ranges[0]["year"])
        scope_month = int(planned_ranges[0]["month"])
    else:
        scope_year = int(year)
        scope_month = int(month)

    ordered_planned = _validate_month_assembly_inputs(
        planned_ranges=planned_ranges,
        parsed_pages=parsed_pages,
        year=scope_year,
        month=scope_month,
        source_reported_total=source_reported_total,
    )

    sorted_pages = sorted(
        parsed_pages,
        key=lambda row: (
            int(row[1].range_position.get("start") or 0),
            int(row[1].range_position.get("end") or 0),
            row[1].page_id,
        ),
    )
    pages = [row[1] for row in sorted_pages]
    observed = [
        {
            "start": int(p.range_position["start"]),
            "end": int(p.range_position["end"]),
        }
        for p in pages
    ]
    anomalies = detect_range_anomalies(ordered_planned, observed)

    sources: list[ProcurementSourceObservation] = []
    tenders: list[ProcurementTenderObservation] = []
    lines: list[ProcurementLineObservation] = []
    for _q, _p, srcs, tends, lns, _d in sorted_pages:
        sources.extend(srcs)
        tenders.extend(tends)
        lines.extend(lns)

    total_items = sum(p.response_item_count for p in pages)
    completeness = _classify_monthly_completeness(
        planned=ordered_planned,
        pages=pages,
        anomalies=anomalies,
        source_reported_total=source_reported_total,
        total_items=total_items,
    )

    month_query = build_ocds_month_query(year=scope_year, month=scope_month)
    identity = query_identity_from_model(month_query)
    source_fp = acquisition_source_fingerprint(
        source_kind=SOURCE_KIND_OCDS,
        query_identity=identity,
        pages=pages,
        completeness_status=completeness,
    )
    semantic = acquisition_normalized_semantic_digest(
        source_observations=sources,
        tender_observations=tenders,
        line_observations=lines,
        parser_version=PARSER_VERSION,
        contract_version=ACQUISITION_CONTRACT_VERSION,
    )
    snap_id = procurement_snapshot_id(
        acquisition_query_id_value=month_query.acquisition_query_id,
        source_fingerprint=source_fp,
    )
    sources_t = tuple(
        ProcurementSourceObservation(
            **{
                **o.to_dict(),
                "snapshot_id": snap_id,
                "provenance_reason_codes": tuple(o.provenance_reason_codes),
                "release_tags": tuple(o.release_tags),
            }
        )
        for o in sources
    )
    from datetime import datetime, timezone

    materialized = materialized_at_utc or datetime.now(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    diagnostics = {
        "planned_ranges": ordered_planned,
        "anomalies": anomalies,
        "source_reported_total": source_reported_total,
        "total_items": total_items,
        "page_count": len(pages),
        "shuffled_input_order_normalized": True,
        "records_policy": "B_historical_preferred_compiled_if_unique",
        "month_scope_endpoint_kind": ENDPOINT_OCDS_MONTHLY_MONTH,
        "child_endpoint_kind": ENDPOINT_OCDS_MONTHLY_RANGE,
    }
    return AcquisitionSnapshot(
        snapshot_id=snap_id,
        query=month_query,
        pages=tuple(pages),
        source_observations=sources_t,
        tender_observations=tuple(tenders),
        line_observations=tuple(lines),
        completeness_status=completeness,
        parser_version=PARSER_VERSION,
        contract_version=ACQUISITION_CONTRACT_VERSION,
        fixture_origin=fixture_origin,
        diagnostics=diagnostics,
        source_fingerprint=source_fp,
        normalized_semantic_digest=semantic,
        materialized_at_utc=materialized,
    )
