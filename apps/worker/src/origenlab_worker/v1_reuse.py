"""The V1 functions the Gmail capture reuses — imported here and nowhere else.

Owner decision D4 (spec 2026-10-04): parsing, intake classification, the send-direction hint and
the staging validator are the `origenlab_email_pipeline` functions that produced the 92 staged
`gmail_message` records, so a captured payload means what a staged one means. Slice 8 deletes V1;
before it does, these move into this package and only the imports below change.
"""

from __future__ import annotations

from origenlab_email_pipeline.historical_quote_register.quote_signal import classify_send_direction
from origenlab_email_pipeline.ingest.gmail_imap import message_from_bytes
from origenlab_email_pipeline.migration.v2_evidence_stage.manifest import (
    ManifestRefused,
)
from origenlab_email_pipeline.migration.v2_evidence_stage.manifest import (
    _require_stageable_gmail_payload as require_stageable_gmail_payload,
)
from origenlab_email_pipeline.parse_mbox import date_iso_from_msg, recipients_header, walk_attachments
from origenlab_email_pipeline.qa.mailbox_intake_inventory import classify_intake_folder

__all__ = [
    "ManifestRefused",
    "classify_intake_folder",
    "classify_send_direction",
    "date_iso_from_msg",
    "message_from_bytes",
    "recipients_header",
    "require_stageable_gmail_payload",
    "walk_attachments",
]
