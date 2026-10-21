"""The V1 functions the Gmail capture reuses — imported here and nowhere else.

Owner decision D4 (spec 2026-10-04): parsing, intake classification, the send-direction hint and
the staging validator are the `origenlab_email_pipeline` functions that produced the 92 staged
`gmail_message` records, so a captured payload means what a staged one means. Slice 8 deletes V1;
before it does, these move into this package and only the imports below change.
"""

from __future__ import annotations

from origenlab_email_pipeline.chilecompra_anexo_evidence import constants as _anexo_constants
from origenlab_email_pipeline.chilecompra_anexo_evidence.detect import detect_format
from origenlab_email_pipeline.chilecompra_anexo_evidence.extract import ExtractionLimits, extract_payload
from origenlab_email_pipeline.historical_quote_register.quote_signal import classify_send_direction
from origenlab_email_pipeline.ingest.gmail_imap import message_from_bytes
from origenlab_email_pipeline.migration.v2_evidence_stage.manifest import (
    ManifestRefused,
)
from origenlab_email_pipeline.migration.v2_evidence_stage.manifest import (
    DELIVERY_REPORT_SENDER,
)
from origenlab_email_pipeline.migration.v2_evidence_stage.manifest import (
    _require_stageable_gmail_payload as require_stageable_gmail_payload,
)
from origenlab_email_pipeline.parse_mbox import date_iso_from_msg, recipients_header, walk_attachments
from origenlab_email_pipeline.qa.mailbox_intake_inventory import classify_intake_folder

# The anexo extraction outcomes (Task 8): re-exported by name so the worker never spells them itself.
OUTCOME_CORRUPT = _anexo_constants.OUTCOME_CORRUPT
OUTCOME_ENCRYPTED = _anexo_constants.OUTCOME_ENCRYPTED
OUTCOME_EXTRACTED_EMPTY = _anexo_constants.OUTCOME_EXTRACTED_EMPTY
OUTCOME_EXTRACTION_FAILED = _anexo_constants.OUTCOME_EXTRACTION_FAILED
OUTCOME_EXTRACTION_SUCCESS = _anexo_constants.OUTCOME_EXTRACTION_SUCCESS
OUTCOME_NEEDS_CONVERTER = _anexo_constants.OUTCOME_NEEDS_CONVERTER
OUTCOME_NEEDS_OCR = _anexo_constants.OUTCOME_NEEDS_OCR
OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT = _anexo_constants.OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT
OUTCOME_UNSUPPORTED_FORMAT = _anexo_constants.OUTCOME_UNSUPPORTED_FORMAT

__all__ = [
    "ExtractionLimits",
    "OUTCOME_CORRUPT",
    "OUTCOME_ENCRYPTED",
    "OUTCOME_EXTRACTED_EMPTY",
    "OUTCOME_EXTRACTION_FAILED",
    "OUTCOME_EXTRACTION_SUCCESS",
    "OUTCOME_NEEDS_CONVERTER",
    "OUTCOME_NEEDS_OCR",
    "OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT",
    "OUTCOME_UNSUPPORTED_FORMAT",
    "detect_format",
    "extract_payload",
    "ManifestRefused",
    "classify_intake_folder",
    "classify_send_direction",
    "date_iso_from_msg",
    "DELIVERY_REPORT_SENDER",
    "message_from_bytes",
    "recipients_header",
    "require_stageable_gmail_payload",
    "walk_attachments",
]
