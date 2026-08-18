"""Bounded importer for a complete attachment bundle a human operator already
downloaded, through their own browser, from Mercado Público's own UI, and
placed on local disk.

This module performs zero network I/O, launches or controls no browser, and
never touches Mercado Público's reCAPTCHA-gated ``ViewAttachment.aspx`` flow
in any way -- it only reads bytes the operator already legitimately holds. It
requires an explicit tender code and an explicit file path; it never infers,
searches, or guesses either.

Everything stays in memory: no ZIP member is ever written to disk, so there is
no on-disk extraction root to escape and no on-disk symlink to resolve.
Container-level safety bounds (member count, per-member size, total size,
compression ratio) reuse the exact primitives ``archive.py`` already uses for
attachment-embedded ZIP expansion (``preflight_archive`` /
``verify_actual_expansion``), so a malicious operator ZIP is held to the same
zip-bomb defenses as a malicious portal attachment. Per-entry issues (zip-slip
path traversal, symlink entries, encrypted entries, one unreadable member) are
excluded from the trusted inventory and recorded, not treated as a reason to
discard the rest of an otherwise-safe ZIP -- this mirrors how
``archive.iter_archive_members`` already treats an unsafe member path as a
per-member skip rather than a whole-archive failure.

Deterministic ordering: entries are returned in original ZIP central-directory
order (``ZipFile.infolist()`` order), not re-sorted by filename, so the
inventory mirrors the order the operator's own zip tool produced.
"""

from __future__ import annotations

import io
import stat as stat_module
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from origenlab_email_pipeline.chilecompra_anexo_evidence.acquire import (
    AcquiredAttachment,
    AttachmentStub,
    SourceInventory,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.archive import (
    ArchiveLimits,
    ArchiveSafetyError,
    is_unsafe_member_path,
    preflight_archive,
    verify_actual_expansion,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.constants import (
    ACQUISITION_SOURCE_OPERATOR_COMPLETE_BUNDLE,
    REASON_OPERATOR_COMPLETENESS_NOT_DECLARED,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.models import sha256_bytes


class OperatorZipImportError(ValueError):
    """The operator-supplied ZIP failed a bounded safety check and was refused."""


def _is_symlink_entry(info: zipfile.ZipInfo) -> bool:
    """A ZIP entry's Unix mode (upper 16 bits of external_attr) marks symlinks.

    A symlink entry's stored bytes are a *path string*, not real file content;
    treating one as a regular attachment would silently poison provenance
    with the wrong bytes under the right-looking filename. Since nothing here
    is ever written to disk, there is no followable on-disk symlink to chase
    -- rejecting the entry itself is the complete defense.
    """
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat_module.S_ISLNK(mode) if mode else False


@dataclass(frozen=True)
class OperatorZipEntry:
    """One safely-validated file pulled from an operator ZIP, in import order."""

    zip_entry_index: int
    original_filename: str
    safe_filename: str
    payload: bytes
    sha256: str
    byte_count: int
    # Points at the zip_entry_index of the first entry with identical bytes,
    # or None if this is the first (or only) occurrence of its content.
    duplicate_of_zip_entry_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "zip_entry_index": self.zip_entry_index,
            "original_filename": self.original_filename,
            "safe_filename": self.safe_filename,
            "sha256": self.sha256,
            "byte_count": self.byte_count,
            "duplicate_of_zip_entry_index": self.duplicate_of_zip_entry_index,
        }


@dataclass(frozen=True)
class OperatorZipImportResult:
    """Everything safely recovered from one operator ZIP, plus what was refused."""

    zip_path: str
    zip_sha256: str
    entries: tuple[OperatorZipEntry, ...]
    # Human-readable, one line per excluded entry (never silent -- every
    # excluded file is named and given a reason).
    rejected_entries: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "zip_path": self.zip_path,
            "zip_sha256": self.zip_sha256,
            "entries": [e.to_dict() for e in self.entries],
            "rejected_entries": list(self.rejected_entries),
        }


def _import_operator_zip_payload(
    payload: bytes,
    *,
    zip_path_label: str,
    limits: ArchiveLimits | None = None,
) -> OperatorZipImportResult:
    """Shared validation core for both a real on-disk ZIP and in-memory bytes.

    ``zip_path_label`` is purely descriptive (``import_operator_zip``'s
    ``str(zip_path)``, or an HTTP upload's synthetic label) -- nothing in
    this function ever touches the filesystem. Raises
    :class:`OperatorZipImportError` when the ZIP as a whole is corrupt,
    unreadable, or exceeds a container-level safety bound (member count,
    per-member size, total uncompressed size, compression ratio). Individual
    unsafe members (zip-slip paths, symlink entries, encrypted entries, one
    unreadable member) are excluded from ``entries`` and listed in
    ``rejected_entries`` instead of failing the whole import, so one bad
    member never costs the operator every other legitimate file in the ZIP.
    """
    active_limits = limits or ArchiveLimits()

    zip_sha256 = sha256_bytes(payload)

    try:
        preflight = preflight_archive(payload, limits=active_limits)
    except ArchiveSafetyError as exc:
        raise OperatorZipImportError(
            f"corrupt or unreadable zip container: {exc}"
        ) from exc
    if preflight.rejected:
        raise OperatorZipImportError(
            "zip rejected by declared-size safety bounds: "
            + ", ".join(preflight.reason_codes)
        )

    try:
        expansion = verify_actual_expansion(payload, limits=active_limits)
    except ArchiveSafetyError as exc:
        raise OperatorZipImportError(
            f"corrupt or unreadable zip container: {exc}"
        ) from exc
    if expansion.rejected:
        raise OperatorZipImportError(
            "zip rejected by actual-expansion safety bounds: "
            + ", ".join(expansion.reason_codes)
        )

    # Lazy import: keeps this module's own top-level imports free of the
    # network-capable chilecompra_api module, matching the same convention
    # LocalAttachmentSource/PortalAttachmentSource already use in acquire.py.
    from origenlab_email_pipeline.chilecompra_api import safe_attachment_filename

    entries: list[OperatorZipEntry] = []
    rejected: list[str] = []
    first_index_by_sha: dict[str, int] = {}

    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            for index, info in enumerate(infos):
                name = info.filename
                if is_unsafe_member_path(name):
                    rejected.append(f"{name!r}: unsafe_member_path (zip-slip/traversal)")
                    continue
                if _is_symlink_entry(info):
                    rejected.append(f"{name!r}: symlink_entry_rejected")
                    continue
                if info.flag_bits & 0x1:
                    rejected.append(f"{name!r}: encrypted_entry_rejected")
                    continue
                cap = active_limits.max_member_uncompressed_bytes
                try:
                    with archive.open(info) as handle:
                        data = handle.read(cap + 1)
                except (zipfile.BadZipFile, RuntimeError, OSError, EOFError) as exc:
                    rejected.append(f"{name!r}: unreadable ({type(exc).__name__})")
                    continue
                if len(data) > cap:
                    # Actual-expansion verification above already bounds the
                    # whole container; this is a defensive per-member backstop.
                    rejected.append(f"{name!r}: exceeds max_member_uncompressed_bytes")
                    continue

                digest = sha256_bytes(data)
                duplicate_of = first_index_by_sha.get(digest)
                if duplicate_of is None:
                    first_index_by_sha[digest] = index

                entries.append(
                    OperatorZipEntry(
                        zip_entry_index=index,
                        original_filename=name,
                        safe_filename=safe_attachment_filename(name),
                        payload=data,
                        sha256=digest,
                        byte_count=len(data),
                        duplicate_of_zip_entry_index=duplicate_of,
                    )
                )
    except zipfile.BadZipFile as exc:
        raise OperatorZipImportError(f"corrupt or unreadable zip container: {exc}") from exc

    return OperatorZipImportResult(
        zip_path=zip_path_label,
        zip_sha256=zip_sha256,
        entries=tuple(entries),
        rejected_entries=tuple(rejected),
    )


def import_operator_zip(
    zip_path: Path,
    *,
    limits: ArchiveLimits | None = None,
) -> OperatorZipImportResult:
    """Validate and load one operator-supplied ZIP already present on disk.

    See :func:`_import_operator_zip_payload` for the full validation
    contract (shared with :func:`import_operator_zip_bytes`). The only
    filesystem access anywhere in this module is the ``read_bytes()`` call
    below.
    """
    try:
        payload = zip_path.read_bytes()
    except OSError as exc:
        raise OperatorZipImportError(
            f"unable to read zip file {zip_path}: {exc}"
        ) from exc
    return _import_operator_zip_payload(payload, zip_path_label=str(zip_path), limits=limits)


def import_operator_zip_bytes(
    payload: bytes,
    *,
    limits: ArchiveLimits | None = None,
    source_label: str = "<uploaded bytes>",
) -> OperatorZipImportResult:
    """Validate and load one operator-supplied ZIP already read into memory.

    Identical validation to :func:`import_operator_zip` (same shared core),
    for a caller that already has the bytes in hand -- e.g. an HTTP upload
    body already read and size-bounded by the caller before this is called.
    There is no filesystem ``Path`` here and nothing is ever written to disk.
    """
    return _import_operator_zip_payload(payload, zip_path_label=source_label, limits=limits)


@dataclass
class OperatorZipAttachmentSource:
    """AttachmentSource backed by an operator ZIP already validated on disk.

    Two-phase inventory/iter_payloads contract, matching
    ``acquire.AttachmentSource`` exactly, so ``build_tender_bundle`` (and
    therefore extraction, T1, and every other downstream layer) treats this
    identically to ``PortalAttachmentSource`` or ``LocalAttachmentSource``:
    none of them care where the bytes came from.

    ``declare_complete`` is a bare, explicit operator assertion -- never
    inferred from file count, filenames, or any other source's result count
    -- that this ZIP is Mercado Público's complete attachment inventory for
    this tender, downloaded by the operator through their own browser. When
    False (the default), a reason code is recorded up front so the resulting
    bundle fails closed to incomplete/unknown, exactly like a source-level
    gated-listing hint does for the legacy HTTP source.

    Two ways to construct one, both funneling into the exact same validation/
    import/provenance pipeline (``import_operator_zip`` /
    ``import_operator_zip_bytes`` share one internal implementation -- see
    ``_import_operator_zip_payload``): ``from_path`` for a ZIP already on
    local disk (the CLI's case), or ``from_bytes`` for bytes already read
    into memory (an HTTP upload body, already size-bounded by the caller --
    this never writes the upload to a temporary file just to satisfy the
    ``zip_path``-shaped CLI seam). Direct keyword construction with
    ``zip_path=`` remains supported unchanged for existing callers.
    """

    tender_code: str
    declare_complete: bool = False
    limits: ArchiveLimits | None = None
    source_kind: str = ACQUISITION_SOURCE_OPERATOR_COMPLETE_BUNDLE
    zip_path: Path | None = None
    payload_bytes: bytes | None = None
    payload_label: str = "<uploaded bytes>"

    def __post_init__(self) -> None:
        if (self.zip_path is None) == (self.payload_bytes is None):
            raise ValueError(
                "OperatorZipAttachmentSource requires exactly one of zip_path "
                "(a real file already on disk) or payload_bytes (bytes already "
                "read into memory) -- never both, never neither"
            )
        self._loaded_result: OperatorZipImportResult | None = None

    @classmethod
    def from_path(
        cls,
        zip_path: Path,
        *,
        tender_code: str,
        declare_complete: bool = False,
        limits: ArchiveLimits | None = None,
    ) -> "OperatorZipAttachmentSource":
        return cls(
            tender_code=tender_code,
            declare_complete=declare_complete,
            limits=limits,
            zip_path=zip_path,
        )

    @classmethod
    def from_bytes(
        cls,
        payload: bytes,
        *,
        tender_code: str,
        declare_complete: bool = False,
        limits: ArchiveLimits | None = None,
        label: str = "<uploaded bytes>",
    ) -> "OperatorZipAttachmentSource":
        return cls(
            tender_code=tender_code,
            declare_complete=declare_complete,
            limits=limits,
            payload_bytes=payload,
            payload_label=label,
        )

    def _load(self) -> OperatorZipImportResult:
        if self._loaded_result is None:
            if self.zip_path is not None:
                self._loaded_result = import_operator_zip(self.zip_path, limits=self.limits)
            else:
                assert self.payload_bytes is not None
                self._loaded_result = import_operator_zip_bytes(
                    self.payload_bytes, limits=self.limits, source_label=self.payload_label
                )
        return self._loaded_result

    def loaded_import_result(self) -> OperatorZipImportResult:
        """Public accessor for the validated import (e.g. for CLI reporting).

        Safe to call before or after ``inventory()``/``iter_payloads()`` --
        the underlying validation runs at most once and is memoized.
        """
        return self._load()

    def inventory(self) -> SourceInventory:
        result = self._load()
        stubs = tuple(
            AttachmentStub(
                listing_ordinal=0,
                row_ordinal=entry.zip_entry_index,
                portal_filename=entry.original_filename,
                safe_filename=entry.safe_filename,
            )
            for entry in result.entries
        )
        incomplete_reason_codes = (
            () if self.declare_complete else (REASON_OPERATOR_COMPLETENESS_NOT_DECLARED,)
        )
        return SourceInventory(
            listing_page_count=1,
            stubs=stubs,
            incomplete_reason_codes=incomplete_reason_codes,
        )

    def iter_payloads(self, inventory: SourceInventory) -> Iterator[AcquiredAttachment]:
        result = self._load()
        by_row = {entry.zip_entry_index: entry for entry in result.entries}
        for stub in inventory.stubs:
            entry = by_row[stub.row_ordinal]
            yield AcquiredAttachment(stub=stub, payload=entry.payload, content_type="")


__all__ = [
    "OperatorZipAttachmentSource",
    "OperatorZipEntry",
    "OperatorZipImportError",
    "OperatorZipImportResult",
    "import_operator_zip",
    "import_operator_zip_bytes",
]
