"""Build a full, structured PREVIEW of an operator-supplied annex bundle.

This is the shared reusable seam behind both the #493 CLI
(``operator_cli/import_tender_annex_bundle.py``, which stays on its own
existing counts-only summary shape unchanged) and an HTTP preview endpoint
(``apps/api``): given an already-constructed
``chilecompra_anexo_evidence.OperatorZipAttachmentSource`` -- built via
``.from_path`` for a ZIP already on local disk, or ``.from_bytes`` for an
HTTP upload body already read into memory and size-bounded by the caller --
this runs the exact same canonical acquisition -> extraction -> T1 pipeline
used everywhere else in this codebase and returns the FULL structured result
as one dict (tender_facts/items/coverage/evidence via
``TenderTermsBundle.to_dict()``), never a reduced counts-only summary, so a
caller can render the same commercial-term intelligence shown for a
published tender -- just with ``published=False``.

Hard invariants, unchanged from #491/#493:
  * Zero network I/O, zero browser automation anywhere in this module.
  * Never publishes (no call to ``publish_tender_terms`` anywhere here) and
    never persists anything to disk/DB -- this is preview-only.
  * ``completeness_state`` defaults to "unknown" unless the caller's
    ``source`` was explicitly constructed with ``declare_complete=True``.
  * ``contact_authorization`` / ``outreach_authorization`` / ``published`` /
    ``persisted`` are always ``False`` in the returned payload.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from origenlab_email_pipeline.chilecompra_anexo_evidence.acquisition_provenance import (
    build_acquisition_provenance,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.constants import (
    ACQUISITION_SOURCE_OPERATOR_COMPLETE_BUNDLE,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.operator_import import (
    OperatorZipAttachmentSource,
    OperatorZipImportError,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.planner import build_tender_bundle

from .extract import extract_tender_terms


def _iso_now(now: datetime) -> str:
    return now.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def build_operator_annex_bundle_preview(
    source: OperatorZipAttachmentSource,
    *,
    tender_code: str,
    now_fn: Any = None,
) -> dict[str, Any]:
    """Run one operator ZIP through the canonical pipeline; return a full preview.

    Never raises :class:`OperatorZipImportError` -- a rejected/corrupt ZIP is
    caught here and returned as ``{"result": "rejected", "error": ...}``
    (data, not an exception), matching #493's CLI convention, so both a CLI
    and an HTTP adapter can treat both outcomes uniformly. An HTTP adapter is
    expected to translate ``"rejected"`` into a structured 4xx response
    itself; this module has no knowledge of HTTP status codes.
    """
    now = (now_fn or (lambda: datetime.now(timezone.utc)))()
    acquired_at = _iso_now(now)

    try:
        bundle = build_tender_bundle(tender_code, source)
    except OperatorZipImportError as exc:
        return {
            "result": "rejected",
            "tender_code": tender_code,
            "error": str(exc),
            "published": False,
            "persisted": False,
        }

    provenance = build_acquisition_provenance(
        bundle,
        acquisition_source=ACQUISITION_SOURCE_OPERATOR_COMPLETE_BUNDLE,
        acquired_at=acquired_at,
        operator_declared_complete=source.declare_complete,
    )

    terms_bundle = extract_tender_terms(bundle)
    import_result = source.loaded_import_result()

    return {
        "result": "imported",
        "tender_code": tender_code,
        "provenance": provenance.to_dict(),
        "archive": {
            "zip_sha256": import_result.zip_sha256,
            "attachments_discovered": bundle.attachments_discovered,
            "attachments_downloaded": bundle.attachments_downloaded,
            "rejected_entries": list(import_result.rejected_entries),
        },
        "bundle_complete": bundle.bundle_complete,
        "incomplete_reason_codes": list(bundle.incomplete_reason_codes),
        "terms": terms_bundle.to_dict(),
        "published": False,
        "persisted": False,
        "contact_authorization": False,
        "outreach_authorization": False,
    }


__all__ = ["build_operator_annex_bundle_preview"]
