"""Re-derivation of the Wave 1A RFC 2047 addendum from the immutable bundle.

`docs/DATA.md` §7.1 records three RFC 2047 decoded addresses that are absent
from the Wave 1A contacted union and must load as a **separate, independently
hashed** loader input. This proves the derivation is deterministic, verifiable
against the bundle's own ledger, and never edits the bundle.

The bundle used here is synthetic. Addresses are on the reserved
``example.invalid`` domain.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import pytest

from origenlab_email_pipeline.migration.rfc2047_addendum import (
    ADDENDUM_SOURCE_LABEL,
    AddendumRefused,
    derive_wave1a_rfc2047_addendum,
)

_RECOVERED = [
    "decoded-one@example.invalid",
    "decoded-two@example.invalid",
    "decoded-three@example.invalid",
]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_digests(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): _digest(p) for p in sorted(root.rglob("*")) if p.is_file()
    }


def _build_bundle(
    root: Path,
    *,
    recovered: list[str] | None = None,
    ledger_contains: list[str] = (),
    break_checksum: bool = False,
    drop_diagnostic: bool = False,
) -> Path:
    bundle = root / "20260905T042425Z_wave1a_v1_safety_bundle"
    (bundle / "reports").mkdir(parents=True)
    (bundle / "derived").mkdir(parents=True)

    diagnostic = {
        "recovered_addresses_total": 6,
        "recovered_addresses_not_in_contacted_union": (
            _RECOVERED if recovered is None else recovered
        ),
        "note": "Diagnostic only.",
    }
    summary = {"total": 11613}
    if not drop_diagnostic:
        summary["rfc2047_diagnostic"] = diagnostic
    summary_path = bundle / "reports" / "parse_failure_summary.json"
    summary_path.write_text(json.dumps(summary, sort_keys=True), encoding="utf-8")

    ledger_path = bundle / "derived" / "recipient_ledger.jsonl.gz"
    with gzip.open(ledger_path, "wt", encoding="utf-8") as handle:
        handle.write(
            json.dumps({"email_norm": "contacted@example.invalid", "in_contacted_union": True})
            + "\n"
        )
        for address in ledger_contains:
            handle.write(
                json.dumps({"email_norm": address, "in_contacted_union": True}) + "\n"
            )

    lines = []
    for path in (summary_path, ledger_path):
        digest = "0" * 64 if break_checksum else _digest(path)
        lines.append(f"{digest}  {path.relative_to(bundle).as_posix()}\n")
    (bundle / "SHA256SUMS").write_text("".join(sorted(lines)), encoding="utf-8")
    return bundle


def test_derivation_writes_an_independently_hashed_addendum(tmp_path: Path) -> None:
    bundle = _build_bundle(tmp_path)
    result = derive_wave1a_rfc2047_addendum(
        bundle_dir=bundle, output_dir=tmp_path / "out"
    )

    assert result.count == 3
    assert result.source_label == ADDENDUM_SOURCE_LABEL
    rows = [json.loads(line) for line in result.path.read_text(encoding="utf-8").splitlines()]
    assert [r["address"] for r in rows] == sorted(_RECOVERED)
    assert all(r["source"] == ADDENDUM_SOURCE_LABEL for r in rows)
    assert result.sidecar.read_text(encoding="utf-8").split()[0] == result.sha256
    assert result.sha256 == _digest(result.path)


def test_derivation_is_deterministic(tmp_path: Path) -> None:
    bundle = _build_bundle(tmp_path)
    one = derive_wave1a_rfc2047_addendum(bundle_dir=bundle, output_dir=tmp_path / "a")
    two = derive_wave1a_rfc2047_addendum(bundle_dir=bundle, output_dir=tmp_path / "b")
    assert one.sha256 == two.sha256
    assert one.path.read_bytes() == two.path.read_bytes()


def test_the_wave1a_bundle_is_byte_for_byte_untouched(tmp_path: Path) -> None:
    bundle = _build_bundle(tmp_path)
    before = _tree_digests(bundle)
    derive_wave1a_rfc2047_addendum(bundle_dir=bundle, output_dir=tmp_path / "out")
    assert _tree_digests(bundle) == before


def test_a_checksum_mismatch_in_the_bundle_is_refused(tmp_path: Path) -> None:
    bundle = _build_bundle(tmp_path, break_checksum=True)
    with pytest.raises(AddendumRefused, match="SHA256SUMS"):
        derive_wave1a_rfc2047_addendum(bundle_dir=bundle, output_dir=tmp_path / "out")


def test_a_missing_diagnostic_block_is_refused_not_invented(tmp_path: Path) -> None:
    bundle = _build_bundle(tmp_path, drop_diagnostic=True)
    with pytest.raises(AddendumRefused, match="rfc2047_diagnostic"):
        derive_wave1a_rfc2047_addendum(bundle_dir=bundle, output_dir=tmp_path / "out")


def test_an_address_already_in_the_contacted_union_is_refused(tmp_path: Path) -> None:
    bundle = _build_bundle(tmp_path, ledger_contains=[_RECOVERED[0]])
    with pytest.raises(AddendumRefused, match="contacted union"):
        derive_wave1a_rfc2047_addendum(bundle_dir=bundle, output_dir=tmp_path / "out")


def test_an_output_inside_the_repository_is_refused(tmp_path: Path) -> None:
    bundle = _build_bundle(tmp_path)
    inside = Path(__file__).resolve().parent / "rfc2047_forbidden_output"
    with pytest.raises(Exception, match="Git working tree"):
        derive_wave1a_rfc2047_addendum(bundle_dir=bundle, output_dir=inside)
    assert not inside.exists()


def test_an_existing_addendum_is_never_replaced(tmp_path: Path) -> None:
    bundle = _build_bundle(tmp_path)
    out = tmp_path / "out"
    derive_wave1a_rfc2047_addendum(bundle_dir=bundle, output_dir=out)
    with pytest.raises(Exception, match="already exists"):
        derive_wave1a_rfc2047_addendum(bundle_dir=bundle, output_dir=out)


def test_a_count_expectation_only_warns(tmp_path: Path) -> None:
    bundle = _build_bundle(tmp_path)
    result = derive_wave1a_rfc2047_addendum(
        bundle_dir=bundle, output_dir=tmp_path / "out", expect_count=4
    )
    assert result.count == 3
    assert any("4" in w and "3" in w for w in result.warnings)


def test_the_console_summary_never_prints_an_address(tmp_path: Path) -> None:
    bundle = _build_bundle(tmp_path)
    result = derive_wave1a_rfc2047_addendum(bundle_dir=bundle, output_dir=tmp_path / "out")
    text = "\n".join(result.console_lines)
    for address in _RECOVERED:
        assert address not in text
    assert "3" in text


def test_the_addendum_and_its_sidecar_are_owner_only(tmp_path: Path) -> None:
    """The addendum carries real addresses: 0600 in a 0700 root, like the bundle."""
    bundle = _build_bundle(tmp_path)
    out = tmp_path / "out"
    result = derive_wave1a_rfc2047_addendum(bundle_dir=bundle, output_dir=out)

    assert out.stat().st_mode & 0o777 == 0o700
    assert result.path.stat().st_mode & 0o777 == 0o600
    assert result.sidecar.stat().st_mode & 0o777 == 0o600
    assert list(out.glob(".*.tmp")) == []


def test_a_group_writable_addendum_output_root_is_refused(tmp_path: Path) -> None:
    from origenlab_email_pipeline.migration.bundle import OutputPathError

    bundle = _build_bundle(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    out.chmod(0o777)
    with pytest.raises(OutputPathError, match="writable"):
        derive_wave1a_rfc2047_addendum(bundle_dir=bundle, output_dir=out)
    assert not list(out.glob("*addendum*"))
