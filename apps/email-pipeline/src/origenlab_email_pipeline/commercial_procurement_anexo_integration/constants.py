"""Contract constants for guarded annex evidence integration."""

from __future__ import annotations

from typing import Final


ANEXO_INTEGRATION_VERSION: Final = "procurement_annex_production_integration_v1"
# Descriptive alias used by the verified-bundle fingerprint payload.
ANEXO_INTEGRATION_CONTRACT_VERSION: Final = ANEXO_INTEGRATION_VERSION
VERIFIED_ANEXO_BUNDLE_DIGEST_ALGORITHM: Final = (
    "procurement_annex_verified_bundle_digest_v1"
)

REQUIRED_EVIDENCE_FILENAMES: Final = (
    "summary.json",
    "attachment_manifest.json",
    "extraction_manifest.json",
    "evidence_chunks.jsonl",
)
