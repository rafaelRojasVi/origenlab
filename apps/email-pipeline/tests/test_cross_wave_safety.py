"""Cross-wave Wave 1A / Wave 1B outbound-safety reconciliation.

``docs/DATA.md`` §7 and §7.5.1 record each wave's raw counts; neither records
how many distinct addresses the two waves protect together, nor which Wave 1B
evidence route produced the overlap. These tests prove the reconciliation
measures all of that rather than assuming it, keeps the prior-contact and
suppression control classes apart, refuses every contract violation, and never
lets an address, a domain or a reason text reach a report, a console line or an
error message.

Both bundles here are synthetic. Addresses and domains are on the reserved
``example.invalid`` domain.
"""

from __future__ import annotations

import ast
import gzip
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from origenlab_email_pipeline.migration.bundle import OutputPathError
from origenlab_email_pipeline.migration.cross_wave_safety import (
    ADDENDUM_SOURCE_LABEL,
    RECONCILER_VERSION,
    ReconciliationRefused,
    reconcile_cross_wave_safety,
)

_WAVE1A_NAME = "20260101T000000Z_wave1a_v1_safety_bundle"
_WAVE1B_NAME = "20260201T000000Z_wave1b_v1_safety_bundle"

# --------------------------------------------------------------------------- #
# The synthetic universe. Every expected count below is derived from these
# literals rather than written twice, so a fixture change cannot silently
# invalidate an assertion.
# --------------------------------------------------------------------------- #

_UNION = [f"union-{n}@example.invalid" for n in range(1, 7)]
_ADDENDUM = [f"decoded-{n}@example.invalid" for n in range(1, 4)]
_SAFETY_1A = sorted({*_UNION, *_ADDENDUM})

#: Wave 1B `campaign_accepted`, with the campaigns that reached each address.
#: `new-2` was reached by both campaigns — the multi-campaign case.
_CAMPAIGN: dict[str, list[str]] = {
    _UNION[0]: ["camp-a"],
    "new-1@example.invalid": ["camp-a"],
    "new-2@example.invalid": ["camp-a", "camp-b"],
}
#: Wave 1B `sent_history`. `decoded-1` overlaps the RFC 2047 addendum, so the
#: combined overlap is larger than the campaign overlap — the whole point.
_SENT_HISTORY = [_ADDENDUM[0], "new-2@example.invalid", "new-3@example.invalid"]
_OUTREACH: list[str] = []

_DELTA_1B = sorted({*_CAMPAIGN, *_SENT_HISTORY, *_OUTREACH})
_ALL_ADDRESSES = sorted({*_SAFETY_1A, *_DELTA_1B})

_SUPP_1A = {
    "supp-1@example.invalid": "bounce_other",
    "supp-2@example.invalid": "bounce_other",
    "supp-3@example.invalid": "bounce_no_such_user",
    "supp-4@example.invalid": "manual_do_not_contact",
}
_SUPP_1B = {
    "supp-3@example.invalid": "bounce_other",
    "supp-4@example.invalid": "bounce_no_such_user",
    "supp-5@example.invalid": "bounce_no_such_user",
}
_DOMAINS_1A = ["a.example.invalid", "b.example.invalid", "c.example.invalid"]
_DOMAINS_1B = ["c.example.invalid", "d.example.invalid"]

_MANUAL_1A = ["inactive", "active"]
_MANUAL_1B = ["hold"]

_ALL_DOMAINS = sorted({*_DOMAINS_1A, *_DOMAINS_1B})
_ALL_SUPPRESSED = sorted({*_SUPP_1A, *_SUPP_1B})
_EVERY_SECRET = sorted({*_ALL_ADDRESSES, *_ALL_DOMAINS, *_ALL_SUPPRESSED})


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_digests(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): _digest(p) for p in sorted(root.rglob("*")) if p.is_file()
    }


def _write_private(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(0o600)


def _canonical(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, ensure_ascii=True, indent=2).encode("utf-8") + b"\n"


def _jsonl(rows: list[dict[str, Any]]) -> bytes:
    return b"".join(json.dumps(r, sort_keys=True).encode("utf-8") + b"\n" for r in rows)


def _write_gz_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    path.chmod(0o600)


def _write_sha256sums(bundle: Path, relpaths: list[str], *, break_checksum: bool = False) -> None:
    lines = []
    for rel in sorted(relpaths):
        digest = "0" * 64 if break_checksum else _digest(bundle / rel)
        lines.append(f"{digest}  {rel}\n")
    _write_private(bundle / "SHA256SUMS", "".join(lines).encode("utf-8"))


def _write_archive(bundle: Path, *, break_sidecar: bool = False) -> None:
    """A stand-in archive: the tool only hashes it against its sidecar."""
    archive = bundle.parent / f"{bundle.name}.tar.gz"
    _write_private(archive, f"archive-of-{bundle.name}".encode("utf-8"))
    digest = "0" * 64 if break_sidecar else _digest(archive)
    _write_private(Path(f"{archive}.sha256"), f"{digest}  {archive.name}\n".encode("utf-8"))


def _tighten(bundle: Path) -> None:
    for node in [bundle, *bundle.rglob("*")]:
        if node.is_dir():
            node.chmod(0o700)


# --------------------------------------------------------------------------- #
# Fixture builders
# --------------------------------------------------------------------------- #


def _build_wave1a(
    root: Path,
    *,
    union: list[str] | None = None,
    recorded_union_count: int | None = None,
    duplicate_union_row: bool = False,
    break_checksum: bool = False,
    extra_unlisted_file: bool = False,
    drop_flag: bool = False,
    suppressions: dict[str, str] | None = None,
    recorded_suppression_count: int | None = None,
    duplicate_suppression: bool = False,
    malformed_reason_code: bool = False,
    domains: list[str] | None = None,
    duplicate_domain: bool = False,
    unnormalized_domain: bool = False,
) -> Path:
    bundle = root / _WAVE1A_NAME
    addresses = _UNION if union is None else union

    rows = [{"email_norm": a, "in_contacted_union": True} for a in addresses]
    if duplicate_union_row:
        rows.append({"email_norm": addresses[0], "in_contacted_union": True})
    rows.append({"email_norm": "blocked-only@example.invalid", "in_contacted_union": False})
    if drop_flag:
        rows = [{"email_norm": a} for a in addresses]
    _write_gz_jsonl(bundle / "derived" / "recipient_ledger.jsonl.gz", rows)

    supp = _SUPP_1A if suppressions is None else suppressions
    supp_rows: list[dict[str, Any]] = [
        {
            "email": address,
            "suppression_reason_code": code,
            "suppression_reason_text": "never read by the reconciliation",
            "suppression_source": "bounce_handler",
        }
        for address, code in sorted(supp.items())
    ]
    if duplicate_suppression and supp_rows:
        supp_rows.append(dict(supp_rows[0]))
    if malformed_reason_code and supp_rows:
        supp_rows[0] = {**supp_rows[0], "suppression_reason_code": 17}
    _write_gz_jsonl(bundle / "exact" / "contact_email_suppression.jsonl.gz", supp_rows)

    domain_values = list(_DOMAINS_1A if domains is None else domains)
    if duplicate_domain and domain_values:
        domain_values.append(domain_values[0])
    if unnormalized_domain:
        domain_values = ["MiXeD.example.invalid"]
    _write_gz_jsonl(
        bundle / "exact" / "contact_domain_suppression.jsonl.gz",
        [
            {"domain_norm": d, "suppression_reason_text": "never read"}
            for d in domain_values
        ],
    )

    _write_gz_jsonl(
        bundle / "exact" / "manual_contact_status.jsonl.gz",
        [
            {
                "email_norm": f"manual-{n}@example.invalid",
                "status": status,
                "organization_name": "NEVER READ BY THE RECONCILIATION",
                "reason": "never read",
            }
            for n, status in enumerate(_MANUAL_1A, start=1)
        ],
    )

    recorded = len(addresses) if recorded_union_count is None else recorded_union_count
    recorded_supp = (
        len(supp) if recorded_suppression_count is None else recorded_suppression_count
    )
    _write_private(
        bundle / "reports" / "reconciliation_summary.json",
        _canonical({"recipient_ledger": {"contacted_union_count": recorded}}),
    )

    relpaths = [
        "derived/recipient_ledger.jsonl.gz",
        "exact/contact_domain_suppression.jsonl.gz",
        "exact/contact_email_suppression.jsonl.gz",
        "exact/manual_contact_status.jsonl.gz",
        "reports/reconciliation_summary.json",
    ]
    if extra_unlisted_file:
        _write_private(bundle / "derived" / "planted.jsonl", b"{}\n")

    _write_private(
        bundle / "manifest.json",
        _canonical(
            {
                "bundle_name": bundle.name,
                "row_counts": {
                    "contacted_union": recorded,
                    "contact_email_suppression": recorded_supp,
                    "contact_domain_suppression": len(set(domain_values)),
                },
            }
        ),
    )
    _write_sha256sums(bundle, relpaths, break_checksum=break_checksum)
    _write_archive(bundle)
    _tighten(bundle)
    return bundle


def _build_addendum(
    root: Path,
    *,
    addresses: list[str] | None = None,
    duplicate: bool = False,
    malformed: bool = False,
    wrong_source: bool = False,
    break_sidecar: bool = False,
) -> Path:
    values = list(_ADDENDUM if addresses is None else addresses)
    if duplicate:
        values.append(values[0])
    if malformed:
        values.append("not-an-address")

    payload = _jsonl(
        [
            {
                "address": value,
                "source": "something_else" if wrong_source else ADDENDUM_SOURCE_LABEL,
                "kind": "prior_contact",
                "scope": "address",
                "purpose": "marketing",
            }
            for value in values
        ]
    )
    target = root / f"{_WAVE1A_NAME}_rfc2047_addendum.jsonl"
    _write_private(target, payload)
    digest = "0" * 64 if break_sidecar else hashlib.sha256(payload).hexdigest()
    _write_private(Path(f"{target}.sha256"), f"{digest}  {target.name}\n".encode("utf-8"))
    return target


def _build_wave1b(
    root: Path,
    *,
    campaign: dict[str, list[str]] | None = None,
    sent_history: list[str] | None = None,
    outreach: list[str] | None = None,
    combined: list[str] | None = None,
    recorded_delta_count: int | None = None,
    recorded_raw_by_source: dict[str, int] | None = None,
    duplicate_row: bool = False,
    duplicate_campaign_row: bool = False,
    drop_source_categories: bool = False,
    drop_campaign_ids: bool = False,
    break_content_digest: bool = False,
    suppressions: dict[str, str] | None = None,
    domains: list[str] | None = None,
) -> Path:
    bundle = root / _WAVE1B_NAME
    campaign_map = _CAMPAIGN if campaign is None else campaign
    sent = list(_SENT_HISTORY if sent_history is None else sent_history)
    out = list(_OUTREACH if outreach is None else outreach)

    campaign_rows = [
        {"address": address, "campaign_ids": sorted(ids)}
        for address, ids in sorted(campaign_map.items())
    ]
    if drop_campaign_ids and campaign_rows:
        campaign_rows[0] = {"address": campaign_rows[0]["address"]}
    if duplicate_campaign_row and campaign_rows:
        campaign_rows.append(dict(campaign_rows[0]))
    _write_private(bundle / "delta" / "campaign_prior_contact.jsonl", _jsonl(campaign_rows))

    _write_private(
        bundle / "delta" / "sent_history_prior_contact.jsonl",
        _jsonl([{"address": a, "sent_message_count": 1} for a in sent]),
    )
    _write_private(
        bundle / "delta" / "outreach_prior_contact.jsonl",
        _jsonl([{"address": a, "state": "contacted"} for a in out]),
    )

    combined_values = list(
        combined if combined is not None else sorted({*campaign_map, *sent, *out})
    )
    if duplicate_row and combined_values:
        combined_values.append(combined_values[0])
    combined_rows = []
    for address in combined_values:
        row: dict[str, Any] = {"address": address}
        if not drop_source_categories:
            categories = []
            if address in campaign_map:
                categories.append("campaign_accepted")
            if address in sent:
                categories.append("sent_history")
            if address in out:
                categories.append("outreach_state")
            row["source_categories"] = categories or ["campaign_accepted"]
        combined_rows.append(row)
    _write_private(bundle / "delta" / "combined_prior_contact.jsonl", _jsonl(combined_rows))

    supp = _SUPP_1B if suppressions is None else suppressions
    _write_private(
        bundle / "delta" / "contact_email_suppression.jsonl",
        _jsonl(
            [
                {
                    "email": address,
                    "suppression_reason_code": code,
                    "suppression_reason_text_present": True,
                    "suppression_reason_text_sha256": "0" * 64,
                }
                for address, code in sorted(supp.items())
            ]
        ),
    )
    domain_values = list(_DOMAINS_1B if domains is None else domains)
    _write_private(
        bundle / "delta" / "contact_domain_suppression.jsonl",
        _jsonl([{"domain_norm": d} for d in domain_values]),
    )
    _write_private(
        bundle / "delta" / "manual_contact_status.jsonl",
        _jsonl(
            [
                {
                    "email_norm": f"manual-b-{n}@example.invalid",
                    "status": status,
                    "organization_name": "NEVER READ",
                    "reason_present": True,
                }
                for n, status in enumerate(_MANUAL_1B, start=1)
            ]
        ),
    )

    recorded = (
        len(set(combined_values)) if recorded_delta_count is None else recorded_delta_count
    )
    raw_by_source = recorded_raw_by_source or {
        "campaign_accepted": len(campaign_map),
        "sent_history": len(set(sent)),
        "outreach_state": len(set(out)),
    }
    _write_private(
        bundle / "reports" / "reconciliation.json",
        _canonical(
            {
                "combined_prior_contact": {
                    "deduplicated_total": recorded,
                    "raw_by_source": raw_by_source,
                }
            }
        ),
    )
    _write_sha256sums(
        bundle,
        [
            "delta/campaign_prior_contact.jsonl",
            "delta/combined_prior_contact.jsonl",
            "delta/contact_domain_suppression.jsonl",
            "delta/contact_email_suppression.jsonl",
            "delta/manual_contact_status.jsonl",
            "delta/outreach_prior_contact.jsonl",
            "delta/sent_history_prior_contact.jsonl",
            "reports/reconciliation.json",
        ],
    )

    manifest = {
        "bundle_name": bundle.name,
        "row_counts": {
            "delta_combined_prior_contact": recorded,
            "delta_contact_email_suppression": len(supp),
            "delta_contact_domain_suppression": len(set(domain_values)),
        },
        "run": {"started_at_utc": "2026-02-01T00:00:00Z"},
        "wave": "1B",
    }
    content = {k: v for k, v in manifest.items() if k != "run"}
    digest = hashlib.sha256(
        json.dumps(content, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    manifest["content_digest_sha256"] = "0" * 64 if break_content_digest else digest
    _write_private(bundle / "manifest.json", _canonical(manifest))
    _write_archive(bundle)
    _tighten(bundle)
    return bundle


def _run(tmp_path: Path, *, out: str = "out", **kwargs: Any):
    """Build the default synthetic trio and reconcile it."""
    sources = tmp_path / "sources"
    sources.mkdir(exist_ok=True)
    wave1a = kwargs.pop("wave1a", None) or _build_wave1a(sources)
    addendum = kwargs.pop("addendum", None) or _build_addendum(sources)
    wave1b = kwargs.pop("wave1b", None) or _build_wave1b(sources)
    return reconcile_cross_wave_safety(
        wave1a_dir=wave1a,
        wave1a_addendum_path=addendum,
        wave1b_dir=wave1b,
        output_dir=tmp_path / out,
        recorded_wave1a_manifest_sha256=_digest(wave1a / "manifest.json"),
        recorded_wave1a_archive_sha256=_digest(wave1a.parent / f"{wave1a.name}.tar.gz"),
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# The combined measurement
# --------------------------------------------------------------------------- #


def test_the_overlap_is_measured_not_assumed(tmp_path: Path) -> None:
    counts = _run(tmp_path).counts

    assert counts["wave1a_contacted_union"] == len(_UNION)
    assert counts["wave1a_rfc2047_addendum"] == len(_ADDENDUM)
    assert counts["wave1a_safety_combined"] == len(_SAFETY_1A)
    assert counts["wave1b_combined_prior_contact"] == len(_DELTA_1B)

    expected_overlap = len(set(_DELTA_1B) & set(_SAFETY_1A))
    assert counts["cross_wave_intersection"] == expected_overlap
    assert counts["wave1a_only"] == len(_SAFETY_1A) - expected_overlap
    assert counts["wave1b_only"] == len(_DELTA_1B) - expected_overlap
    assert counts["cross_wave_safety_union"] == len(_ALL_ADDRESSES)


def test_the_naive_sum_is_strictly_larger_than_the_measured_union(tmp_path: Path) -> None:
    """8,580 + 2,075 is an upper bound. The whole point of the tool."""
    counts = _run(tmp_path).counts
    naive = counts["wave1a_safety_combined"] + counts["wave1b_combined_prior_contact"]
    assert counts["cross_wave_safety_union"] < naive
    assert counts["cross_wave_safety_union"] == naive - counts["cross_wave_intersection"]


def test_rfc2047_addresses_are_included_in_the_safety_set(tmp_path: Path) -> None:
    """Dropping the addendum must change both the 1A total and the overlap."""
    without = _run(
        tmp_path,
        out="without",
        addendum=_build_addendum(tmp_path / "solo", addresses=["decoded-only@example.invalid"]),
    )
    assert without.counts["wave1a_rfc2047_addendum"] == 1
    assert without.counts["wave1a_safety_combined"] == len(_UNION) + 1
    # `_ADDENDUM[0]` carried the sent_history half of the overlap; only the
    # campaign half survives without it.
    assert without.counts["cross_wave_intersection"] == 1


def test_the_union_is_deduplicated(tmp_path: Path) -> None:
    result = _run(tmp_path)
    assert result.counts["cross_wave_safety_union"] == len(set(_ALL_ADDRESSES))
    assert all(entry["ok"] for entry in result.report["invariants"])


def test_every_invariant_is_recorded_and_holds(tmp_path: Path) -> None:
    report = _run(tmp_path).report
    checks = {entry["check"] for entry in report["invariants"]}
    assert len(checks) == len(report["invariants"]) >= 11
    assert all(entry["ok"] for entry in report["invariants"])


# --------------------------------------------------------------------------- #
# Source-specific overlap — the 1,195 must not be called campaign recontacts
# --------------------------------------------------------------------------- #


def test_the_combined_overlap_is_not_the_campaign_overlap(tmp_path: Path) -> None:
    """The headline number spans three routes; only one of them is a campaign."""
    report = _run(tmp_path).report
    by_source = report["prior_contact_by_source"]

    campaign_overlap = len(set(_CAMPAIGN) & set(_SAFETY_1A))
    sent_overlap = len(set(_SENT_HISTORY) & set(_SAFETY_1A))
    combined_overlap = len(set(_DELTA_1B) & set(_SAFETY_1A))

    assert by_source["campaign_accepted"]["already_in_wave1a_safety"] == campaign_overlap
    assert by_source["sent_history"]["already_in_wave1a_safety"] == sent_overlap
    assert by_source["combined"]["already_in_wave1a_safety"] == combined_overlap
    # The distinction the report exists to make.
    assert campaign_overlap < combined_overlap


def test_each_source_reports_its_own_union_present_and_new(tmp_path: Path) -> None:
    by_source = _run(tmp_path).report["prior_contact_by_source"]

    assert by_source["campaign_accepted"]["right"] == len(_CAMPAIGN)
    assert by_source["campaign_accepted"]["new_relative_to_wave1a"] == len(
        set(_CAMPAIGN) - set(_SAFETY_1A)
    )
    assert by_source["sent_history"]["right"] == len(set(_SENT_HISTORY))
    assert by_source["sent_history"]["new_relative_to_wave1a"] == len(
        set(_SENT_HISTORY) - set(_SAFETY_1A)
    )


def test_an_empty_source_is_reported_as_zero_not_omitted(tmp_path: Path) -> None:
    """`outreach_state` is empty in the real bundle and must still appear."""
    by_source = _run(tmp_path).report["prior_contact_by_source"]
    assert "outreach_state" in by_source
    assert by_source["outreach_state"]["right"] == 0
    assert by_source["outreach_state"]["already_in_wave1a_safety"] == 0
    assert by_source["outreach_state"]["new_relative_to_wave1a"] == 0


def test_a_non_empty_outreach_source_is_measured(tmp_path: Path) -> None:
    """The empty case must not be the only one the code handles."""
    sources = tmp_path / "sources"
    wave1b = _build_wave1b(sources, outreach=[_UNION[1], "new-4@example.invalid"])
    report = _run(tmp_path, wave1b=wave1b).report
    by_source = report["prior_contact_by_source"]
    assert by_source["outreach_state"]["right"] == 2
    assert by_source["outreach_state"]["already_in_wave1a_safety"] == 1
    partition = report["source_membership_partition"]
    assert partition["outreach_state_only"]["addresses"] == 2


def test_the_membership_partition_counts_each_address_exactly_once(tmp_path: Path) -> None:
    report = _run(tmp_path).report
    partition = report["source_membership_partition"]

    campaign, sent = set(_CAMPAIGN), set(_SENT_HISTORY)
    assert partition["campaign_accepted_only"]["addresses"] == len(campaign - sent)
    assert partition["sent_history_only"]["addresses"] == len(sent - campaign)
    assert partition["campaign_and_sent_history"]["addresses"] == len(campaign & sent)
    assert partition["all_three_sources"]["addresses"] == 0

    total = sum(cell["addresses"] for cell in partition.values())
    assert total == report["counts"]["wave1b_combined_prior_contact"]


def test_per_campaign_intersections_are_attributed(tmp_path: Path) -> None:
    report = _run(tmp_path).report
    per_campaign = report["per_campaign"]
    assert set(per_campaign) == {"camp-a", "camp-b"}

    camp_a = {a for a, ids in _CAMPAIGN.items() if "camp-a" in ids}
    assert per_campaign["camp-a"]["accepted_addresses"] == len(camp_a)
    assert per_campaign["camp-a"]["already_in_wave1a_safety"] == len(camp_a & set(_SAFETY_1A))
    assert per_campaign["camp-a"]["new_relative_to_wave1a"] == len(camp_a - set(_SAFETY_1A))


def test_multi_campaign_duplication_is_reported(tmp_path: Path) -> None:
    report = _run(tmp_path).report
    expected = len([a for a, ids in _CAMPAIGN.items() if len(ids) > 1])
    assert report["multi_campaign_addresses"] == expected
    assert report["per_campaign"]["camp-b"]["also_in_another_campaign"] == expected


def test_a_combined_delta_that_is_not_the_union_of_its_sources_is_refused(
    tmp_path: Path,
) -> None:
    wave1b = _build_wave1b(
        tmp_path / "sources", combined=[*_DELTA_1B, "smuggled@example.invalid"]
    )
    with pytest.raises(ReconciliationRefused, match="does not equal the bundle's own"):
        _run(tmp_path, wave1b=wave1b)


def test_a_source_size_that_contradicts_the_bundle_report_is_refused(tmp_path: Path) -> None:
    wave1b = _build_wave1b(
        tmp_path / "sources",
        recorded_raw_by_source={"campaign_accepted": 99, "sent_history": 3, "outreach_state": 0},
    )
    with pytest.raises(ReconciliationRefused, match="campaign_accepted prior-contact source"):
        _run(tmp_path, wave1b=wave1b)


def test_a_campaign_row_without_attribution_is_refused(tmp_path: Path) -> None:
    wave1b = _build_wave1b(tmp_path / "sources", drop_campaign_ids=True)
    with pytest.raises(ReconciliationRefused, match="campaign_ids"):
        _run(tmp_path, wave1b=wave1b)


def test_a_duplicate_campaign_row_is_refused(tmp_path: Path) -> None:
    wave1b = _build_wave1b(tmp_path / "sources", duplicate_campaign_row=True)
    with pytest.raises(ReconciliationRefused, match="repeats an address"):
        _run(tmp_path, wave1b=wave1b)


# --------------------------------------------------------------------------- #
# The suppression baseline — a different control class
# --------------------------------------------------------------------------- #


def test_the_address_suppression_baseline_is_measured(tmp_path: Path) -> None:
    supp = _run(tmp_path).report["address_suppression"]
    a, b = set(_SUPP_1A), set(_SUPP_1B)

    assert supp["wave1a"] == len(a)
    assert supp["wave1b"] == len(b)
    assert supp["intersection"] == len(a & b)
    assert supp["wave1a_only"] == len(a - b)
    assert supp["wave1b_only"] == len(b - a)
    assert supp["deduplicated_union"] == len(a | b)
    assert supp["deduplicated_union"] == len(a) + len(b) - len(a & b)


def test_the_reason_code_matrix_is_aggregate_and_complete(tmp_path: Path) -> None:
    matrix = _run(tmp_path).report["address_suppression"]["reason_code_matrix"]

    expected_1a: dict[str, int] = {}
    for code in _SUPP_1A.values():
        expected_1a[code] = expected_1a.get(code, 0) + 1
    assert matrix["wave1a"] == dict(sorted(expected_1a.items()))
    assert sum(matrix["wave1a"].values()) == len(_SUPP_1A)
    assert sum(matrix["wave1b"].values()) == len(_SUPP_1B)


def test_a_suppression_row_without_a_reason_code_is_counted_not_guessed(
    tmp_path: Path,
) -> None:
    wave1a = _build_wave1a(
        tmp_path / "sources",
        suppressions={"nocode@example.invalid": None},  # type: ignore[dict-item]
    )
    matrix = _run(tmp_path, wave1a=wave1a).report["address_suppression"][
        "reason_code_matrix"
    ]
    assert matrix["wave1a"] == {"<absent>": 1}


def test_a_malformed_reason_code_is_refused(tmp_path: Path) -> None:
    wave1a = _build_wave1a(tmp_path / "sources", malformed_reason_code=True)
    with pytest.raises(ReconciliationRefused, match="suppression_reason_code"):
        _run(tmp_path, wave1a=wave1a)


def test_a_duplicate_suppression_address_is_refused(tmp_path: Path) -> None:
    wave1a = _build_wave1a(tmp_path / "sources", duplicate_suppression=True)
    with pytest.raises(ReconciliationRefused, match="repeats an address"):
        _run(tmp_path, wave1a=wave1a)


def test_the_domain_suppression_baseline_is_measured(tmp_path: Path) -> None:
    domains = _run(tmp_path).report["domain_suppression"]
    a, b = set(_DOMAINS_1A), set(_DOMAINS_1B)

    assert domains["wave1a"] == len(a)
    assert domains["wave1b"] == len(b)
    assert domains["intersection"] == len(a & b)
    assert domains["deduplicated_union"] == len(a | b)
    assert "reason_code_matrix" not in domains


def test_an_empty_wave1b_domain_delta_is_zero_not_a_refusal(tmp_path: Path) -> None:
    wave1b = _build_wave1b(tmp_path / "sources", domains=[])
    domains = _run(tmp_path, wave1b=wave1b).report["domain_suppression"]
    assert domains["wave1b"] == 0
    assert domains["deduplicated_union"] == len(set(_DOMAINS_1A))


def test_a_duplicate_domain_is_refused(tmp_path: Path) -> None:
    wave1a = _build_wave1a(tmp_path / "sources", duplicate_domain=True)
    with pytest.raises(ReconciliationRefused, match="repeats a domain"):
        _run(tmp_path, wave1a=wave1a)


def test_an_unnormalized_domain_is_refused(tmp_path: Path) -> None:
    wave1a = _build_wave1a(tmp_path / "sources", unnormalized_domain=True)
    with pytest.raises(ReconciliationRefused, match="normalized domain form"):
        _run(tmp_path, wave1a=wave1a)


def test_the_manual_contact_status_stays_out_of_every_union(tmp_path: Path) -> None:
    """A hold is not a suppression, and nothing here reinterprets it as one."""
    report = _run(tmp_path).report
    manual = report["manual_contact_status"]
    assert manual["wave1b_delta_by_status"] == {"hold": len(_MANUAL_1B)}
    assert manual["wave1a_by_status"] == {"active": 1, "inactive": 1}

    # Not folded into the suppression union, and not into the prior-contact one.
    assert report["address_suppression"]["deduplicated_union"] == len(
        {*_SUPP_1A, *_SUPP_1B}
    )
    assert report["counts"]["cross_wave_safety_union"] == len(_ALL_ADDRESSES)


def test_the_suppression_class_never_enters_the_prior_contact_union(
    tmp_path: Path,
) -> None:
    counts = _run(tmp_path).counts
    assert counts["cross_wave_safety_union"] == len(_ALL_ADDRESSES)
    assert not set(_SUPP_1A) & set(_ALL_ADDRESSES)


# --------------------------------------------------------------------------- #
# Checksum and contract verification
# --------------------------------------------------------------------------- #


def test_a_wave1a_checksum_mismatch_is_refused(tmp_path: Path) -> None:
    wave1a = _build_wave1a(tmp_path / "sources", break_checksum=True)
    with pytest.raises(ReconciliationRefused, match="SHA256SUMS mismatch"):
        _run(tmp_path, wave1a=wave1a)


def test_a_bundle_file_missing_from_sha256sums_is_refused(tmp_path: Path) -> None:
    wave1a = _build_wave1a(tmp_path / "sources", extra_unlisted_file=True)
    with pytest.raises(ReconciliationRefused, match="does not list"):
        _run(tmp_path, wave1a=wave1a)


def test_a_changed_wave1a_archive_hash_is_refused(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    wave1a = _build_wave1a(sources)
    with pytest.raises(ReconciliationRefused, match="immutable"):
        reconcile_cross_wave_safety(
            wave1a_dir=wave1a,
            wave1a_addendum_path=_build_addendum(sources),
            wave1b_dir=_build_wave1b(sources),
            output_dir=tmp_path / "out",
            recorded_wave1a_manifest_sha256=_digest(wave1a / "manifest.json"),
            recorded_wave1a_archive_sha256="f" * 64,
        )


def test_a_changed_wave1a_manifest_hash_is_refused(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    wave1a = _build_wave1a(sources)
    with pytest.raises(ReconciliationRefused, match="immutable"):
        reconcile_cross_wave_safety(
            wave1a_dir=wave1a,
            wave1a_addendum_path=_build_addendum(sources),
            wave1b_dir=_build_wave1b(sources),
            output_dir=tmp_path / "out",
            recorded_wave1a_manifest_sha256="f" * 64,
            recorded_wave1a_archive_sha256=_digest(sources / f"{_WAVE1A_NAME}.tar.gz"),
        )


def test_an_addendum_sidecar_mismatch_is_refused(tmp_path: Path) -> None:
    addendum = _build_addendum(tmp_path / "sources", break_sidecar=True)
    with pytest.raises(ReconciliationRefused, match="sidecar"):
        _run(tmp_path, addendum=addendum)


def test_a_wave1b_content_digest_mismatch_is_refused(tmp_path: Path) -> None:
    wave1b = _build_wave1b(tmp_path / "sources", break_content_digest=True)
    with pytest.raises(ReconciliationRefused, match="content_digest_sha256"):
        _run(tmp_path, wave1b=wave1b)


def test_a_duplicate_addendum_address_is_refused(tmp_path: Path) -> None:
    addendum = _build_addendum(tmp_path / "sources", duplicate=True)
    with pytest.raises(ReconciliationRefused, match="repeats an address"):
        _run(tmp_path, addendum=addendum)


def test_a_duplicate_wave1b_combined_row_is_refused(tmp_path: Path) -> None:
    wave1b = _build_wave1b(tmp_path / "sources", duplicate_row=True)
    with pytest.raises(ReconciliationRefused, match="repeats an address"):
        _run(tmp_path, wave1b=wave1b)


def test_a_duplicate_ledger_union_row_is_refused(tmp_path: Path) -> None:
    wave1a = _build_wave1a(tmp_path / "sources", duplicate_union_row=True)
    with pytest.raises(ReconciliationRefused, match="repeats an address"):
        _run(tmp_path, wave1a=wave1a)


def test_a_malformed_address_is_refused(tmp_path: Path) -> None:
    addendum = _build_addendum(tmp_path / "sources", malformed=True)
    with pytest.raises(ReconciliationRefused, match="not a usable address"):
        _run(tmp_path, addendum=addendum)


def test_an_unnormalized_address_is_refused(tmp_path: Path) -> None:
    addendum = _build_addendum(tmp_path / "sources", addresses=["MiXeD-Case@example.invalid"])
    with pytest.raises(ReconciliationRefused, match="normalized migration address form"):
        _run(tmp_path, addendum=addendum)


def test_an_addendum_row_with_the_wrong_source_label_is_refused(tmp_path: Path) -> None:
    addendum = _build_addendum(tmp_path / "sources", wrong_source=True)
    with pytest.raises(ReconciliationRefused, match=ADDENDUM_SOURCE_LABEL):
        _run(tmp_path, addendum=addendum)


def test_an_addendum_address_already_in_the_union_is_refused(tmp_path: Path) -> None:
    """docs/DATA.md §7.1 records the three as absent from the contacted union."""
    addendum = _build_addendum(tmp_path / "sources", addresses=[_UNION[0]])
    with pytest.raises(ReconciliationRefused, match="already in the Wave 1A contacted"):
        _run(tmp_path, addendum=addendum)


def test_a_ledger_without_the_union_flag_is_refused(tmp_path: Path) -> None:
    wave1a = _build_wave1a(tmp_path / "sources", drop_flag=True)
    with pytest.raises(ReconciliationRefused, match="in_contacted_union"):
        _run(tmp_path, wave1a=wave1a)


def test_a_combined_row_without_source_categories_is_refused(tmp_path: Path) -> None:
    wave1b = _build_wave1b(tmp_path / "sources", drop_source_categories=True)
    with pytest.raises(ReconciliationRefused, match="source_categories"):
        _run(tmp_path, wave1b=wave1b)


def test_a_reconstructed_size_that_contradicts_the_manifest_is_refused(
    tmp_path: Path,
) -> None:
    wave1a = _build_wave1a(tmp_path / "sources", recorded_union_count=len(_UNION) + 1)
    with pytest.raises(ReconciliationRefused, match="manifest"):
        _run(tmp_path, wave1a=wave1a)


def test_a_suppression_size_that_contradicts_the_manifest_is_refused(
    tmp_path: Path,
) -> None:
    wave1a = _build_wave1a(tmp_path / "sources", recorded_suppression_count=99)
    with pytest.raises(ReconciliationRefused, match="address suppressions"):
        _run(tmp_path, wave1a=wave1a)


def test_a_wave1b_size_that_contradicts_its_manifest_is_refused(tmp_path: Path) -> None:
    wave1b = _build_wave1b(tmp_path / "sources", recorded_delta_count=99)
    with pytest.raises(ReconciliationRefused, match="manifest"):
        _run(tmp_path, wave1b=wave1b)


# --------------------------------------------------------------------------- #
# Privacy, permissions and immutability
# --------------------------------------------------------------------------- #


def test_no_address_domain_or_free_text_appears_in_the_report_or_console(
    tmp_path: Path,
) -> None:
    result = _run(tmp_path)
    surfaces = [
        result.path.read_text(encoding="utf-8"),
        result.sidecar.read_text(encoding="utf-8"),
        "\n".join(result.console_lines),
        json.dumps(result.report),
    ]
    for surface in surfaces:
        for secret in _EVERY_SECRET:
            assert secret not in surface
        assert "example.invalid" not in surface
        assert "@" not in surface
        assert "NEVER READ" not in surface.upper()


def test_no_address_appears_in_a_refusal_message(tmp_path: Path) -> None:
    addendum = _build_addendum(tmp_path / "sources", addresses=[_UNION[0]])
    with pytest.raises(ReconciliationRefused) as excinfo:
        _run(tmp_path, addendum=addendum)
    message = str(excinfo.value)
    assert _UNION[0] not in message
    assert "example.invalid" not in message


def test_both_source_bundles_are_byte_for_byte_untouched(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    wave1a = _build_wave1a(sources)
    addendum = _build_addendum(sources)
    wave1b = _build_wave1b(sources)
    before = (_tree_digests(wave1a), _digest(addendum), _tree_digests(wave1b))
    modes_before = {
        str(p): stat.S_IMODE(p.lstat().st_mode) for p in sorted(sources.rglob("*"))
    }

    _run(tmp_path, wave1a=wave1a, addendum=addendum, wave1b=wave1b)

    assert (_tree_digests(wave1a), _digest(addendum), _tree_digests(wave1b)) == before
    assert {
        str(p): stat.S_IMODE(p.lstat().st_mode) for p in sorted(sources.rglob("*"))
    } == modes_before


def test_the_report_and_sidecar_are_owner_only(tmp_path: Path) -> None:
    result = _run(tmp_path)
    out = result.path.parent
    assert stat.S_IMODE(out.stat().st_mode) == 0o700
    assert stat.S_IMODE(result.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(result.sidecar.stat().st_mode) == 0o600
    assert list(out.glob(".*.tmp")) == []
    assert result.sidecar.read_text(encoding="utf-8").split()[0] == result.sha256
    assert result.sha256 == _digest(result.path)


def test_a_group_writable_source_bundle_is_refused(tmp_path: Path) -> None:
    wave1a = _build_wave1a(tmp_path / "sources")
    wave1a.chmod(0o770)
    with pytest.raises(ReconciliationRefused, match="group- or world-writable"):
        _run(tmp_path, wave1a=wave1a)


def test_a_symlinked_source_bundle_is_refused(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    real = _build_wave1a(sources)
    link = tmp_path / "linked_bundle"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(ReconciliationRefused, match="symbolic link"):
        reconcile_cross_wave_safety(
            wave1a_dir=link,
            wave1a_addendum_path=_build_addendum(sources),
            wave1b_dir=_build_wave1b(sources),
            output_dir=tmp_path / "out",
            wave1a_archive=sources / f"{_WAVE1A_NAME}.tar.gz",
            recorded_wave1a_manifest_sha256=_digest(real / "manifest.json"),
            recorded_wave1a_archive_sha256=_digest(sources / f"{_WAVE1A_NAME}.tar.gz"),
        )


def test_a_symlink_inside_a_bundle_is_refused(tmp_path: Path) -> None:
    wave1a = _build_wave1a(tmp_path / "sources")
    (wave1a / "derived" / "shadow.jsonl").symlink_to(wave1a / "manifest.json")
    with pytest.raises(ReconciliationRefused, match="symbolic link"):
        _run(tmp_path, wave1a=wave1a)


def test_a_world_readable_source_warns_but_still_reconciles(tmp_path: Path) -> None:
    """Wave 1A predates the owner-only policy; refusing it would hide the evidence."""
    wave1a = _build_wave1a(tmp_path / "sources")
    wave1a.chmod(0o755)
    (wave1a / "manifest.json").chmod(0o644)

    result = _run(tmp_path, wave1a=wave1a)
    perms = result.report["permissions"]["wave1a_bundle"]
    assert perms["private_mode_ok"] is False
    assert perms["files_wider_than_0600"] == 1
    assert any("owner-only" in warning for warning in result.warnings)
    assert result.counts["cross_wave_safety_union"] == len(_ALL_ADDRESSES)


def test_an_output_inside_the_repository_is_refused(tmp_path: Path) -> None:
    inside = Path(__file__).resolve().parent / "cross_wave_forbidden_output"
    with pytest.raises(OutputPathError, match="Git working tree"):
        sources = tmp_path / "sources"
        reconcile_cross_wave_safety(
            wave1a_dir=_build_wave1a(sources),
            wave1a_addendum_path=_build_addendum(sources),
            wave1b_dir=_build_wave1b(sources),
            output_dir=inside,
            recorded_wave1a_manifest_sha256=_digest(sources / _WAVE1A_NAME / "manifest.json"),
            recorded_wave1a_archive_sha256=_digest(sources / f"{_WAVE1A_NAME}.tar.gz"),
        )
    assert not inside.exists()


def test_an_existing_report_is_never_replaced(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    wave1a = _build_wave1a(sources)
    addendum = _build_addendum(sources)
    wave1b = _build_wave1b(sources)
    first = _run(tmp_path, wave1a=wave1a, addendum=addendum, wave1b=wave1b)
    before = first.path.read_bytes()

    with pytest.raises(OutputPathError, match="already exists"):
        _run(tmp_path, wave1a=wave1a, addendum=addendum, wave1b=wave1b)

    assert first.path.read_bytes() == before
    assert list(first.path.parent.glob(".*.tmp")) == []


def test_an_earlier_report_version_is_never_overwritten(tmp_path: Path) -> None:
    """A superseding run writes a new versioned name beside the old artifact."""
    sources = tmp_path / "sources"
    out = tmp_path / "out"
    out.mkdir()
    previous = out / (
        f"{_WAVE1A_NAME}__{_WAVE1B_NAME}_cross_wave_safety_reconciliation.json"
    )
    previous.write_bytes(b'{"tool_version": "1.0.0"}\n')
    previous.chmod(0o600)
    before = previous.read_bytes()

    result = _run(
        tmp_path,
        wave1a=_build_wave1a(sources),
        addendum=_build_addendum(sources),
        wave1b=_build_wave1b(sources),
    )

    assert previous.exists()
    assert previous.read_bytes() == before
    assert result.path != previous
    assert result.path.name.endswith(f"_v{RECONCILER_VERSION.split('.')[0]}.json")


def test_the_report_is_deterministic(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    wave1a = _build_wave1a(sources)
    addendum = _build_addendum(sources)
    wave1b = _build_wave1b(sources)
    one = _run(tmp_path, out="a", wave1a=wave1a, addendum=addendum, wave1b=wave1b)
    two = _run(tmp_path, out="b", wave1a=wave1a, addendum=addendum, wave1b=wave1b)
    assert one.path.read_bytes() == two.path.read_bytes()
    assert one.sha256 == two.sha256
    assert one.path.name == two.path.name


def test_the_report_records_input_identities_and_hashes(tmp_path: Path) -> None:
    report = _run(tmp_path).report
    inputs = report["inputs"]
    assert set(inputs) == {"wave1a_bundle", "wave1a_rfc2047_addendum", "wave1b_bundle"}
    for entry in inputs.values():
        assert len(entry["sha256"]) == 64
    assert len(inputs["wave1b_bundle"]["content_digest_sha256"]) == 64
    assert report["tool"] == "reconcile_wave1a_wave1b_safety"
    assert report["tool_version"] == RECONCILER_VERSION
    assert report["normalizer"] == "candidate_export_gate.normalize_export_email"
    assert report["reads"]["databases_opened"] == 0
    assert report["reads"]["gmail_network_calls"] == 0


# --------------------------------------------------------------------------- #
# Static proof: nothing here can open a database or the network
# --------------------------------------------------------------------------- #

_FORBIDDEN_MODULES = {
    "sqlite3",
    "psycopg",
    "psycopg2",
    "asyncpg",
    "socket",
    "ssl",
    "http",
    "http.client",
    "urllib",
    "urllib.request",
    "requests",
    "httpx",
    "imaplib",
    "smtplib",
    "poplib",
    "ftplib",
    "googleapiclient",
    "google",
}

_SRC = Path(__file__).resolve().parents[1]
_SOURCES = (
    _SRC / "src/origenlab_email_pipeline/migration/cross_wave_safety.py",
    _SRC / "src/origenlab_email_pipeline/migration/integrity.py",
    _SRC / "src/origenlab_email_pipeline/migration/artifact_permissions.py",
    _SRC / "scripts/migration/reconcile_wave1a_wave1b_safety.py",
    _SRC / "scripts/migration/harden_wave1a_artifact_permissions.py",
)


@pytest.mark.parametrize("source", _SOURCES, ids=lambda p: p.name)
def test_the_tool_imports_no_database_or_network_module(source: Path) -> None:
    """A transitive import for a pure helper is fine; opening a connection is not.

    ``candidate_export_gate`` reaches ``business_mart``, which imports ``sqlite3``
    at module level for other callers. What matters is that *these* tools never
    name a driver or a network library, so they have nothing to open.
    """
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & _FORBIDDEN_MODULES


@pytest.mark.parametrize("source", _SOURCES, ids=lambda p: p.name)
def test_the_tool_calls_nothing_that_opens_a_connection(source: Path) -> None:
    forbidden_calls = {
        "connect",
        "connect_readonly",
        "open_source_readonly",
        "urlopen",
        "create_connection",
        "socket",
        "IMAP4",
        "IMAP4_SSL",
        "SMTP",
    }
    tree = ast.parse(source.read_text(encoding="utf-8"))
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                called.add(func.id)
            elif isinstance(func, ast.Attribute):
                called.add(func.attr)
    assert not called & forbidden_calls


def test_the_reconciliation_opens_no_file_for_writing_outside_the_output_root() -> None:
    """``open(...)`` appears once, for hashing, and it is read-binary."""
    for source in (_SOURCES[0], _SOURCES[1]):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "open":
                    modes = [a.value for a in node.args[1:] if isinstance(a, ast.Constant)]
                    assert modes == ["rb"], f"{source.name} opens a file for writing"


def test_the_environment_needs_no_database(tmp_path: Path, monkeypatch) -> None:
    """A poisoned sqlite3.connect proves the run never reaches a database."""
    import sqlite3

    def _explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the reconciliation opened a database")

    monkeypatch.setattr(sqlite3, "connect", _explode)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert _run(tmp_path).counts["cross_wave_safety_union"] == len(_ALL_ADDRESSES)
    assert os.environ.get("DATABASE_URL") is None
