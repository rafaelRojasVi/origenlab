"""Verified, read-only access to the Wave 1A / Wave 1B migration artifacts.

Nothing in the importer reads an artifact file directly. Everything goes through
:func:`load_inputs`, which refuses before a single row is parsed when an artifact is a
symlink, is owned by another account, is group- or world-writable, fails its own
``SHA256SUMS``, fails its ``.sha256`` sidecar, or disagrees with the hash
``docs/DATA.md`` §7 pins for it.

The integrity rules themselves live in :mod:`..integrity`, shared with the extractor, the
permission-hardening tool and the cross-wave reconciler. This module adds only the
knowledge of *which* artifacts an import needs and *which* files inside them it reads.

**Read-only by construction.** No function here opens a file for writing, and the bundles
are never modified — Wave 1A in particular is immutable (`docs/DATA.md` §7.5).
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.migration.integrity import (
    ArtifactIntegrityError,
    as_refusal,
    assert_recorded,
    assert_source_safe,
    observe_modes,
    sha256_file,
    verify_bundle_checksums,
    verify_sidecar,
)

#: Hashes `docs/DATA.md` §7 and §7.5 pin. A mismatch is never a warning.
WAVE1A_ARCHIVE_SHA256 = "776dd73ee6931a006249b893493f9effbb2303493d4d8b580cfea379ffe8631a"
WAVE1A_MANIFEST_SHA256 = "201b7fab58c4b17fd3e7495b9a175625f9d5ae6882afe72ef9e19e1432510e48"
WAVE1B_ARCHIVE_SHA256 = "823ec73671809115751c0b354857a667b19382f7ce26db2beb40b02c76304a93"
WAVE1B_MANIFEST_SHA256 = "496d832bb01cc81ef93c25a742df0e15e69124b7ec21bdab6f6bfc96c21c0eb1"
WAVE1B_CONTENT_DIGEST_SHA256 = (
    "32080fa414b096c62b913b9b14b42e80ce6ec6b7e53fbc56b7a982320fd74eae"
)
RECONCILIATION_V2_SHA256 = (
    "ed23ff6f33a175f4e51b2fc8cf10a5f03971469bc857240e18f9791ccd182c46"
)

#: Files the importer reads out of the Wave 1A bundle.
WAVE1A_FILES: dict[str, str] = {
    "recipient_ledger": "derived/recipient_ledger.jsonl.gz",
    "contact_email_suppression": "exact/contact_email_suppression.jsonl.gz",
    "contact_domain_suppression": "exact/contact_domain_suppression.jsonl.gz",
    "manual_contact_status": "exact/manual_contact_status.jsonl.gz",
    "outbound_campaign": "exact/outbound_campaign.jsonl.gz",
    "outbound_campaign_recipient": "exact/outbound_campaign_recipient.jsonl.gz",
    "outbound_send_attempt": "exact/outbound_send_attempt.jsonl.gz",
    "outreach_contact_state": "exact/outreach_contact_state.jsonl.gz",
    "supplier_master": "exact/supplier_master.jsonl.gz",
    "supplier_contact_channel": "exact/supplier_contact_channel.jsonl.gz",
}

#: Files the importer reads out of the Wave 1B bundle.
WAVE1B_FILES: dict[str, str] = {
    "combined_prior_contact": "delta/combined_prior_contact.jsonl",
    "campaign_prior_contact": "delta/campaign_prior_contact.jsonl",
    "sent_history_prior_contact": "delta/sent_history_prior_contact.jsonl",
    "outreach_prior_contact": "delta/outreach_prior_contact.jsonl",
    "contact_email_suppression": "delta/contact_email_suppression.jsonl",
    "contact_domain_suppression": "delta/contact_domain_suppression.jsonl",
    "manual_contact_status": "delta/manual_contact_status.jsonl",
    "outbound_campaign": "exact/outbound_campaign.jsonl",
    "outbound_campaign_recipient": "exact/outbound_campaign_recipient.jsonl",
    "outbound_send_attempt": "exact/outbound_send_attempt.jsonl",
    "remaining_candidates": "derived/remaining_candidates.jsonl",
}


class InputRefused(Exception):
    """An input artifact failed verification. No row was parsed and nothing was written."""


@dataclass(frozen=True)
class ExpectedHashes:
    """The hashes an import run requires its artifacts to have.

    Production always uses :data:`PINNED_HASHES`, the values `docs/DATA.md` §7 records.
    The type exists so a test can verify a *synthetic* bundle against its own hashes
    without weakening the production pinning or monkeypatching a module constant.
    """

    wave1a_archive: str
    wave1a_manifest: str
    wave1b_archive: str
    wave1b_manifest: str
    wave1b_content_digest: str
    reconciliation: str


#: The hashes `docs/DATA.md` §7 and §7.5 pin. Wave 1A is immutable and its hashes never
#: change; a mismatch is a refusal, never a warning.
PINNED_HASHES = ExpectedHashes(
    wave1a_archive=WAVE1A_ARCHIVE_SHA256,
    wave1a_manifest=WAVE1A_MANIFEST_SHA256,
    wave1b_archive=WAVE1B_ARCHIVE_SHA256,
    wave1b_manifest=WAVE1B_MANIFEST_SHA256,
    wave1b_content_digest=WAVE1B_CONTENT_DIGEST_SHA256,
    reconciliation=RECONCILIATION_V2_SHA256,
)


@dataclass(frozen=True)
class ArtifactIdentity:
    """What one verified artifact is, and the hashes proving it.

    The report carries this instead of any path, so an aggregate report never discloses
    the operator's private migration root.
    """

    name: str
    sha256: str
    files_verified: int | None = None
    private_mode_ok: bool | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_report(self) -> dict[str, Any]:
        entry: dict[str, Any] = {"name": self.name, "sha256": self.sha256}
        if self.files_verified is not None:
            entry["files_verified"] = self.files_verified
        if self.private_mode_ok is not None:
            entry["private_mode_ok"] = self.private_mode_ok
        entry.update(self.extra)
        return entry


@dataclass(frozen=True)
class ImportInputs:
    """Every verified artifact an import run reads, already parsed.

    Attributes:
        wave1a: table name → rows, from the Wave 1A bundle.
        wave1b: table name → rows, from the Wave 1B bundle.
        addendum: the three RFC 2047 recovered-address rows (`docs/DATA.md` §7.4).
        reconciliation: the cross-wave reconciliation report `v2` (§7.5.2).
        identities: one :class:`ArtifactIdentity` per verified artifact.
    """

    wave1a: dict[str, list[dict[str, Any]]]
    wave1b: dict[str, list[dict[str, Any]]]
    addendum: list[dict[str, Any]]
    reconciliation: dict[str, Any]
    identities: tuple[ArtifactIdentity, ...]

    def identity_report(self) -> list[dict[str, Any]]:
        return [identity.to_report() for identity in self.identities]


def _refuse(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Run an integrity check, re-raising its failure as :class:`InputRefused`."""
    return as_refusal(InputRefused, fn, *args, **kwargs)


def _iter_jsonl(path: Path, *, origin: str) -> Iterator[dict[str, Any]]:
    """Yield each JSON object of a ``.jsonl`` or ``.jsonl.gz`` file.

    Raises:
        InputRefused: a line is not valid JSON, or is not a JSON object. A malformed
            evidence line is never skipped — skipping would silently shrink a safety set.
    """
    opener = gzip.open if path.name.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as handle:  # type: ignore[operator]
        for index, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise InputRefused(f"{origin} line {index} is not valid JSON: {exc}") from exc
            if not isinstance(row, dict):
                raise InputRefused(f"{origin} line {index} is not a JSON object")
            yield row


def _load_bundle(
    bundle: Path,
    archive: Path,
    *,
    label: str,
    files: dict[str, str],
    expected_archive_sha256: str,
    expected_manifest_sha256: str,
) -> tuple[dict[str, list[dict[str, Any]]], ArtifactIdentity, dict[str, Any]]:
    """Verify one bundle end to end, then parse the files the importer needs."""
    _refuse(assert_source_safe, bundle, label)
    modes = _refuse(observe_modes, bundle, label)
    verified = _refuse(verify_bundle_checksums, bundle, label)

    archive_sha = _refuse(
        verify_sidecar, archive, archive.with_suffix(archive.suffix + ".sha256"), f"{label} archive"
    )
    _refuse(assert_recorded, archive_sha, expected_archive_sha256, f"the {label} archive SHA-256")

    manifest_path = bundle / "manifest.json"
    _refuse(assert_source_safe, manifest_path, f"{label} manifest.json")
    manifest_sha = sha256_file(manifest_path)
    _refuse(
        assert_recorded, manifest_sha, expected_manifest_sha256, f"the {label} manifest.json SHA-256"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    tables: dict[str, list[dict[str, Any]]] = {}
    for name, relpath in files.items():
        target = bundle / relpath
        if not target.is_file():
            raise InputRefused(f"{label} is missing {relpath}, which the importer reads")
        if relpath not in verified:
            raise InputRefused(f"{label}/{relpath} is not covered by SHA256SUMS")
        tables[name] = list(_iter_jsonl(target, origin=f"{label}/{relpath}"))

    identity = ArtifactIdentity(
        name=bundle.name,
        sha256=archive_sha,
        files_verified=len(verified),
        private_mode_ok=bool(modes["private_mode_ok"]),
        extra={"manifest_sha256": manifest_sha},
    )
    return tables, identity, manifest


def load_inputs(
    *,
    wave1a_bundle: Path,
    wave1a_archive: Path,
    wave1a_addendum: Path,
    wave1b_bundle: Path,
    wave1b_archive: Path,
    reconciliation: Path,
    expected: ExpectedHashes = PINNED_HASHES,
) -> ImportInputs:
    """Verify and parse every artifact an import run needs.

    Every argument is a path the operator supplies; none is derived from a repository
    path, because the artifacts live outside Git under the operator's private migration
    root (`docs/STATUS.md` §2.6).

    Returns:
        The parsed :class:`ImportInputs`.

    Raises:
        InputRefused: any artifact fails a tamper, checksum or recorded-hash check.
            Nothing is parsed past the first failure and nothing is ever written.
    """
    wave1a, wave1a_identity, _ = _load_bundle(
        wave1a_bundle,
        wave1a_archive,
        label="wave 1A bundle",
        files=WAVE1A_FILES,
        expected_archive_sha256=expected.wave1a_archive,
        expected_manifest_sha256=expected.wave1a_manifest,
    )
    wave1b, wave1b_identity, wave1b_manifest = _load_bundle(
        wave1b_bundle,
        wave1b_archive,
        label="wave 1B bundle",
        files=WAVE1B_FILES,
        expected_archive_sha256=expected.wave1b_archive,
        expected_manifest_sha256=expected.wave1b_manifest,
    )

    recorded_digest = str(wave1b_manifest.get("content_digest_sha256", ""))
    _refuse(
        assert_recorded,
        recorded_digest,
        expected.wave1b_content_digest,
        "the wave 1B content_digest_sha256",
    )

    addendum_sha = _refuse(
        verify_sidecar,
        wave1a_addendum,
        wave1a_addendum.with_suffix(wave1a_addendum.suffix + ".sha256"),
        "wave 1A RFC 2047 addendum",
    )
    addendum = list(_iter_jsonl(wave1a_addendum, origin="wave 1A RFC 2047 addendum"))

    reconciliation_sha = _refuse(
        verify_sidecar,
        reconciliation,
        reconciliation.with_suffix(reconciliation.suffix + ".sha256"),
        "cross-wave reconciliation report",
    )
    _refuse(
        assert_recorded,
        reconciliation_sha,
        expected.reconciliation,
        "the cross-wave reconciliation report SHA-256",
    )
    report = json.loads(reconciliation.read_text(encoding="utf-8"))

    return ImportInputs(
        wave1a=wave1a,
        wave1b=wave1b,
        addendum=addendum,
        reconciliation=report,
        identities=(
            wave1a_identity,
            ArtifactIdentity(name=wave1a_addendum.name, sha256=addendum_sha),
            wave1b_identity,
            ArtifactIdentity(name=reconciliation.name, sha256=reconciliation_sha),
        ),
    )


__all__ = [
    "PINNED_HASHES",
    "ArtifactIdentity",
    "ExpectedHashes",
    "ArtifactIntegrityError",
    "ImportInputs",
    "InputRefused",
    "load_inputs",
]
