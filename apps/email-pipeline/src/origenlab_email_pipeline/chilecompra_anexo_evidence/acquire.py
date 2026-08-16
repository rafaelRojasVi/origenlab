"""Inventory-first, one-body-at-a-time acquisition of anexo payloads.

Sources enumerate every row before any body is fetched, then stream payloads so
the caller never holds the whole corpus in memory. A local source keeps the
whole pipeline testable without network access.
"""

from __future__ import annotations

import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Protocol

from origenlab_email_pipeline.chilecompra_anexo_evidence.constants import (
    DEFAULT_EVIDENCE_MAX_ATTACHMENT_COUNT,
    DEFAULT_EVIDENCE_MAX_TOTAL_BYTES,
    REASON_ATTACHMENT_COUNT_BUDGET_EXCEEDED,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.models import sha256_bytes


class AttachmentBudgetError(RuntimeError):
    """A budget was exceeded before/while fetching bodies; caller must record it."""

    def __init__(self, reason_code: str, message: str, *, discovered: int = 0) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.discovered = discovered


@dataclass(frozen=True)
class AttachmentStub:
    """One discovered row, before its body exists locally."""

    listing_ordinal: int
    row_ordinal: int
    portal_filename: str
    safe_filename: str
    tipo: str = ""
    descripcion: str = ""
    fecha_adjunto: str = ""


@dataclass(frozen=True)
class AcquiredAttachment:
    """One downloaded body plus the stub it belongs to."""

    stub: AttachmentStub
    payload: bytes
    content_type: str = ""
    error: str | None = None


@dataclass(frozen=True)
class SourceInventory:
    listing_page_count: int
    stubs: tuple[AttachmentStub, ...]


class AttachmentSource(Protocol):
    """Two-phase contract: enumerate everything, then stream bodies."""

    source_kind: str

    def inventory(self) -> SourceInventory: ...

    def iter_payloads(
        self, inventory: SourceInventory
    ) -> Iterator[AcquiredAttachment]: ...


@dataclass
class LocalAttachmentSource:
    """Offline source used by tests and by replaying an already-cached corpus."""

    items: list[tuple[str, str, bytes]]
    listing_page_count: int = 1
    source_kind: str = "local"
    listing_ordinals: list[int] | None = None
    failures: dict[int, str] | None = None

    def inventory(self) -> SourceInventory:
        from origenlab_email_pipeline.chilecompra_api import safe_attachment_filename

        stubs: list[AttachmentStub] = []
        for index, (filename, _content_type, _payload) in enumerate(self.items):
            listing_ordinal = (
                self.listing_ordinals[index] if self.listing_ordinals else 0
            )
            stubs.append(
                AttachmentStub(
                    listing_ordinal=listing_ordinal,
                    row_ordinal=index,
                    portal_filename=filename,
                    safe_filename=safe_attachment_filename(filename),
                )
            )
        return SourceInventory(
            listing_page_count=self.listing_page_count, stubs=tuple(stubs)
        )

    def iter_payloads(self, inventory: SourceInventory) -> Iterator[AcquiredAttachment]:
        failures = self.failures or {}
        for stub in inventory.stubs:
            filename, content_type, payload = self.items[stub.row_ordinal]
            if stub.row_ordinal in failures:
                yield AcquiredAttachment(
                    stub=stub,
                    payload=b"",
                    content_type=content_type,
                    error=failures[stub.row_ordinal],
                )
                continue
            yield AcquiredAttachment(
                stub=stub, payload=payload, content_type=content_type
            )


@dataclass
class PortalAttachmentSource:
    """Mercado Público portal source; read-only GET/POST against the public site."""

    detail_url: str
    timeout: float = 30.0
    max_bytes: int = 64 * 1024 * 1024
    html_max_bytes: int = 4 * 1024 * 1024
    max_attachment_count: int = DEFAULT_EVIDENCE_MAX_ATTACHMENT_COUNT
    max_total_bytes: int = DEFAULT_EVIDENCE_MAX_TOTAL_BYTES
    opener: object | None = None
    source_kind: str = "mercadopublico_portal"

    def __post_init__(self) -> None:
        self._inventory = None
        self._session = None

    def _portal_inventory(self):
        from origenlab_email_pipeline.chilecompra_api import (
            list_licitacion_attachments,
            new_portal_opener,
        )

        if self._session is None:
            self._session = self.opener or new_portal_opener()
        if self._inventory is None:
            self._inventory = list_licitacion_attachments(
                self.detail_url,
                timeout=self.timeout,
                html_max_bytes=self.html_max_bytes,
                opener=self._session,
            )
        return self._inventory

    def inventory(self) -> SourceInventory:
        from origenlab_email_pipeline.chilecompra_api import safe_attachment_filename

        portal = self._portal_inventory()
        stubs: list[AttachmentStub] = []
        for row_ordinal, attachment in enumerate(portal.attachments):
            stubs.append(
                AttachmentStub(
                    listing_ordinal=portal.listing_ordinal_of(attachment),
                    row_ordinal=row_ordinal,
                    portal_filename=attachment.nombre,
                    safe_filename=safe_attachment_filename(attachment.nombre),
                    tipo=attachment.tipo,
                    descripcion=attachment.descripcion,
                    fecha_adjunto=attachment.fecha_adjunto,
                )
            )
        return SourceInventory(
            listing_page_count=len(portal.listing_urls), stubs=tuple(stubs)
        )

    def iter_payloads(self, inventory: SourceInventory) -> Iterator[AcquiredAttachment]:
        from origenlab_email_pipeline.chilecompra_api import (
            ChileCompraApiError,
            download_portal_attachment,
        )

        portal = self._portal_inventory()
        discovered = len(portal.attachments)
        if discovered > self.max_attachment_count:
            raise AttachmentBudgetError(
                REASON_ATTACHMENT_COUNT_BUDGET_EXCEEDED,
                f"discovered {discovered} attachments but "
                f"max_attachment_count={self.max_attachment_count}",
                discovered=discovered,
            )
        total = 0
        # Always post back through the session that produced the __VIEWSTATE.
        session = portal.session or self._session
        for stub in inventory.stubs:
            attachment = portal.attachments[stub.row_ordinal]
            fields = portal.listing_form_fields.get(attachment.listing_url, {})

            # No cross-run skip here by design: Mercado Público does not expose
            # a strong per-body freshness validator (content hash/ETag/immutable
            # version) via listing metadata, so a metadata-only match (filename/
            # tipo/descripcion/fecha_adjunto) is never proof the attachment
            # bytes are unchanged. Every opt-in run re-fetches every body.
            try:
                download = download_portal_attachment(
                    attachment,
                    fields,
                    opener=session,
                    timeout=self.timeout,
                    max_bytes=self.max_bytes,
                )
            except ChileCompraApiError as exc:
                yield AcquiredAttachment(
                    stub=stub, payload=b"", error=f"{type(exc).__name__}: {exc}"[:300]
                )
                continue
            total += len(download.content)
            if total > self.max_total_bytes:
                raise AttachmentBudgetError(
                    "total_bytes_budget_exceeded",
                    f"attachment bytes exceed max_total_bytes={self.max_total_bytes}",
                    discovered=discovered,
                )
            yield AcquiredAttachment(
                stub=stub,
                payload=download.content,
                content_type=download.content_type,
            )


@dataclass
class ContentAddressedCache:
    """SHA-256 addressed raw payload store.

    A stored object is trusted only when it is a regular, non-symlink file whose
    bytes hash to the digest in its own name. Anything else (symlink, directory,
    device, truncated or tampered content) is treated as untrusted and is
    atomically replaced by the verified bytes on the next ``put``; ``read``
    fails closed and returns ``None``.
    """

    root: Path

    def path_for(self, digest: str) -> Path:
        return self.root / digest[:2] / f"{digest}.bin"

    def _read_regular_file(self, path: Path) -> bytes | None:
        """Read without ever traversing a symlink at the final path component."""
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(os.fspath(path), flags)
        except OSError:
            return None
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return None
            with os.fdopen(fd, "rb") as handle:
                return handle.read()
        except OSError:
            return None

    def verified_bytes(self, digest: str) -> bytes | None:
        """Return stored bytes only if they still hash to ``digest``."""
        data = self._read_regular_file(self.path_for(digest))
        if data is None:
            return None
        return data if sha256_bytes(data) == digest else None

    def has(self, digest: str) -> bool:
        return self.verified_bytes(digest) is not None

    def put(self, payload: bytes) -> tuple[str, Path]:
        digest = sha256_bytes(payload)
        target = self.path_for(digest)
        if self.verified_bytes(digest) is not None:
            return digest, target

        target.parent.mkdir(parents=True, exist_ok=True)
        # mkstemp creates a uniquely named file exclusively, so two concurrent
        # writers can never collide on, or delete, each other's temp path.
        fd, tmp_name = tempfile.mkstemp(
            dir=os.fspath(target.parent), prefix=f".{target.name}.tmp."
        )
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
            os.chmod(tmp, 0o644)
            # Same-directory rename: atomic, and racing writers publish identical
            # verified bytes, so the winner's content is still correct.
            os.replace(tmp, target)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        return digest, target

    def read(self, digest: str) -> bytes | None:
        return self.verified_bytes(digest)


__all__ = [
    "AcquiredAttachment",
    "AttachmentBudgetError",
    "AttachmentSource",
    "AttachmentStub",
    "ContentAddressedCache",
    "LocalAttachmentSource",
    "PortalAttachmentSource",
    "SourceInventory",
]
