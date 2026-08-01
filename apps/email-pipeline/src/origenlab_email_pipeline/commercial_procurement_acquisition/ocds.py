"""Official ChileCompra OCDS package parser and range planner (no network)."""

from __future__ import annotations

from typing import Any

from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
    original_bytes_digest,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.constants import (
    ENDPOINT_OCDS_MONTHLY_RANGE,
    ENDPOINT_PATH_OCDS_TEMPLATE,
    OCDS_MAX_PAGE_SIZE,
    PARSER_VERSION,
    QUERY_CONTRACT_VERSION,
    SOURCE_KIND_OCDS,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.fingerprint import (
    acquisition_page_id,
    acquisition_query_id,
    procurement_line_observation_id,
    procurement_source_observation_id,
    procurement_tender_observation_id,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.models import (
    AcquisitionPage,
    AcquisitionQuery,
    ProcurementLineObservation,
    ProcurementSourceObservation,
    ProcurementTenderObservation,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.redaction import (
    sanitize_error_message,
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
    """Deterministic non-overlapping [start, end] ranges (1-indexed inclusive).

    Does not execute network calls. page_size max 1000.
    """
    if page_size < 1 or page_size > OCDS_MAX_PAGE_SIZE:
        raise OcdsParseError(f"page_size must be 1..{OCDS_MAX_PAGE_SIZE}")
    if source_reported_total < 0:
        raise OcdsParseError("source_reported_total must be >= 0")
    if source_reported_total == 0:
        return []
    ranges: list[dict[str, int]] = []
    start = 1
    while start <= source_reported_total:
        end = min(start + page_size - 1, source_reported_total)
        ranges.append(
            {
                "year": year,
                "month": month,
                "start": start,
                "end": end,
            }
        )
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
    # Overlaps among observed
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
    if range_end - range_start + 1 > OCDS_MAX_PAGE_SIZE:
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
        **identity,  # type: ignore[arg-type]
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


def _extract_releases(payload: dict[str, Any]) -> list[dict[str, Any]]:
    releases = payload.get("releases")
    if isinstance(releases, list):
        return [r for r in releases if isinstance(r, dict)]
    records = payload.get("records")
    if isinstance(records, list):
        out: list[dict[str, Any]] = []
        for rec in records:
            if not isinstance(rec, dict):
                continue
            compiled = rec.get("compiledRelease")
            if isinstance(compiled, dict):
                out.append(compiled)
            nested = rec.get("releases")
            if isinstance(nested, list):
                out.extend(r for r in nested if isinstance(r, dict))
        return out
    # Single release document
    if "ocid" in payload and "tender" in payload:
        return [payload]
    return []


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
    query = query or build_ocds_query(
        year=year, month=month, range_start=range_start, range_end=range_end
    )
    if not isinstance(payload, dict):
        digest = canonical_json_digest({"error": "not_object"})
        page = AcquisitionPage(
            page_id=acquisition_page_id(
                acquisition_query_id_value=query.acquisition_query_id,
                range_position={
                    "start": query.range_start,
                    "end": query.range_end,
                },
                raw_canonical_json_digest=digest,
            ),
            source_kind=SOURCE_KIND_OCDS,
            endpoint_kind=ENDPOINT_OCDS_MONTHLY_RANGE,
            acquisition_query_id=query.acquisition_query_id,
            range_position={"start": query.range_start, "end": query.range_end},
            acquired_at_utc=acquired_at_utc,
            raw_canonical_json_digest=digest,
            original_bytes_digest=original_bytes_digest(original_bytes),
            parser_input_digest=digest,
            response_item_count=0,
            source_reported_total=None,
            http_status=http_status,
            parser_status="malformed",
            error_classification="malformed_response",
            completeness_status="malformed_response",
            envelope_meta={},
        )
        return query, page, [], [], [], {"error": "payload must be a JSON object"}

    releases = _extract_releases(payload)
    parser_digest = canonical_json_digest(payload)
    package_uri = _as_str(payload.get("uri"))
    publisher = payload.get("publisher")
    package_id = package_uri or (
        _as_str(publisher.get("name")) if isinstance(publisher, dict) else None
    )
    published = _as_str(payload.get("publishedDate") or payload.get("publicationDate"))

    completeness = "complete"
    if not releases:
        completeness = "terminal_empty_page"

    page = AcquisitionPage(
        page_id=acquisition_page_id(
            acquisition_query_id_value=query.acquisition_query_id,
            range_position={"start": query.range_start, "end": query.range_end},
            raw_canonical_json_digest=parser_digest,
        ),
        source_kind=SOURCE_KIND_OCDS,
        endpoint_kind=ENDPOINT_OCDS_MONTHLY_RANGE,
        acquisition_query_id=query.acquisition_query_id,
        range_position={"start": query.range_start, "end": query.range_end},
        acquired_at_utc=acquired_at_utc,
        raw_canonical_json_digest=parser_digest,
        original_bytes_digest=original_bytes_digest(original_bytes),
        parser_input_digest=parser_digest,
        response_item_count=len(releases),
        source_reported_total=None,
        http_status=http_status,
        parser_status="ok" if releases or completeness == "terminal_empty_page" else "malformed",
        error_classification=None,
        completeness_status=completeness,
        envelope_meta={
            "uri": _as_str(payload.get("uri")),
            "version": _as_str(payload.get("version")),
            "publishedDate": published,
            "package_id": package_id,
        },
    )

    sources: list[ProcurementSourceObservation] = []
    tenders: list[ProcurementTenderObservation] = []
    lines: list[ProcurementLineObservation] = []

    for release in releases:
        ocid = _as_str(release.get("ocid"))
        release_id = _as_str(release.get("id"))
        tender_obj = release.get("tender") if isinstance(release.get("tender"), dict) else {}
        tender_id_src = _as_str(tender_obj.get("id")) if tender_obj else None
        source_native = "|".join(
            p for p in (ocid or "", release_id or "", tender_id_src or "") if p
        ) or canonical_json_digest(release)[:24]
        status_value = _as_str(tender_obj.get("status")) if tender_obj else None
        buyer = release.get("buyer")
        procuring = tender_obj.get("procuringEntity") if tender_obj else None
        buyer_name = _party_name(buyer) or _party_name(procuring)
        buyer_id = _party_id(buyer) or _party_id(procuring)
        period = tender_obj.get("tenderPeriod") if isinstance(tender_obj.get("tenderPeriod"), dict) else {}
        pub = _as_str(release.get("date")) or _as_str(period.get("startDate"))
        close = _as_str(period.get("endDate"))
        raw_digest = canonical_json_digest(release)
        # Do not collapse different releases: observation key includes release id.
        obs_id = procurement_source_observation_id(
            source_kind=SOURCE_KIND_OCDS,
            endpoint_kind=ENDPOINT_OCDS_MONTHLY_RANGE,
            source_native_key=source_native,
            package_id=package_id,
            release_id=release_id,
            raw_payload_digest=raw_digest,
        )
        # Canonical tender-key candidate prefers tender.id then ocid.
        if tender_id_src:
            canon = f"ocds-tender:{tender_id_src.casefold()}"
        elif ocid:
            canon = f"ocds-ocid:{ocid.casefold()}"
        else:
            canon = f"ocds-release:{source_native.casefold()}"

        sources.append(
            ProcurementSourceObservation(
                observation_id=obs_id,
                snapshot_id=snapshot_id,
                source_kind=SOURCE_KIND_OCDS,
                endpoint_kind=ENDPOINT_OCDS_MONTHLY_RANGE,
                source_native_key=source_native,
                canonical_tender_key_candidate=canon,
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
                raw_payload_digest=raw_digest,
                parser_version=PARSER_VERSION,
                provenance_reason_codes=("ocds_release",),
                page_id=page.page_id,
            )
        )
        tender_obs_id = procurement_tender_observation_id(
            source_observation_id=obs_id, normalized_tender_key=canon
        )
        tags = release.get("tag")
        stage = None
        if isinstance(tags, list) and tags:
            stage = _as_str(tags[0])
        tenders.append(
            ProcurementTenderObservation(
                tender_observation_id=tender_obs_id,
                source_observation_id=obs_id,
                source_kind=SOURCE_KIND_OCDS,
                normalized_tender_key=canon,
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
                currency=_as_str(tender_obj.get("value", {}).get("currency"))
                if isinstance(tender_obj.get("value"), dict)
                else None,
                estimated_value=_as_str(tender_obj.get("value", {}).get("amount"))
                if isinstance(tender_obj.get("value"), dict)
                else None,
                field_provenance={
                    "normalized_tender_key": "tender.id|ocid",
                    "source_status_value": "tender.status",
                    "close_timestamp_raw": "tender.tenderPeriod.endDate",
                    "note": "OCDS status is not PR5 active eligibility",
                },
            )
        )
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
                    quantity=_as_str(item.get("quantity")),
                    unit=unit_name,
                    ordinal=ordinal,
                    field_provenance={
                        "source_native_line_id": "item.id",
                        "unspsc_or_classification": "item.classification.id",
                    },
                )
            )

    diagnostics = {
        "release_count": len(releases),
        "line_count": len(lines),
        "status_mapping": "source_status_system=ocds; not PR5 eligibility",
        "package_publishedDate": published,
    }
    return query, page, sources, tenders, lines, diagnostics
