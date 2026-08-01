"""OCDS range-index semantics conclusions from probe observations."""

from __future__ import annotations

from typing import Any, Literal

from origenlab_email_pipeline.commercial_procurement_acquisition.redaction import (
    redact_token,
)

RangeConclusion = Literal[
    "zero_based_end_exclusive",
    "zero_based_end_inclusive",
    "one_based_end_inclusive",
    "one_based_end_exclusive",
    "zero_based_offset_limit",
    "ambiguous",
    "endpoint_rejected_zero",
    "contract_unavailable",
]


def _listing_tokens(payload: dict[str, Any] | None) -> list[str]:
    if not payload:
        return []
    tokens: list[str] = []
    data = payload.get("data")
    if isinstance(data, list):
        for row in data:
            if not isinstance(row, dict):
                continue
            ocid = str(row.get("ocid") or "")
            tokens.append(redact_token("ocds_obs", ocid))
        return tokens
    releases = payload.get("releases")
    if isinstance(releases, list):
        for rel in releases:
            if not isinstance(rel, dict):
                continue
            ocid = str(rel.get("ocid") or "")
            rid = str(rel.get("id") or "")
            tokens.append(redact_token("ocds_obs", f"{ocid}|{rid}"))
        return tokens
    records = payload.get("records")
    if isinstance(records, list):
        for rec in records:
            if not isinstance(rec, dict):
                continue
            nested = rec.get("releases")
            if isinstance(nested, list) and nested and isinstance(nested[0], dict):
                ocid = str(nested[0].get("ocid") or "")
                rid = str(nested[0].get("id") or "")
                tokens.append(redact_token("ocds_obs", f"{ocid}|{rid}"))
                continue
            compiled = rec.get("compiledRelease")
            if isinstance(compiled, dict):
                ocid = str(compiled.get("ocid") or "")
                rid = str(compiled.get("id") or "")
                tokens.append(redact_token("ocds_obs", f"{ocid}|{rid}"))
    return tokens


def summarize_probe(
    *,
    label: str,
    start: int,
    end: int,
    http_status: int | None,
    payload: dict[str, Any] | None,
    error_classification: str | None,
) -> dict[str, Any]:
    tokens = _listing_tokens(payload)
    pagination = payload.get("pagination") if isinstance(payload, dict) else None
    package_type = None
    if payload:
        if isinstance(payload.get("data"), list) and isinstance(pagination, dict):
            package_type = "lista_index_pagination"
        elif isinstance(payload.get("releases"), list):
            package_type = "releases"
        elif isinstance(payload.get("records"), list):
            package_type = "records"
        elif "status" in payload and "detail" in payload:
            package_type = "error_envelope"
        else:
            package_type = "unknown"
    return {
        "label": label,
        "start": start,
        "end": end,
        "http_status": http_status,
        "error_classification": error_classification,
        "returned_count": len(tokens),
        "first_token": tokens[0] if tokens else None,
        "last_token": tokens[-1] if tokens else None,
        "tokens": tokens,
        "package_type": package_type,
        "pagination": {
            "offset": pagination.get("offset") if isinstance(pagination, dict) else None,
            "limit": pagination.get("limit") if isinstance(pagination, dict) else None,
            "total": pagination.get("total") if isinstance(pagination, dict) else None,
        }
        if isinstance(pagination, dict)
        else None,
        "body_status": payload.get("status") if isinstance(payload, dict) else None,
    }


def _probe_is_valid_lista_index(p: dict[str, Any]) -> bool:
    if p.get("package_type") != "lista_index_pagination":
        return False
    pag = p.get("pagination") or {}
    if not isinstance(pag, dict):
        return False
    if pag.get("offset") != p.get("start"):
        return False
    if pag.get("limit") != p.get("end"):
        return False
    try:
        return int(p.get("returned_count") or -1) == int(p.get("end"))
    except (TypeError, ValueError):
        return False


def _zero_limit_ok(p00: dict[str, Any]) -> tuple[bool, str]:
    """Validate 0/0 independently for the offset/limit contract."""
    count = int(p00.get("returned_count") or 0)
    package = p00.get("package_type")
    if package == "lista_index_pagination" and count > 0:
        return False, "0/0_inconsistent_nonempty_listing"
    if package == "error_envelope" or p00.get("body_status") == 404:
        return True, "0/0_empty_or_error"
    if count == 0:
        return True, "0/0_empty_or_error"
    return False, "0/0_inconsistent"


def conclude_range_semantics(probes: list[dict[str, Any]]) -> dict[str, Any]:
    """Derive indexing conclusion from 0/0, 1/1, 0/9, 1/10 probe summaries."""
    by_label = {p["label"]: p for p in probes}
    p00 = by_label.get("0_0")
    p11 = by_label.get("1_1")
    p09 = by_label.get("0_9")
    p110 = by_label.get("1_10")

    evidence: list[str] = []
    if not all([p00, p11, p09, p110]):
        return {
            "conclusion": "contract_unavailable",
            "evidence": ["missing_probe_summaries"],
            "probes": probes,
            "pr5b_correction_required": False,
        }

    listing_required = (p11, p09, p110)
    listing_valid = [_probe_is_valid_lista_index(p) for p in listing_required]
    any_lista = any(
        p.get("package_type") == "lista_index_pagination" for p in listing_required
    )

    if any_lista:
        evidence.append("lista_index_pagination_envelope_observed")
        for p in (p00, p11, p09, p110):
            pag = p.get("pagination") or {}
            evidence.append(
                f"{p['label']}: count={p['returned_count']} "
                f"path={p['start']}/{p['end']} "
                f"pagination_offset={pag.get('offset')} "
                f"pagination_limit={pag.get('limit')} "
                f"package={p.get('package_type')} "
                f"body_status={p.get('body_status')}"
            )
        zero_ok, zero_note = _zero_limit_ok(p00)
        evidence.append(zero_note)
        evidence.append(f"listing_probes_valid={sum(listing_valid)}/3")

        if not all(listing_valid):
            return {
                "conclusion": "ambiguous"
                if any(listing_valid) or any_lista
                else "contract_unavailable",
                "evidence": evidence
                + ["complete_listing_probe_set_required_for_zero_based_offset_limit"],
                "probes": probes,
                "pr5b_correction_required": False,
                "contract_validation_inconclusive": True,
            }
        if not zero_ok:
            return {
                "conclusion": "ambiguous",
                "evidence": evidence + ["0/0_must_be_empty_or_error_independently"],
                "probes": probes,
                "pr5b_correction_required": False,
                "contract_validation_inconclusive": True,
            }

        evidence.append("path_echoes_pagination_offset_limit=True")
        evidence.append("returned_count_equals_path_limit=True")
        return {
            "conclusion": "zero_based_offset_limit",
            "evidence": evidence,
            "probes": probes,
            "width_interpretation": "second path param is limit (count), not inclusive end",
            "advertised_max_1000": "limit <= 1000",
            "pr5b_correction_required": True,
            "merged_pr5b_assumption": {
                "start_ge_1": True,
                "inclusive": True,
                "width": "end - start + 1",
            },
            "observed_wire_contract": {
                "path": "/APISOCDS/OCDS/listaOCDSAgnoMes/{year}/{month}/{offset}/{limit}",
                "offset_base": 0,
                "second_param": "limit",
                "envelope": "creationDate/version/pagination/data",
            },
        }

    def rejected(p: dict[str, Any]) -> bool:
        status = p.get("http_status")
        return status is None or int(status) >= 400 or p.get("package_type") == "error_envelope"

    if rejected(p00) and not rejected(p11):
        evidence.append("0/0 rejected while 1/1 accepted")
        return {
            "conclusion": "endpoint_rejected_zero",
            "evidence": evidence,
            "probes": probes,
            "pr5b_correction_required": False,
        }

    if any(rejected(p) for p in (p00, p11, p09, p110)):
        return {
            "conclusion": "contract_unavailable",
            "evidence": evidence + ["one_or_more_probes_failed"],
            "probes": probes,
            "pr5b_correction_required": False,
        }

    c00, c11 = int(p00["returned_count"]), int(p11["returned_count"])
    c09, c110 = int(p09["returned_count"]), int(p110["returned_count"])
    evidence.append(f"counts 0/0={c00} 1/1={c11} 0/9={c09} 1/10={c110}")
    if c00 == 0 and c11 == 1 and c110 == 10:
        return {
            "conclusion": "one_based_end_inclusive",
            "evidence": evidence,
            "probes": probes,
            "width_interpretation": "end - start + 1",
            "pr5b_correction_required": False,
        }
    return {
        "conclusion": "ambiguous",
        "evidence": evidence,
        "probes": probes,
        "pr5b_correction_required": False,
        "contract_validation_inconclusive": True,
    }
