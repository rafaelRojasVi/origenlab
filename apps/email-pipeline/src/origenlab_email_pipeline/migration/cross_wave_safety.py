"""Cross-wave outbound-safety reconciliation over immutable migration artifacts.

``docs/DATA.md`` §7 records the Wave 1A safety counts, §7.4 the separately
hashed RFC 2047 addendum, and §7.5.1 the Wave 1B combined prior-contact delta.
Each is a *raw* fact about its own snapshot. The migration needs one more
number that none of them carries: how many distinct addresses the two waves
protect **together**, once their overlap is removed.

That number is measured here, never asserted::

    wave1a_safety      = deduplicate(wave1a_contacted_union ∪ wave1a_rfc2047_addendum)
    cross_wave_safety  = deduplicate(wave1a_safety ∪ wave1b_combined_prior_contact)

``8,580 + 2,075`` is an upper bound, not a total. The two waves share an
unknown number of addresses, and the only honest way to learn it is to
reconstruct both sets from the artifacts and intersect them.

Guarantees, each independently sufficient to stop a mistake:

* **Nothing is opened but files.** This module imports no database driver, no
  Gmail client and no network library, and calls none. Its only reuse of
  production code is :func:`candidate_export_gate.normalize_export_email` — the
  one normalized migration address form, the same function Wave 1B used.
* **Every artifact is verified before a record is read.** Each bundle is
  checked in full against its own ``SHA256SUMS``, and a bundle file the
  checksum list does not mention is a refusal, not an omission. Wave 1A's
  ``manifest.json`` and archive are additionally compared with the values
  ``docs/DATA.md`` §7 pins; Wave 1B's ``content_digest_sha256`` is recomputed
  from its manifest and its archive verified against its sidecar. The addendum
  is verified against its own sidecar.
* **Sizes are re-derived, never trusted.** Each reconstructed set is compared
  with the count its manifest and its reconciliation report record. A
  disagreement refuses the run.
* **The sources are read, never written.** No path inside either bundle is
  opened for writing, chmod-ed or renamed, and the tool creates nothing except
  the report and its sidecar, in the operator-named output root.
* **No address leaves.** Addresses live in memory as set members and are never
  printed, logged, written to the report or interpolated into an exception
  message. Neither is a name, an organization, a domain or any free text.

**Permission policy.** A source artifact that is a symlink, is not owned by the
running user, or is group- or world-**writable** is refused outright: anyone
else could have altered the evidence. A source that is merely group- or
world-*readable* is a privacy weakness rather than a tampering risk — the Wave
1A bundle predates the ``0700``/``0600`` construction policy and is stored that
way — so it is recorded as ``private_mode_ok: false``, warned about, and the
run continues. The report and its sidecar this tool writes are always ``0600``.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.candidate_export_gate import normalize_export_email
from origenlab_email_pipeline.migration.bundle import (
    assert_no_collision,
    assert_output_root_private,
    manifest_content_digest,
    write_private_bytes,
)
from origenlab_email_pipeline.migration.integrity import (
    as_refusal,
    assert_not_symlink,
    assert_recorded,
    observe_modes,
    sha256_file,
    verify_bundle_checksums,
    verify_sidecar,
)

#: Bumped whenever the measured quantities or the invariants change shape.
RECONCILER_VERSION = "2.0.0"

#: The one normalized migration address form, reused rather than re-implemented.
NORMALIZER_IDENTITY = "candidate_export_gate.normalize_export_email"

#: Wave 1A is immutable and its hashes are published in ``docs/DATA.md`` §7.
#: They are pinned here so a substituted bundle is caught before it is read.
WAVE1A_MANIFEST_SHA256 = "201b7fab58c4b17fd3e7495b9a175625f9d5ae6882afe72ef9e19e1432510e48"
WAVE1A_ARCHIVE_SHA256 = "776dd73ee6931a006249b893493f9effbb2303493d4d8b580cfea379ffe8631a"

#: ``contact_control.source`` of the §7.4 addendum rows.
ADDENDUM_SOURCE_LABEL = "wave1a_rfc2047_addendum"

_LEDGER_REL = "derived/recipient_ledger.jsonl.gz"
_WAVE1A_RECONCILIATION_REL = "reports/reconciliation_summary.json"
_WAVE1A_EMAIL_SUPPRESSION_REL = "exact/contact_email_suppression.jsonl.gz"
_WAVE1A_DOMAIN_SUPPRESSION_REL = "exact/contact_domain_suppression.jsonl.gz"
_WAVE1A_MANUAL_STATUS_REL = "exact/manual_contact_status.jsonl.gz"

_COMBINED_REL = "delta/combined_prior_contact.jsonl"
_CAMPAIGN_REL = "delta/campaign_prior_contact.jsonl"
_SENT_HISTORY_REL = "delta/sent_history_prior_contact.jsonl"
_OUTREACH_REL = "delta/outreach_prior_contact.jsonl"
_WAVE1B_EMAIL_SUPPRESSION_REL = "delta/contact_email_suppression.jsonl"
_WAVE1B_DOMAIN_SUPPRESSION_REL = "delta/contact_domain_suppression.jsonl"
_WAVE1B_MANUAL_STATUS_REL = "delta/manual_contact_status.jsonl"
_WAVE1B_RECONCILIATION_REL = "reports/reconciliation.json"

#: The three prior-contact source categories Wave 1B reports separately.
SOURCE_CATEGORIES = ("campaign_accepted", "sent_history", "outreach_state")

#: The report format. The major version is part of the report file name, so a
#: superseding run never collides with, overwrites or deletes an earlier one.
_REPORT_SUFFIX = "cross_wave_safety_reconciliation_v2.json"

_FORMULA = (
    "wave1a_safety = deduplicate(wave1a_contacted_union | wave1a_rfc2047_addendum); "
    "cross_wave_safety = deduplicate(wave1a_safety | wave1b_combined_prior_contact)"
)

_PRIVACY_STATEMENT = (
    "No address, personal name, organization, domain or operator free text appears "
    "in this report. Every quantity is a count."
)


class ReconciliationRefused(Exception):
    """The reconciliation failed closed.

    Messages carry counts and relative file names only — never an address, a
    domain, an absolute path or any other datum from the artifacts.
    """


@dataclass(frozen=True)
class ArtifactIdentity:
    """What a verified input artifact is, and the hashes proving it."""

    name: str
    sha256: str
    files_verified: int | None = None
    extra: dict[str, Any] | None = None

    def to_report(self) -> dict[str, Any]:
        entry: dict[str, Any] = {"name": self.name, "sha256": self.sha256}
        if self.files_verified is not None:
            entry["files_verified"] = self.files_verified
        if self.extra:
            entry.update(self.extra)
        return entry


@dataclass(frozen=True)
class ReconciliationResult:
    """The written report and the measured numbers it carries."""

    path: Path
    sidecar: Path
    sha256: str
    report: dict[str, Any]
    warnings: tuple[str, ...] = ()
    console_lines: tuple[str, ...] = ()

    @property
    def counts(self) -> dict[str, int]:
        return self.report["counts"]


# --------------------------------------------------------------------------- #
# Integrity and permission verification
# --------------------------------------------------------------------------- #
#
# The rules themselves live in :mod:`migration.integrity`, shared with the
# permission-hardening tool so the two can never drift apart. These wrappers
# only re-raise an integrity failure as this module's refusal type.


def _sha256_file(path: Path) -> str:
    return sha256_file(path)


def _assert_not_symlink(path: Path, label: str) -> None:
    as_refusal(ReconciliationRefused, assert_not_symlink, path, label)


def _verify_bundle(bundle: Path, label: str) -> tuple[dict[str, str], int]:
    verified = as_refusal(ReconciliationRefused, verify_bundle_checksums, bundle, label)
    return verified, len(verified)


def _verify_sidecar(target: Path, sidecar: Path, label: str) -> str:
    return as_refusal(ReconciliationRefused, verify_sidecar, target, sidecar, label)


def _assert_recorded(actual: str, expected: str, label: str) -> None:
    as_refusal(ReconciliationRefused, assert_recorded, actual, expected, label)


def _observe_private_modes(bundle: Path, label: str) -> tuple[dict[str, Any], list[str]]:
    """Record whether a source bundle is owner-only, warning rather than refusing.

    Group/world-*writable* refuses inside :func:`integrity.observe_modes`.
    Group/world-*readable* is a privacy weakness rather than a tampering risk,
    so it is recorded and warned about: refusing it would make a bundle written
    before the owner-only policy unreadable rather than safer.
    """
    observed = as_refusal(ReconciliationRefused, observe_modes, bundle, label)
    warnings: list[str] = []
    if not observed["private_mode_ok"]:
        warnings.append(
            f"{label} is not owner-only: {observed['directories_wider_than_0700']} "
            f"directory(ies) wider than 0700 and {observed['files_wider_than_0600']} "
            "file(s) wider than 0600. Nothing is group- or world-writable, so the "
            "evidence is intact, but the artifact is readable by other local accounts "
            "and should be tightened by the operator."
        )
    return observed, warnings


# --------------------------------------------------------------------------- #
# Address-set reconstruction
# --------------------------------------------------------------------------- #


def _normalized(value: Any, *, origin: str, index: int) -> str:
    """Return the canonical migration address form, or refuse.

    A value that is not a string, that does not normalize, or that is not
    stored byte-for-byte in its normalized form — mixed case included — breaks
    the source contract: both artifacts are documented as carrying the already
    normalized migration address. The address itself never reaches the message.
    """
    if not isinstance(value, str) or not value.strip():
        raise ReconciliationRefused(f"{origin} record {index} has no address")
    normalized = normalize_export_email(value)
    if not normalized or "@" not in normalized:
        raise ReconciliationRefused(
            f"{origin} record {index} holds a value that is not a usable address"
        )
    if normalized != value.strip():
        raise ReconciliationRefused(
            f"{origin} record {index} is not stored in the normalized migration address form"
        )
    return normalized


def _add_unique(target: set[str], address: str, *, origin: str, index: int) -> None:
    if address in target:
        raise ReconciliationRefused(
            f"{origin} repeats an address at record {index}; this artifact is "
            "contractually one row per address"
        )
    target.add(address)


def _iter_jsonl(lines: Iterable[str], *, origin: str) -> Iterator[tuple[int, dict[str, Any]]]:
    index = 0
    for raw in lines:
        if not raw.strip():
            continue
        index += 1
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            raise ReconciliationRefused(f"{origin} record {index} is not valid JSON") from None
        if not isinstance(row, dict):
            raise ReconciliationRefused(f"{origin} record {index} is not a JSON object")
        yield index, row


def _wave1a_contacted_union(ledger: Path) -> set[str]:
    """The exact documented contacted union: ledger rows flagged ``in_contacted_union``."""
    origin = _LEDGER_REL
    addresses: set[str] = set()
    with gzip.open(ledger, "rt", encoding="utf-8") as handle:
        for index, row in _iter_jsonl(handle, origin=origin):
            if "in_contacted_union" not in row:
                raise ReconciliationRefused(
                    f"{origin} record {index} has no in_contacted_union flag; "
                    "the ledger schema is not the documented one"
                )
            if not row.get("in_contacted_union"):
                continue
            address = _normalized(row.get("email_norm"), origin=origin, index=index)
            _add_unique(addresses, address, origin=origin, index=index)
    if not addresses:
        raise ReconciliationRefused(f"{origin} yielded no contacted-union address")
    return addresses


def _wave1a_addendum(addendum: Path) -> set[str]:
    """The three separately hashed RFC 2047 addresses of ``docs/DATA.md`` §7.4."""
    origin = "the Wave 1A RFC 2047 addendum"
    addresses: set[str] = set()
    lines = addendum.read_text(encoding="utf-8").splitlines()
    for index, row in _iter_jsonl(lines, origin=origin):
        source = row.get("source")
        if source != ADDENDUM_SOURCE_LABEL:
            raise ReconciliationRefused(
                f"{origin} record {index} does not carry source={ADDENDUM_SOURCE_LABEL}"
            )
        address = _normalized(row.get("address"), origin=origin, index=index)
        _add_unique(addresses, address, origin=origin, index=index)
    if not addresses:
        raise ReconciliationRefused(f"{origin} is empty")
    return addresses


def _wave1b_combined(combined: Path) -> set[str]:
    """The Wave 1B deduplicated combined prior-contact delta, rebuilt from the rows."""
    origin = _COMBINED_REL
    addresses: set[str] = set()
    lines = combined.read_text(encoding="utf-8").splitlines()
    for index, row in _iter_jsonl(lines, origin=origin):
        categories = row.get("source_categories")
        if not isinstance(categories, list) or not categories:
            raise ReconciliationRefused(
                f"{origin} record {index} carries no source_categories; "
                "the combined delta schema is not the documented one"
            )
        address = _normalized(row.get("address"), origin=origin, index=index)
        _add_unique(addresses, address, origin=origin, index=index)
    if not addresses:
        raise ReconciliationRefused(f"{origin} is empty")
    return addresses


def _read_lines(path: Path) -> list[str]:
    """Read a ``.jsonl`` or ``.jsonl.gz`` artifact. An empty file is empty, not absent."""
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return handle.read().splitlines()
    return path.read_text(encoding="utf-8").splitlines()


def _address_set(
    path: Path,
    *,
    origin: str,
    key: str,
    allow_empty: bool = False,
) -> set[str]:
    """One artifact, one normalized address per row, uniqueness enforced."""
    addresses: set[str] = set()
    for index, row in _iter_jsonl(_read_lines(path), origin=origin):
        address = _normalized(row.get(key), origin=origin, index=index)
        _add_unique(addresses, address, origin=origin, index=index)
    if not addresses and not allow_empty:
        raise ReconciliationRefused(f"{origin} is empty")
    return addresses


def _campaign_attribution(path: Path) -> dict[str, tuple[str, ...]]:
    """Wave 1B ``campaign_accepted`` addresses, each with the campaigns that reached it."""
    origin = _CAMPAIGN_REL
    attributed: dict[str, tuple[str, ...]] = {}
    for index, row in _iter_jsonl(_read_lines(path), origin=origin):
        address = _normalized(row.get("address"), origin=origin, index=index)
        if address in attributed:
            raise ReconciliationRefused(
                f"{origin} repeats an address at record {index}; this artifact is "
                "contractually one row per address"
            )
        campaigns = row.get("campaign_ids")
        if not isinstance(campaigns, list) or not campaigns:
            raise ReconciliationRefused(
                f"{origin} record {index} carries no campaign_ids; the artifact does "
                "not support per-campaign attribution"
            )
        if not all(isinstance(value, str) and value.strip() for value in campaigns):
            raise ReconciliationRefused(
                f"{origin} record {index} has a malformed campaign id"
            )
        attributed[address] = tuple(sorted(set(campaigns)))
    if not attributed:
        raise ReconciliationRefused(f"{origin} is empty")
    return attributed


def _reason_code_matrix(
    path: Path,
    *,
    origin: str,
    key: str,
    allow_empty: bool = False,
) -> tuple[set[str], dict[str, int]]:
    """Suppression addresses plus an aggregate reason-code histogram.

    The histogram is counts by recorded code and nothing else — no address, no
    reason text, and no inference about what a code implies. A row with no code
    is counted under ``"<absent>"`` rather than assigned a meaning.
    """
    addresses: set[str] = set()
    codes: dict[str, int] = {}
    for index, row in _iter_jsonl(_read_lines(path), origin=origin):
        address = _normalized(row.get(key), origin=origin, index=index)
        _add_unique(addresses, address, origin=origin, index=index)
        code = row.get("suppression_reason_code")
        if code is None:
            label = "<absent>"
        elif isinstance(code, str) and code.strip():
            label = code.strip()
        else:
            raise ReconciliationRefused(
                f"{origin} record {index} has a malformed suppression_reason_code"
            )
        codes[label] = codes.get(label, 0) + 1
    if not addresses and not allow_empty:
        raise ReconciliationRefused(f"{origin} is empty")
    return addresses, dict(sorted(codes.items()))


def _domain_set(path: Path, *, origin: str, allow_empty: bool = False) -> set[str]:
    """Suppressed registrable domains, already normalized by the source contract."""
    domains: set[str] = set()
    for index, row in _iter_jsonl(_read_lines(path), origin=origin):
        value = row.get("domain_norm")
        if not isinstance(value, str) or not value.strip():
            raise ReconciliationRefused(f"{origin} record {index} has no domain")
        domain = value.strip()
        if domain != domain.lower() or "@" in domain or "." not in domain:
            raise ReconciliationRefused(
                f"{origin} record {index} is not stored in the normalized domain form"
            )
        if domain in domains:
            raise ReconciliationRefused(
                f"{origin} repeats a domain at record {index}; this artifact is "
                "contractually one row per domain"
            )
        domains.add(domain)
    if not domains and not allow_empty:
        raise ReconciliationRefused(f"{origin} is empty")
    return domains


def _status_histogram(
    path: Path, *, origin: str, allow_empty: bool = False
) -> dict[str, int]:
    """Manual contact-status rows counted by recorded status, nothing else.

    Only the status value is read. The organization name, role label, reason and
    evidence on these rows never leave the bundle, and no status is reinterpreted
    as a suppression here — that mapping belongs to the loader, not to a count.
    """
    statuses: dict[str, int] = {}
    for index, row in _iter_jsonl(_read_lines(path), origin=origin):
        status = row.get("status")
        if not isinstance(status, str) or not status.strip():
            raise ReconciliationRefused(f"{origin} record {index} has no status")
        label = status.strip()
        statuses[label] = statuses.get(label, 0) + 1
    if not statuses and not allow_empty:
        raise ReconciliationRefused(f"{origin} is empty")
    return dict(sorted(statuses.items()))


def _overlap(left: set[str], right: set[str]) -> dict[str, int]:
    """The four numbers that describe two sets, measured rather than assumed."""
    return {
        "left": len(left),
        "right": len(right),
        "intersection": len(left & right),
        "left_only": len(left - right),
        "right_only": len(right - left),
        "union": len(left | right),
    }


def _recorded_count(payload: Any, path: tuple[str, ...], *, origin: str) -> int:
    node: Any = payload
    for key in path:
        if not isinstance(node, dict) or key not in node:
            raise ReconciliationRefused(f"{origin} has no {'.'.join(path)}")
        node = node[key]
    if not isinstance(node, int):
        raise ReconciliationRefused(f"{origin}.{'.'.join(path)} is not an integer")
    return node


def _assert_size(measured: int, recorded: int, *, quantity: str, source: str) -> None:
    if measured != recorded:
        raise ReconciliationRefused(
            f"reconstructed {quantity} is {measured}, but {source} records {recorded}; "
            "the artifact and its own manifest disagree"
        )


def _load_json(path: Path, *, origin: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise ReconciliationRefused(f"{origin} is not valid JSON") from None
    if not isinstance(payload, dict):
        raise ReconciliationRefused(f"{origin} is not a JSON object")
    return payload


# --------------------------------------------------------------------------- #
# The reconciliation
# --------------------------------------------------------------------------- #


def reconcile_cross_wave_safety(
    *,
    wave1a_dir: Path,
    wave1a_addendum_path: Path,
    wave1b_dir: Path,
    output_dir: Path,
    wave1a_archive: Path | None = None,
    wave1b_archive: Path | None = None,
    recorded_wave1a_manifest_sha256: str = WAVE1A_MANIFEST_SHA256,
    recorded_wave1a_archive_sha256: str = WAVE1A_ARCHIVE_SHA256,
) -> ReconciliationResult:
    """Measure the cross-wave safety union and write a private, address-free report.

    Args:
        wave1a_dir: the extracted, immutable Wave 1A bundle directory.
        wave1a_addendum_path: the separately hashed RFC 2047 addendum JSONL.
        wave1b_dir: the extracted Wave 1B bundle directory.
        output_dir: where the report is written; refused inside a Git tree.
        wave1a_archive: the Wave 1A ``.tar.gz``; defaults to ``<wave1a_dir>.tar.gz``.
        wave1b_archive: the Wave 1B ``.tar.gz``; defaults to ``<wave1b_dir>.tar.gz``.
        recorded_wave1a_manifest_sha256: the ``docs/DATA.md`` §7 manifest hash Wave
            1A must still have. Overridden only by tests, over synthetic bundles.
        recorded_wave1a_archive_sha256: the ``docs/DATA.md`` §7 archive hash Wave 1A
            must still have. Overridden only by tests, over synthetic bundles.

    Raises:
        ReconciliationRefused: any checksum, schema, uniqueness, size or
            invariant check failed. Nothing is written in that case.
    """
    def _source(value: Path, label: str) -> Path:
        """Expand, refuse a symlink *before* resolving it away, then resolve."""
        candidate = Path(value).expanduser()
        _assert_not_symlink(candidate, label)
        return candidate.resolve()

    wave1a = _source(wave1a_dir, "the Wave 1A bundle")
    wave1b = _source(wave1b_dir, "the Wave 1B bundle")
    addendum = _source(wave1a_addendum_path, "the Wave 1A RFC 2047 addendum")
    archive_a = (
        _source(wave1a_archive, "the Wave 1A archive")
        if wave1a_archive
        else Path(f"{wave1a}.tar.gz")
    )
    archive_b = (
        _source(wave1b_archive, "the Wave 1B archive")
        if wave1b_archive
        else Path(f"{wave1b}.tar.gz")
    )

    warnings: list[str] = []

    # --- integrity, before a single record is read ------------------------- #
    perms_a, warn_a = _observe_private_modes(wave1a, "the Wave 1A bundle")
    perms_b, warn_b = _observe_private_modes(wave1b, "the Wave 1B bundle")
    warnings.extend(warn_a)
    warnings.extend(warn_b)

    digests_a, files_a = _verify_bundle(wave1a, "the Wave 1A bundle")
    digests_b, files_b = _verify_bundle(wave1b, "the Wave 1B bundle")

    manifest_a_sha = _sha256_file(wave1a / "manifest.json")
    _assert_recorded(
        manifest_a_sha, recorded_wave1a_manifest_sha256, "the Wave 1A manifest.json SHA-256"
    )

    archive_a_sha = _verify_sidecar(archive_a, Path(f"{archive_a}.sha256"), "the Wave 1A archive")
    _assert_recorded(
        archive_a_sha, recorded_wave1a_archive_sha256, "the Wave 1A archive SHA-256"
    )

    archive_b_sha = _verify_sidecar(archive_b, Path(f"{archive_b}.sha256"), "the Wave 1B archive")

    addendum_sha = _verify_sidecar(
        addendum, Path(f"{addendum}.sha256"), "the Wave 1A RFC 2047 addendum"
    )

    manifest_b = _load_json(wave1b / "manifest.json", origin="the Wave 1B manifest.json")
    recorded_digest = manifest_b.get("content_digest_sha256")
    recomputed_digest = manifest_content_digest(manifest_b)
    if recorded_digest != recomputed_digest:
        raise ReconciliationRefused(
            "the Wave 1B manifest's content_digest_sha256 does not match the manifest content"
        )
    manifest_b_sha = _sha256_file(wave1b / "manifest.json")

    # --- reconstruct both sets from the records, not the reported totals --- #
    manifest_a = _load_json(wave1a / "manifest.json", origin="the Wave 1A manifest.json")
    recon_a = _load_json(
        wave1a / _WAVE1A_RECONCILIATION_REL, origin=f"the Wave 1A {_WAVE1A_RECONCILIATION_REL}"
    )
    recon_b = _load_json(
        wave1b / _WAVE1B_RECONCILIATION_REL, origin=f"the Wave 1B {_WAVE1B_RECONCILIATION_REL}"
    )

    base_1a = _wave1a_contacted_union(wave1a / _LEDGER_REL)
    _assert_size(
        len(base_1a),
        _recorded_count(manifest_a, ("row_counts", "contacted_union"), origin="the Wave 1A manifest"),
        quantity="Wave 1A contacted union",
        source="the Wave 1A manifest",
    )
    _assert_size(
        len(base_1a),
        _recorded_count(
            recon_a,
            ("recipient_ledger", "contacted_union_count"),
            origin=f"the Wave 1A {_WAVE1A_RECONCILIATION_REL}",
        ),
        quantity="Wave 1A contacted union",
        source=f"the Wave 1A {_WAVE1A_RECONCILIATION_REL}",
    )

    addendum_set = _wave1a_addendum(addendum)
    already_known = addendum_set & base_1a
    if already_known:
        raise ReconciliationRefused(
            f"{len(already_known)} addendum address(es) are already in the Wave 1A contacted "
            "union; docs/DATA.md §7.1 records them as absent from it"
        )

    delta_1b = _wave1b_combined(wave1b / _COMBINED_REL)
    _assert_size(
        len(delta_1b),
        _recorded_count(
            manifest_b, ("row_counts", "delta_combined_prior_contact"), origin="the Wave 1B manifest"
        ),
        quantity="Wave 1B combined prior-contact delta",
        source="the Wave 1B manifest",
    )
    _assert_size(
        len(delta_1b),
        _recorded_count(
            recon_b,
            ("combined_prior_contact", "deduplicated_total"),
            origin=f"the Wave 1B {_WAVE1B_RECONCILIATION_REL}",
        ),
        quantity="Wave 1B combined prior-contact delta",
        source=f"the Wave 1B {_WAVE1B_RECONCILIATION_REL}",
    )

    # --- the three prior-contact sources, separately ----------------------- #
    campaign_by_address = _campaign_attribution(wave1b / _CAMPAIGN_REL)
    campaign_1b = set(campaign_by_address)
    sent_history_1b = _address_set(
        wave1b / _SENT_HISTORY_REL, origin=_SENT_HISTORY_REL, key="address"
    )
    outreach_1b = _address_set(
        wave1b / _OUTREACH_REL, origin=_OUTREACH_REL, key="address", allow_empty=True
    )
    per_source = {
        "campaign_accepted": campaign_1b,
        "sent_history": sent_history_1b,
        "outreach_state": outreach_1b,
    }
    for name, members in per_source.items():
        _assert_size(
            len(members),
            _recorded_count(
                recon_b,
                ("combined_prior_contact", "raw_by_source", name),
                origin=f"the Wave 1B {_WAVE1B_RECONCILIATION_REL}",
            ),
            quantity=f"Wave 1B {name} prior-contact source",
            source=f"the Wave 1B {_WAVE1B_RECONCILIATION_REL}",
        )
    rebuilt_combined = campaign_1b | sent_history_1b | outreach_1b
    if rebuilt_combined != delta_1b:
        raise ReconciliationRefused(
            "the union of the three Wave 1B prior-contact sources does not equal the "
            "bundle's own combined delta; the artifact set is not self-consistent"
        )

    # --- the suppression and manual-control classes ------------------------ #
    supp_1a, supp_codes_1a = _reason_code_matrix(
        wave1a / _WAVE1A_EMAIL_SUPPRESSION_REL,
        origin=_WAVE1A_EMAIL_SUPPRESSION_REL,
        key="email",
    )
    supp_1b, supp_codes_1b = _reason_code_matrix(
        wave1b / _WAVE1B_EMAIL_SUPPRESSION_REL,
        origin=_WAVE1B_EMAIL_SUPPRESSION_REL,
        key="email",
        allow_empty=True,
    )
    _assert_size(
        len(supp_1a),
        _recorded_count(
            manifest_a, ("row_counts", "contact_email_suppression"), origin="the Wave 1A manifest"
        ),
        quantity="Wave 1A address suppressions",
        source="the Wave 1A manifest",
    )
    _assert_size(
        len(supp_1b),
        _recorded_count(
            manifest_b,
            ("row_counts", "delta_contact_email_suppression"),
            origin="the Wave 1B manifest",
        ),
        quantity="Wave 1B address suppressions",
        source="the Wave 1B manifest",
    )

    domains_1a = _domain_set(
        wave1a / _WAVE1A_DOMAIN_SUPPRESSION_REL, origin=_WAVE1A_DOMAIN_SUPPRESSION_REL
    )
    domains_1b = _domain_set(
        wave1b / _WAVE1B_DOMAIN_SUPPRESSION_REL,
        origin=_WAVE1B_DOMAIN_SUPPRESSION_REL,
        allow_empty=True,
    )
    _assert_size(
        len(domains_1a),
        _recorded_count(
            manifest_a, ("row_counts", "contact_domain_suppression"), origin="the Wave 1A manifest"
        ),
        quantity="Wave 1A domain suppressions",
        source="the Wave 1A manifest",
    )
    _assert_size(
        len(domains_1b),
        _recorded_count(
            manifest_b,
            ("row_counts", "delta_contact_domain_suppression"),
            origin="the Wave 1B manifest",
        ),
        quantity="Wave 1B domain suppressions",
        source="the Wave 1B manifest",
    )

    manual_1a = _status_histogram(
        wave1a / _WAVE1A_MANUAL_STATUS_REL, origin=_WAVE1A_MANUAL_STATUS_REL
    )
    manual_1b = _status_histogram(
        wave1b / _WAVE1B_MANUAL_STATUS_REL,
        origin=_WAVE1B_MANUAL_STATUS_REL,
        allow_empty=True,
    )

    # --- the measurement --------------------------------------------------- #
    safety_1a = base_1a | addendum_set
    intersection = safety_1a & delta_1b
    union = safety_1a | delta_1b
    only_1a = safety_1a - delta_1b
    only_1b = delta_1b - safety_1a

    counts = {
        "wave1a_contacted_union": len(base_1a),
        "wave1a_rfc2047_addendum": len(addendum_set),
        "wave1a_safety_combined": len(safety_1a),
        "wave1b_combined_prior_contact": len(delta_1b),
        "cross_wave_intersection": len(intersection),
        "wave1a_only": len(only_1a),
        "wave1b_only": len(only_1b),
        "cross_wave_safety_union": len(union),
    }

    # --- which Wave 1B evidence route produced the overlap ------------------ #
    #
    # The combined overlap is a single number about a deduplicated union. It does
    # not say an address was re-contacted *by a September campaign* — an address
    # reached only by ordinary Sent mail lands in the same union. Each source is
    # therefore intersected with the Wave 1A safety set in its own right.
    by_source = {
        name: {
            **_overlap(safety_1a, members),
            "already_in_wave1a_safety": len(safety_1a & members),
            "new_relative_to_wave1a": len(members - safety_1a),
        }
        for name, members in per_source.items()
    }
    by_source["combined"] = {
        **_overlap(safety_1a, delta_1b),
        "already_in_wave1a_safety": len(intersection),
        "new_relative_to_wave1a": len(only_1b),
    }

    # An exact partition of the Wave 1B delta by which sources hold each address,
    # so evidence reached by two routes is never counted under both.
    membership = {
        "campaign_accepted_only": campaign_1b - sent_history_1b - outreach_1b,
        "sent_history_only": sent_history_1b - campaign_1b - outreach_1b,
        "outreach_state_only": outreach_1b - campaign_1b - sent_history_1b,
        "campaign_and_sent_history": (campaign_1b & sent_history_1b) - outreach_1b,
        "campaign_and_outreach_state": (campaign_1b & outreach_1b) - sent_history_1b,
        "sent_history_and_outreach_state": (sent_history_1b & outreach_1b) - campaign_1b,
        "all_three_sources": campaign_1b & sent_history_1b & outreach_1b,
    }
    source_membership_partition = {
        name: {
            "addresses": len(members),
            "already_in_wave1a_safety": len(members & safety_1a),
            "new_relative_to_wave1a": len(members - safety_1a),
        }
        for name, members in membership.items()
    }

    # --- per-campaign attribution ------------------------------------------- #
    campaign_ids = sorted({cid for ids in campaign_by_address.values() for cid in ids})
    multi_campaign = {a for a, ids in campaign_by_address.items() if len(ids) > 1}
    per_campaign = {}
    for campaign_id in campaign_ids:
        members = {a for a, ids in campaign_by_address.items() if campaign_id in ids}
        per_campaign[campaign_id] = {
            "accepted_addresses": len(members),
            "already_in_wave1a_safety": len(members & safety_1a),
            "new_relative_to_wave1a": len(members - safety_1a),
            "also_in_another_campaign": len(members & multi_campaign),
        }

    # --- the suppression baseline, a different control class ---------------- #
    address_suppression = {
        "wave1a": len(supp_1a),
        "wave1b": len(supp_1b),
        "intersection": len(supp_1a & supp_1b),
        "wave1a_only": len(supp_1a - supp_1b),
        "wave1b_only": len(supp_1b - supp_1a),
        "deduplicated_union": len(supp_1a | supp_1b),
        "reason_code_matrix": {
            "wave1a": supp_codes_1a,
            "wave1b": supp_codes_1b,
            "semantics": (
                "counts by the reason code each row actually records. No code is "
                "mapped to a purpose, a scope or a cause here; that mapping belongs "
                "to the loader's truth table, not to a count."
            ),
        },
    }
    domain_suppression = {
        "wave1a": len(domains_1a),
        "wave1b": len(domains_1b),
        "intersection": len(domains_1a & domains_1b),
        "wave1a_only": len(domains_1a - domains_1b),
        "wave1b_only": len(domains_1b - domains_1a),
        "deduplicated_union": len(domains_1a | domains_1b),
        "note": (
            "V1 domain suppressions carry only free reason text, never a reason "
            "code, so no reason matrix exists for this class."
        ),
    }
    manual_contact_status = {
        "wave1a_by_status": manual_1a,
        "wave1b_delta_by_status": manual_1b,
        "note": (
            "Kept separate, and deliberately not folded into either union. A manual "
            "status is not a suppression row, and nothing here maps one onto the "
            "other: docs/DATA.md §7.1 records that mapping for the loader, which "
            "applies it with an explicit source vocabulary."
        ),
    }

    invariants = [
        {
            "check": "wave1a_safety_combined == wave1a_contacted_union + wave1a_rfc2047_addendum",
            "ok": len(safety_1a) == len(base_1a) + len(addendum_set),
            "detail": "the addendum is disjoint from the contacted union, per docs/DATA.md §7.1",
        },
        {
            "check": "cross_wave_safety_union == wave1a_only + cross_wave_intersection + wave1b_only",
            "ok": len(union) == len(only_1a) + len(intersection) + len(only_1b),
            "detail": "the three parts partition the union",
        },
        {
            "check": (
                "cross_wave_safety_union == wave1a_safety_combined "
                "+ wave1b_combined_prior_contact - cross_wave_intersection"
            ),
            "ok": len(union) == len(safety_1a) + len(delta_1b) - len(intersection),
            "detail": "inclusion-exclusion over the two reconstructed sets",
        },
        {
            "check": "cross_wave_safety_union <= wave1a_safety_combined + wave1b_combined_prior_contact",
            "ok": len(union) <= len(safety_1a) + len(delta_1b),
            "detail": "the naive sum is an upper bound, never the total",
        },
        {
            "check": "reconstructed set sizes equal the sizes both manifests record",
            "ok": True,
            "detail": "verified before the measurement; a mismatch refuses the run",
        },
        {
            "check": (
                "wave1b_combined_prior_contact == "
                "deduplicate(campaign_accepted | sent_history | outreach_state)"
            ),
            "ok": rebuilt_combined == delta_1b,
            "detail": "the bundle's combined delta is exactly the union of its three sources",
        },
        {
            "check": "the source-membership partition covers the Wave 1B delta exactly once",
            "ok": sum(len(m) for m in membership.values()) == len(delta_1b)
            and set().union(*membership.values()) == delta_1b,
            "detail": "seven disjoint cells, so two-route evidence is never double-counted",
        },
        {
            "check": (
                "cross_wave_intersection == the union of the per-source intersections"
            ),
            "ok": len(
                (safety_1a & campaign_1b)
                | (safety_1a & sent_history_1b)
                | (safety_1a & outreach_1b)
            )
            == len(intersection),
            "detail": (
                "the combined overlap is not attributable to one source; the "
                "per-source figures are reported separately and never summed"
            ),
        },
        {
            "check": "per-campaign accepted addresses reconcile with the campaign source",
            "ok": sum(
                entry["accepted_addresses"] for entry in per_campaign.values()
            )
            - sum(
                len(campaign_by_address[a]) - 1 for a in multi_campaign
            )
            == len(campaign_1b),
            "detail": "an address in two campaigns is counted once in the source union",
        },
        {
            "check": (
                "address_suppression.deduplicated_union == wave1a + wave1b - intersection"
            ),
            "ok": address_suppression["deduplicated_union"]
            == address_suppression["wave1a"]
            + address_suppression["wave1b"]
            - address_suppression["intersection"],
            "detail": "inclusion-exclusion over the two reconstructed suppression sets",
        },
        {
            "check": (
                "domain_suppression.deduplicated_union == wave1a + wave1b - intersection"
            ),
            "ok": domain_suppression["deduplicated_union"]
            == domain_suppression["wave1a"]
            + domain_suppression["wave1b"]
            - domain_suppression["intersection"],
            "detail": "inclusion-exclusion over the two reconstructed domain sets",
        },
        {
            "check": "the reason-code matrix totals equal the suppression set sizes",
            "ok": sum(supp_codes_1a.values()) == len(supp_1a)
            and sum(supp_codes_1b.values()) == len(supp_1b),
            "detail": "every suppression row is accounted for by exactly one code bucket",
        },
    ]
    failed = [entry["check"] for entry in invariants if not entry["ok"]]
    if failed:
        raise ReconciliationRefused(
            f"{len(failed)} set invariant(s) failed; the reconstruction is not self-consistent"
        )

    report: dict[str, Any] = {
        "kind": "cross_wave_safety_reconciliation",
        "tool": "reconcile_wave1a_wave1b_safety",
        "tool_version": RECONCILER_VERSION,
        "normalizer": NORMALIZER_IDENTITY,
        "formula": _FORMULA,
        "semantics": (
            "The measured union is the V1 -> V2 migration outbound-safety baseline: every "
            "address either wave records as already contacted. It is prior-contact evidence "
            "for the send gate, not CRM identity truth, and it is not a suppression list."
        ),
        "inputs": {
            "wave1a_bundle": ArtifactIdentity(
                name=wave1a.name,
                sha256=manifest_a_sha,
                files_verified=files_a,
                extra={
                    "sha256_kind": "manifest.json",
                    "archive_sha256": archive_a_sha,
                    "sha256sums_sha256": _sha256_file(wave1a / "SHA256SUMS"),
                    "contacted_union_artifact": _LEDGER_REL,
                    "contacted_union_artifact_sha256": digests_a[_LEDGER_REL],
                },
            ).to_report(),
            "wave1a_rfc2047_addendum": ArtifactIdentity(
                name=addendum.name,
                sha256=addendum_sha,
                extra={"sha256_kind": "file", "source_label": ADDENDUM_SOURCE_LABEL},
            ).to_report(),
            "wave1b_bundle": ArtifactIdentity(
                name=wave1b.name,
                sha256=manifest_b_sha,
                files_verified=files_b,
                extra={
                    "sha256_kind": "manifest.json",
                    "content_digest_sha256": recomputed_digest,
                    "archive_sha256": archive_b_sha,
                    "sha256sums_sha256": _sha256_file(wave1b / "SHA256SUMS"),
                    "combined_delta_artifact": _COMBINED_REL,
                    "combined_delta_artifact_sha256": digests_b[_COMBINED_REL],
                },
            ).to_report(),
        },
        "counts": counts,
        "prior_contact_by_source": by_source,
        "source_membership_partition": source_membership_partition,
        "per_campaign": per_campaign,
        "multi_campaign_addresses": len(multi_campaign),
        "address_suppression": address_suppression,
        "domain_suppression": domain_suppression,
        "manual_contact_status": manual_contact_status,
        "invariants": invariants,
        "permissions": {
            "wave1a_bundle": perms_a,
            "wave1b_bundle": perms_b,
            "report": {"file_mode": "0o600", "output_root_mode": "0o700"},
            "policy": (
                "a symlink, a foreign owner or a group/world-writable source refuses the run; "
                "a merely world-readable source is recorded and warned about"
            ),
        },
        "reads": {
            "databases_opened": 0,
            "gmail_network_calls": 0,
            "hosted_services_contacted": 0,
            "source_bytes_written": 0,
            "note": (
                "Immutable files only. This tool imports no database driver, no mail client "
                "and no network library."
            ),
        },
        "warnings": sorted(warnings),
        "privacy": _PRIVACY_STATEMENT,
    }

    out_dir = assert_output_root_private(output_dir)
    target = out_dir / f"{wave1a.name}__{wave1b.name}_{_REPORT_SUFFIX}"
    sidecar = Path(f"{target}.sha256")
    assert_no_collision(target)
    assert_no_collision(sidecar)

    payload = (
        json.dumps(report, sort_keys=True, ensure_ascii=True, indent=2).encode("utf-8") + b"\n"
    )
    write_private_bytes(target, payload)
    digest = hashlib.sha256(payload).hexdigest()
    write_private_bytes(sidecar, f"{digest}  {target.name}\n".encode("utf-8"))

    campaign_overlap = by_source["campaign_accepted"]["already_in_wave1a_safety"]
    console = [
        f"cross-wave safety reconciliation v{RECONCILER_VERSION} ({NORMALIZER_IDENTITY})",
        f"wave 1A contacted union:        {counts['wave1a_contacted_union']}",
        f"wave 1A rfc2047 addendum:       {counts['wave1a_rfc2047_addendum']}",
        f"wave 1A safety (combined):      {counts['wave1a_safety_combined']}",
        f"wave 1B combined prior contact: {counts['wave1b_combined_prior_contact']}",
        f"cross-wave intersection:        {counts['cross_wave_intersection']}",
        f"wave 1A only:                   {counts['wave1a_only']}",
        f"wave 1B only:                   {counts['wave1b_only']}",
        f"cross-wave safety union:        {counts['cross_wave_safety_union']}",
        "--- which Wave 1B route the overlap came from ---",
        f"campaign_accepted in wave 1A:   {campaign_overlap}",
        f"sent_history in wave 1A:        "
        f"{by_source['sent_history']['already_in_wave1a_safety']}",
        f"outreach_state in wave 1A:      "
        f"{by_source['outreach_state']['already_in_wave1a_safety']}",
        "(per-source overlaps are not summed; addresses reach wave 1A by more than one route)",
        "--- suppression baseline (a different control class) ---",
        f"address suppressions 1A/1B:     "
        f"{address_suppression['wave1a']}/{address_suppression['wave1b']}, "
        f"overlap {address_suppression['intersection']}, "
        f"union {address_suppression['deduplicated_union']}",
        f"domain suppressions 1A/1B:      "
        f"{domain_suppression['wave1a']}/{domain_suppression['wave1b']}, "
        f"overlap {domain_suppression['intersection']}, "
        f"union {domain_suppression['deduplicated_union']}",
        f"report sha256: {digest}",
        "addresses are never printed; the report carries counts only",
        *[f"WARNING: {entry}" for entry in sorted(warnings)],
    ]

    return ReconciliationResult(
        path=target,
        sidecar=sidecar,
        sha256=digest,
        report=report,
        warnings=tuple(sorted(warnings)),
        console_lines=tuple(console),
    )
