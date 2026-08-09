"""Bounded multi-document evidence acquisition/extraction for ChileCompra anexos.

Read-only pre-PR5F lane: it downloads, accounts for, and extracts every anexo a
licitación publishes. It deliberately feeds no relevance, prospect, queue,
contact, outreach, or persistence decision.
"""

from origenlab_email_pipeline.chilecompra_anexo_evidence.acquire import (
    AcquiredAttachment,
    AttachmentBudgetError,
    AttachmentStub,
    ContentAddressedCache,
    LocalAttachmentSource,
    PortalAttachmentSource,
    SourceInventory,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.archive import (
    ArchiveLimits,
    ArchiveSafetyError,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.detect import (
    detect_format,
    role_tag_for,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.extract import (
    ExtractionLimits,
    extract_payload,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.models import (
    ArchiveMemberRecord,
    AttachmentExtraction,
    AttachmentRecord,
    EvidenceChunk,
    TenderAttachmentBundle,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.planner import (
    EvidenceBuildConfig,
    build_summary,
    build_tender_bundle,
    write_evidence_outputs,
)

__all__ = [
    "AcquiredAttachment",
    "ArchiveLimits",
    "ArchiveMemberRecord",
    "ArchiveSafetyError",
    "AttachmentBudgetError",
    "AttachmentExtraction",
    "AttachmentRecord",
    "AttachmentStub",
    "ContentAddressedCache",
    "EvidenceBuildConfig",
    "EvidenceChunk",
    "ExtractionLimits",
    "LocalAttachmentSource",
    "PortalAttachmentSource",
    "SourceInventory",
    "TenderAttachmentBundle",
    "build_summary",
    "build_tender_bundle",
    "detect_format",
    "extract_payload",
    "role_tag_for",
    "write_evidence_outputs",
]
