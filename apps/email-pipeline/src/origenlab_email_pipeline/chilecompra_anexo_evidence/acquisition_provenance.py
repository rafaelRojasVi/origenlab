"""Typed acquisition provenance shared across every attachment source.

OrigenLab supports (or, for one seam, could someday support) more than one
legitimate way attachment bytes arrive for a tender:

* ``chilecompra_legacy_partial_http`` -- the existing read-only
  ``VerAntecedentes.aspx`` HTTP scraper (``acquire.PortalAttachmentSource``).
  Provably incomplete for many real tenders; fails closed via
  ``constants.REASON_GATED_LISTING_UNREACHABLE`` when the ficha advertises a
  second, CAPTCHA-gated listing surface this source cannot enumerate.
* ``operator_complete_bundle`` -- a ZIP a human operator already downloaded,
  through their own browser, from Mercado Público's own UI, and placed on
  local disk (``operator_import.OperatorZipAttachmentSource``).
* ``future_chilecompra_document_api`` -- an interface-only seam
  (``FutureChileCompraDocumentApiSource`` below) for a possible future
  official document API. No implementation exists.

None of this changes how ``build_tender_bundle`` / extraction / T1 behave:
every source still produces the same ``TenderAttachmentBundle``, and every
downstream layer stays completely agnostic to where the bytes came from. This
module only adds a normalized, auditable record of *how* they arrived,
layered on top of the existing bundle -- it never replaces
``TenderAttachmentBundle.bundle_complete`` or ``TenderTermsCoverage``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator, Literal, Protocol

from origenlab_email_pipeline.chilecompra_anexo_evidence.acquire import (
    AcquiredAttachment,
    SourceInventory,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.constants import (
    ACQUISITION_SOURCE_CHILECOMPRA_LEGACY_PARTIAL_HTTP,
    ACQUISITION_SOURCE_OPERATOR_COMPLETE_BUNDLE,
    ACQUISITION_SOURCES,
    COMPLETENESS_STATE_COMPLETE,
    COMPLETENESS_STATE_INCOMPLETE,
    COMPLETENESS_STATE_UNKNOWN,
    COMPLETENESS_STATES,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.models import TenderAttachmentBundle

AcquisitionSource = Literal[
    "chilecompra_legacy_partial_http",
    "operator_complete_bundle",
    "future_chilecompra_document_api",
]

CompletenessState = Literal["complete", "incomplete", "unknown"]

# Sources whose bytes were placed by a human operator acting outside any
# automation this codebase performs (never true for the legacy HTTP scraper
# or, were it ever implemented, an official API adapter).
_OPERATOR_SUPPLIED_SOURCES: frozenset[str] = frozenset({ACQUISITION_SOURCE_OPERATOR_COMPLETE_BUNDLE})


def is_operator_supplied(acquisition_source: str) -> bool:
    if acquisition_source not in ACQUISITION_SOURCES:
        raise ValueError(f"unknown acquisition source: {acquisition_source!r}")
    return acquisition_source in _OPERATOR_SUPPLIED_SOURCES


# TenderAttachmentBundle.source_kind predates this provenance vocabulary and
# is left exactly as every existing source sets it (e.g.
# PortalAttachmentSource.source_kind == "mercadopublico_portal") -- changing
# that free-form field is out of scope and would risk breaking any existing
# consumer that reads it. This maps a bundle's existing source_kind onto the
# closed acquisition-source vocabulary for callers (CLI, tests) that only
# have a bundle in hand and don't want to hardcode the mapping themselves.
_SOURCE_KIND_TO_ACQUISITION_SOURCE: dict[str, str] = {
    "mercadopublico_portal": ACQUISITION_SOURCE_CHILECOMPRA_LEGACY_PARTIAL_HTTP,
    ACQUISITION_SOURCE_OPERATOR_COMPLETE_BUNDLE: ACQUISITION_SOURCE_OPERATOR_COMPLETE_BUNDLE,
}


def acquisition_source_for_bundle(bundle: TenderAttachmentBundle) -> str:
    """Best-effort mapping from a bundle's existing ``source_kind`` string."""
    return _SOURCE_KIND_TO_ACQUISITION_SOURCE.get(bundle.source_kind, bundle.source_kind)


@dataclass(frozen=True)
class AttachmentProvenance:
    """Per-attachment audit-trail row: identity, not extraction outcome detail."""

    original_filename: str
    safe_filename: str
    byte_count: int
    sha256: str
    content_type: str
    detected_format: str
    outcome: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_filename": self.original_filename,
            "safe_filename": self.safe_filename,
            "byte_count": self.byte_count,
            "sha256": self.sha256,
            "content_type": self.content_type,
            "detected_format": self.detected_format,
            "outcome": self.outcome,
        }


@dataclass(frozen=True)
class AcquisitionProvenance:
    """One tender's full acquisition audit trail, independent of its source."""

    tender_code: str
    acquisition_source: str
    acquired_at: str
    completeness_state: str
    completeness_reason: str
    operator_supplied: bool
    source_semantic_digest: str
    attachments: tuple[AttachmentProvenance, ...] = ()

    def __post_init__(self) -> None:
        if self.acquisition_source not in ACQUISITION_SOURCES:
            raise ValueError(f"unknown acquisition source: {self.acquisition_source!r}")
        if self.completeness_state not in COMPLETENESS_STATES:
            raise ValueError(f"invalid completeness state: {self.completeness_state!r}")
        if self.operator_supplied != is_operator_supplied(self.acquisition_source):
            raise ValueError(
                "operator_supplied does not match acquisition_source: "
                f"{self.operator_supplied!r} for {self.acquisition_source!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "tender_code": self.tender_code,
            "acquisition_source": self.acquisition_source,
            "acquired_at": self.acquired_at,
            "completeness_state": self.completeness_state,
            "completeness_reason": self.completeness_reason,
            "operator_supplied": self.operator_supplied,
            "source_semantic_digest": self.source_semantic_digest,
            "attachments": [a.to_dict() for a in self.attachments],
        }


def resolve_completeness_state(
    *,
    acquisition_source: str,
    bundle: TenderAttachmentBundle,
    operator_declared_complete: bool = False,
) -> CompletenessState:
    """Project one bundle's acquisition-level completeness into the 3-state vocabulary.

    ``bundle.bundle_complete`` (download + extraction both complete, no
    incomplete_reason_codes at all -- see models.TenderAttachmentBundle) is
    unchanged and remains the single source of truth for "was everything we
    know about actually read". This function only interprets a not-complete
    bundle:

    * For ``operator_complete_bundle`` without an explicit
      ``operator_declared_complete``, the code genuinely does not know whether
      the ZIP is the full Mercado Público inventory -- "unknown" is the
      honest state, not "incomplete" (which would assert we know something is
      missing) and not "complete" (which would assert something never
      declared).
    * Every other case -- including a legacy HTTP bundle whose ficha
      advertised a gated listing surface it cannot enumerate
      (REASON_GATED_LISTING_UNREACHABLE), and an operator bundle that *was*
      declared complete but still hit a real extraction failure -- resolves
      to "incomplete", matching the existing 2-state
      TenderTermsCoverage.is_complete semantics this must stay consistent
      with (gated_listing_unreachable already yields incomplete there today).
    """
    if bundle.bundle_complete:
        return COMPLETENESS_STATE_COMPLETE
    if acquisition_source == ACQUISITION_SOURCE_OPERATOR_COMPLETE_BUNDLE and not operator_declared_complete:
        return COMPLETENESS_STATE_UNKNOWN
    return COMPLETENESS_STATE_INCOMPLETE


def _completeness_reason(
    *,
    acquisition_source: str,
    bundle: TenderAttachmentBundle,
    operator_declared_complete: bool,
    state: CompletenessState,
) -> str:
    if state == COMPLETENESS_STATE_COMPLETE:
        if acquisition_source == ACQUISITION_SOURCE_OPERATOR_COMPLETE_BUNDLE:
            return "operator_declared_complete_and_extraction_complete"
        return "download_and_extraction_complete"
    if bundle.incomplete_reason_codes:
        return ", ".join(bundle.incomplete_reason_codes)
    if state == COMPLETENESS_STATE_UNKNOWN:
        return "operator_did_not_declare_complete"
    # Complete inventory but extraction itself did not fully succeed.
    return "extraction_not_complete"


def build_acquisition_provenance(
    bundle: TenderAttachmentBundle,
    *,
    acquisition_source: str,
    acquired_at: str,
    operator_declared_complete: bool = False,
) -> AcquisitionProvenance:
    """Build the audit-trail record for one already-built TenderAttachmentBundle."""
    if acquisition_source not in ACQUISITION_SOURCES:
        raise ValueError(f"unknown acquisition source: {acquisition_source!r}")

    state = resolve_completeness_state(
        acquisition_source=acquisition_source,
        bundle=bundle,
        operator_declared_complete=operator_declared_complete,
    )
    reason = _completeness_reason(
        acquisition_source=acquisition_source,
        bundle=bundle,
        operator_declared_complete=operator_declared_complete,
        state=state,
    )
    attachments = tuple(
        AttachmentProvenance(
            original_filename=record.portal_filename,
            safe_filename=record.safe_filename,
            byte_count=record.byte_count,
            sha256=record.sha256,
            content_type=record.content_type,
            detected_format=record.detected_format,
            outcome=record.outcome,
        )
        for record in bundle.attachments
    )
    return AcquisitionProvenance(
        tender_code=bundle.tender_id,
        acquisition_source=acquisition_source,
        acquired_at=acquired_at,
        completeness_state=state,
        completeness_reason=reason,
        operator_supplied=is_operator_supplied(acquisition_source),
        source_semantic_digest=bundle.semantic_digest,
        attachments=attachments,
    )


class FutureChileCompraDocumentApiSource(Protocol):
    """Typed seam for a possible future official ChileCompra document API.

    No implementation exists, and none should be added speculatively here:
    no endpoint guessing, no undocumented-API calls, no scraping of
    ``ViewAttachment.aspx``, and absolutely no CAPTCHA automation in any
    form. This Protocol exists purely so that *if* ChileCompra ever publishes
    (or grants access to) an official document API, a future adapter has an
    exact, pre-agreed contract to implement -- and, because it is structurally
    identical to ``acquire.AttachmentSource``, that adapter plugs straight
    into ``build_tender_bundle()`` with no change to extraction, T1, or
    publication.

    Mirrors ``acquire.AttachmentSource`` exactly: enumerate everything first,
    then stream bodies one at a time.
    """

    source_kind: str  # must equal ACQUISITION_SOURCE_FUTURE_CHILECOMPRA_DOCUMENT_API

    def inventory(self) -> SourceInventory: ...

    def iter_payloads(self, inventory: SourceInventory) -> Iterator[AcquiredAttachment]: ...


__all__ = [
    "AcquisitionProvenance",
    "AcquisitionSource",
    "AttachmentProvenance",
    "CompletenessState",
    "FutureChileCompraDocumentApiSource",
    "acquisition_source_for_bundle",
    "build_acquisition_provenance",
    "is_operator_supplied",
    "resolve_completeness_state",
]
