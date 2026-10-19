"""The V1 seam: the worker reuses V1's own functions (D4), and they behave as the capture needs."""

from __future__ import annotations

import pytest

from origenlab_email_pipeline.migration.v2_evidence_stage import manifest as v1_manifest
from origenlab_email_pipeline.parse_mbox import walk_attachments as v1_walk_attachments
from origenlab_email_pipeline.qa.mailbox_intake_inventory import classify_intake_folder as v1_classify
from origenlab_email_pipeline.chilecompra_anexo_evidence import constants as v1_anexo_constants
from origenlab_email_pipeline.chilecompra_anexo_evidence import detect as v1_detect
from origenlab_email_pipeline.chilecompra_anexo_evidence import extract as v1_extract
from origenlab_worker import v1_reuse


def test_the_seam_re_exports_the_v1_objects_themselves() -> None:
    assert v1_reuse.walk_attachments is v1_walk_attachments
    assert v1_reuse.classify_intake_folder is v1_classify
    assert v1_reuse.require_stageable_gmail_payload is v1_manifest._require_stageable_gmail_payload
    assert v1_reuse.ManifestRefused is v1_manifest.ManifestRefused


@pytest.mark.parametrize("label,intake", [
    ("INBOX", "primary_evidence"), ("SENT", "primary_evidence"), ("DRAFT", "metadata_only"),
    ("SPAM", "excluded_spam"), ("TRASH", "excluded_trash"), ("CATEGORY_PERSONAL", "unclassified"),
    ("IMPORTANT", "unclassified"), ("Label_12", "unclassified"),
])
def test_gmail_label_ids_classify_the_way_the_staging_rule_reads_them(label, intake) -> None:
    assert v1_reuse.classify_intake_folder(label) == intake


def test_the_staging_validator_refuses_spam_and_accepts_sent() -> None:
    with pytest.raises(v1_reuse.ManifestRefused):
        v1_reuse.require_stageable_gmail_payload(
            {"intake_class": "primary_evidence", "gmail_labels": ["INBOX", "SPAM"]}, "test")
    v1_reuse.require_stageable_gmail_payload({"intake_class": "primary_evidence", "gmail_labels": ["SENT"]}, "test")


def test_the_seam_re_exports_the_anexo_extraction_objects_themselves() -> None:
    assert v1_reuse.detect_format is v1_detect.detect_format
    assert v1_reuse.extract_payload is v1_extract.extract_payload
    assert v1_reuse.ExtractionLimits is v1_extract.ExtractionLimits
    for name in ("CORRUPT", "ENCRYPTED", "EXTRACTED_EMPTY", "EXTRACTION_FAILED", "EXTRACTION_SUCCESS",
                 "NEEDS_CONVERTER", "NEEDS_OCR", "PARTIAL_DUE_TO_SAFETY_LIMIT", "UNSUPPORTED_FORMAT"):
        assert getattr(v1_reuse, f"OUTCOME_{name}") == getattr(v1_anexo_constants, f"OUTCOME_{name}")
