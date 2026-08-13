"""Immutable models for guarded local ANEXO opportunity integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from origenlab_email_pipeline.chilecompra_anexo_evidence.models import (
    TenderAttachmentBundle,
)


@dataclass(frozen=True)
class VerifiedAnexoEvidence:
    """Cross-file-verified canonical E1 bundles safe for guarded consumption."""

    bundles: Mapping[str, TenderAttachmentBundle]
    semantic_digest: str
    tender_semantic_digests: Mapping[str, str]
    summary: Mapping[str, Any]
    attachment_manifest: Mapping[str, Any]
    extraction_manifest: Mapping[str, Any]

    @property
    def tender_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.bundles))

    def bundle(self, tender_id: str) -> TenderAttachmentBundle:
        try:
            return self.bundles[tender_id]
        except KeyError as exc:
            raise KeyError(f"unknown verified annex tender: {tender_id}") from exc
