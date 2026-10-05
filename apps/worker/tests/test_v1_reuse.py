"""The V1 seam: the worker reuses V1's own functions (D4), and they behave as the capture needs."""

from __future__ import annotations

import pytest

from origenlab_email_pipeline.migration.v2_evidence_stage import manifest as v1_manifest
from origenlab_email_pipeline.parse_mbox import walk_attachments as v1_walk_attachments
from origenlab_email_pipeline.qa.mailbox_intake_inventory import classify_intake_folder as v1_classify
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
