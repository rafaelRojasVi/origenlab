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
from origenlab_email_pipeline.chilecompra_anexo_evidence.acquisition_provenance import (
    AcquisitionProvenance,
    AcquisitionSource,
    AttachmentProvenance,
    CompletenessState,
    FutureChileCompraDocumentApiSource,
    acquisition_source_for_bundle,
    build_acquisition_provenance,
    is_operator_supplied,
    resolve_completeness_state,
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
from origenlab_email_pipeline.chilecompra_anexo_evidence.operator_import import (
    OperatorZipAttachmentSource,
    OperatorZipEntry,
    OperatorZipImportError,
    OperatorZipImportResult,
    import_operator_zip,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.planner import (
    EvidenceBuildConfig,
    build_summary,
    build_tender_bundle,
    write_evidence_outputs,
)

__all__ = [
    "AcquiredAttachment",
    "AcquisitionProvenance",
    "AcquisitionSource",
    "ArchiveLimits",
    "ArchiveMemberRecord",
    "ArchiveSafetyError",
    "AttachmentBudgetError",
    "AttachmentExtraction",
    "AttachmentProvenance",
    "AttachmentRecord",
    "AttachmentStub",
    "CompletenessState",
    "ContentAddressedCache",
    "EvidenceBuildConfig",
    "EvidenceChunk",
    "ExtractionLimits",
    "FutureChileCompraDocumentApiSource",
    "LocalAttachmentSource",
    "OperatorZipAttachmentSource",
    "OperatorZipEntry",
    "OperatorZipImportError",
    "OperatorZipImportResult",
    "PortalAttachmentSource",
    "SourceInventory",
    "TenderAttachmentBundle",
    "acquisition_source_for_bundle",
    "build_acquisition_provenance",
    "build_summary",
    "build_tender_bundle",
    "detect_format",
    "extract_payload",
    "import_operator_zip",
    "is_operator_supplied",
    "resolve_completeness_state",
    "role_tag_for",
    "write_evidence_outputs",
]
