"""Acquire ANEXO evidence for the *current run's* W1 actionable tenders and
publish ANEXO-T1 structured terms from it.

This is the missing seam described in
``publish_tender_terms_from_cached_bundles.py``'s header: the production
ChileCompra refresh (``operator_cli/chilecompra_auto_refresh.py``) computes a
``current_opportunity_queue`` (via the W1 institution-prospect publication)
but never builds a :class:`TenderAttachmentBundle` for any of it. This module
is the smallest safe bridge:

    healthy, same-run current_opportunity_queue
        -> dedupe by tender_code
        -> PortalAttachmentSource + build_tender_bundle per tender_code
        -> publish_tender_terms(...)

Hard invariants:
  * W1's current_opportunity_queue (as already computed/published by *this*
    run) is the sole authority for which tender_codes are eligible. This
    module never re-queries the ChileCompra summary/detail JSON API and never
    invents eligibility of its own.
  * No DB/Gmail/outreach path is touched anywhere in this module.
  * A single tender's acquisition failure never prevents publication of the
    other tenders, and never silently produces "no terms found" -- it always
    surfaces as TenderTermsCoverage incompleteness (see
    chilecompra_anexo_evidence.planner.build_tender_bundle's own contract).
  * publish_tender_terms(...) is the sole publication boundary and is called
    exactly once per invocation, with all currently-eligible bundles, so its
    own atomic all-or-nothing promotion semantics are preserved.
  * Every attachment body is re-downloaded on every run (see acquire.py's
    PortalAttachmentSource) -- there is no cross-run cache-skip anywhere in
    this bridge. The only local cache this module writes is a post-acquisition
    snapshot of each *already-downloaded* TenderAttachmentBundle, written
    atomically, purely so a later bounded local semantic fallback stage can
    replay this run's real evidence without re-acquiring it. It never
    substitutes for or skips a download.
  * If the current run's eligible tender_codes exceed ``max_tenders``, this
    module fails closed (acquires and publishes nothing) rather than silently
    truncating the set -- see ``select_current_tender_codes`` / gate_status
    ``"blocked_max_tenders_exceeded"``.
"""

from __future__ import annotations

import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from origenlab_email_pipeline.chilecompra_anexo_evidence import (
    EvidenceBuildConfig,
    PortalAttachmentSource,
    TenderAttachmentBundle,
    build_tender_bundle,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.acquire import AttachmentSource
from origenlab_email_pipeline.chilecompra_anexo_evidence.fingerprint import (
    bundle_semantic_digest,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.output_safety import (
    write_atomically,
)

from . import production_publish
from .production_publish import (
    TenderTermsPublicationResult,
    publish_tender_terms,
)

DEFAULT_MAX_TENDERS_PER_RUN = 25

GATE_BLOCKED_REDUCED_MODE_W1 = "blocked_reduced_mode_w1"
GATE_NO_CURRENT_OPPORTUNITIES = "no_current_opportunities"
GATE_BLOCKED_MAX_TENDERS_EXCEEDED = "blocked_max_tenders_exceeded"
GATE_ACQUIRED_AND_PUBLISHED = "acquired_and_published"

# Subdirectory of ``cache_dir`` under which each successfully built bundle's
# snapshot is atomically persisted (see ``_persist_bundle_snapshot``).
BUNDLE_SNAPSHOT_DIRNAME = "current_bundle_snapshot"

BuildSourceFn = Callable[[str, str], AttachmentSource]


def _default_build_source(tender_code: str, detail_url: str) -> AttachmentSource:
    return PortalAttachmentSource(detail_url=detail_url)


def _safe_tender_code(tender_code: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in tender_code)


def _persist_bundle_snapshot(
    bundle: TenderAttachmentBundle,
    *,
    cache_dir: Path,
    tender_code: str,
    require_git_ignored: bool,
) -> bool:
    """Atomically snapshot an already-acquired bundle for later local replay.

    This is an evidence/replay cache, not a database and not a second
    acquisition owner: it only ever stores a bundle that this run already
    legitimately built (real bytes already in hand). A write failure here
    never invalidates the acquisition that already succeeded and never
    blocks T1 publication -- it only means this tender_code's bundle will
    not be replayable from the snapshot cache. Returns whether the snapshot
    was written.
    """
    snapshot_dir = cache_dir / BUNDLE_SNAPSHOT_DIRNAME / _safe_tender_code(tender_code)

    def _writer(staged: Path) -> dict[str, str]:
        bundle_path = staged / "bundle.pkl"
        bundle_path.write_bytes(pickle.dumps(bundle))
        return {"bundle.pkl": str(bundle_path)}

    try:
        write_atomically(
            snapshot_dir,
            repo_email_pipeline_root=production_publish._email_pipeline_root(),
            writer=_writer,
            require_git_ignored=require_git_ignored,
        )
    except Exception:  # noqa: BLE001 - snapshot cache is best-effort, never fatal
        return False
    return True


@dataclass(frozen=True)
class TenderAcquisitionOutcome:
    """Per-tender acquisition bookkeeping, independent of the publish step."""

    tender_code: str
    status: str  # "acquired" | "skipped_no_detail_url" | "acquisition_exception"
    attachments_discovered: int = 0
    attachments_downloaded: int = 0
    bundle_complete: bool = False
    incomplete_reason_codes: tuple[str, ...] = ()
    # Whether this tender's already-built bundle was successfully snapshotted
    # into the local evidence/replay cache (see _persist_bundle_snapshot).
    # False for any tender that never reached a successful bundle build, and
    # also False (non-fatally) if the snapshot write itself failed.
    bundle_snapshot_persisted: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tender_code": self.tender_code,
            "status": self.status,
            "attachments_discovered": self.attachments_discovered,
            "attachments_downloaded": self.attachments_downloaded,
            "bundle_complete": self.bundle_complete,
            "incomplete_reason_codes": list(self.incomplete_reason_codes),
            "bundle_snapshot_persisted": self.bundle_snapshot_persisted,
            "error": self.error,
        }


@dataclass(frozen=True)
class AcquireAndPublishResult:
    gate_status: str
    tender_codes_considered: tuple[str, ...]
    acquisitions: tuple[TenderAcquisitionOutcome, ...]
    publish: TenderTermsPublicationResult | None
    # Visibility into the max_tenders bound: eligible_count is the number of
    # distinct tender_codes this run's current_opportunity_queue selected
    # (before any bound is applied); selected_count is how many were actually
    # acquired. When eligible_count > max_tenders this module fails closed
    # (selected_count stays 0, truncated is True) rather than silently
    # dropping the excess -- see GATE_BLOCKED_MAX_TENDERS_EXCEEDED.
    eligible_count: int = 0
    selected_count: int = 0
    truncated: bool = False
    truncation_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_status": self.gate_status,
            "tender_codes_considered": list(self.tender_codes_considered),
            "acquisitions": [a.to_dict() for a in self.acquisitions],
            "publish": self.publish.to_dict() if self.publish else None,
            "eligible_count": self.eligible_count,
            "selected_count": self.selected_count,
            "truncated": self.truncated,
            "truncation_reason": self.truncation_reason,
        }


def select_current_tender_codes(
    current_opportunity_queue: list[dict[str, Any]],
) -> list[str]:
    """Dedupe tender_codes from the queue, preserving first-seen order.

    ``current_opportunity_queue`` may legitimately contain more than one row
    per tender_code (one per matched equipment category); this collapses
    that to exactly one acquisition target per tender. This performs no
    bounding of any kind -- the full eligible set is returned, and any
    max_tenders enforcement happens (visibly, fail-closed) in
    ``acquire_and_publish_current_tender_terms``.
    """
    seen: dict[str, None] = {}
    for row in current_opportunity_queue:
        code = str(row.get("tender_code") or "").strip()
        if not code or code in seen:
            continue
        seen[code] = None
    return list(seen.keys())


def _failed_bundle(tender_code: str, reason_code: str) -> TenderAttachmentBundle:
    digest = bundle_semantic_digest(
        tender_id=tender_code,
        attachments=(),
        chunks=(),
        incomplete_reason_codes=[reason_code],
    )
    return TenderAttachmentBundle(
        tender_id=tender_code,
        source_kind="mercadopublico_portal",
        listing_page_count=0,
        attachments_discovered=0,
        attachments_downloaded=0,
        attachments=(),
        chunks=(),
        bytes_downloaded=0,
        incomplete_reason_codes=(reason_code,),
        semantic_digest=digest,
        outcome_counts={},
    )


def acquire_and_publish_current_tender_terms(
    *,
    current_opportunity_queue: list[dict[str, Any]],
    tender_code_to_detail_url: dict[str, str],
    out_dir: Path,
    cache_dir: Path,
    as_of_utc: str,
    reduced_mode: bool = False,
    max_tenders: int = DEFAULT_MAX_TENDERS_PER_RUN,
    build_source_fn: BuildSourceFn | None = None,
    config: EvidenceBuildConfig | None = None,
    require_git_ignored: bool = True,
) -> AcquireAndPublishResult:
    """Acquire ANEXO evidence for this run's current W1 tenders and publish T1.

    Fails closed at the gate: a degraded/reduced-mode W1 read (``reduced_mode
    =True``) or an empty queue means zero acquisition and zero publication --
    never a partial/best-guess acquisition.
    """
    if reduced_mode:
        return AcquireAndPublishResult(
            gate_status=GATE_BLOCKED_REDUCED_MODE_W1,
            tender_codes_considered=(),
            acquisitions=(),
            publish=None,
            eligible_count=0,
            selected_count=0,
            truncated=False,
        )

    tender_codes = select_current_tender_codes(current_opportunity_queue)
    eligible_count = len(tender_codes)

    if not tender_codes:
        return AcquireAndPublishResult(
            gate_status=GATE_NO_CURRENT_OPPORTUNITIES,
            tender_codes_considered=(),
            acquisitions=(),
            publish=None,
            eligible_count=0,
            selected_count=0,
            truncated=False,
        )

    if eligible_count > max_tenders:
        # Fail closed: exceeding the bound must never silently drop tenders.
        # Zero acquisition, zero publication -- visible via gate_status and
        # the eligible/selected/truncated fields, not a quiet [:max_tenders]
        # slice, consistent with this codebase's other budget enforcement
        # (e.g. AttachmentBudgetError in chilecompra_anexo_evidence.acquire).
        return AcquireAndPublishResult(
            gate_status=GATE_BLOCKED_MAX_TENDERS_EXCEEDED,
            tender_codes_considered=(),
            acquisitions=(),
            publish=None,
            eligible_count=eligible_count,
            selected_count=0,
            truncated=True,
            truncation_reason=(
                f"eligible tender_codes ({eligible_count}) exceed "
                f"max_tenders ({max_tenders}); refusing to silently drop any "
                "-- raise max_tenders or narrow the current_opportunity_queue "
                "upstream"
            ),
        )

    builder = build_source_fn

    bundles: list[TenderAttachmentBundle] = []
    outcomes: list[TenderAcquisitionOutcome] = []

    for tender_code in tender_codes:
        detail_url = tender_code_to_detail_url.get(tender_code)
        if not detail_url:
            outcomes.append(
                TenderAcquisitionOutcome(
                    tender_code=tender_code,
                    status="skipped_no_detail_url",
                    error="no detail_url resolvable for tender_code from this run's queue",
                )
            )
            bundles.append(_failed_bundle(tender_code, "no_detail_url"))
            continue

        try:
            if builder is not None:
                source = builder(tender_code, detail_url)
            else:
                source = _default_build_source(tender_code, detail_url)
            bundle = build_tender_bundle(tender_code, source, config=config)
        except Exception as exc:  # noqa: BLE001 - one tender must never sink the run
            outcomes.append(
                TenderAcquisitionOutcome(
                    tender_code=tender_code,
                    status="acquisition_exception",
                    error=f"{type(exc).__name__}: {exc}"[:300],
                )
            )
            bundles.append(_failed_bundle(tender_code, "acquisition_exception"))
            continue

        bundles.append(bundle)
        snapshot_persisted = _persist_bundle_snapshot(
            bundle,
            cache_dir=cache_dir,
            tender_code=tender_code,
            require_git_ignored=require_git_ignored,
        )

        outcomes.append(
            TenderAcquisitionOutcome(
                tender_code=tender_code,
                status="acquired",
                attachments_discovered=bundle.attachments_discovered,
                attachments_downloaded=bundle.attachments_downloaded,
                bundle_complete=bundle.bundle_complete,
                incomplete_reason_codes=bundle.incomplete_reason_codes,
                bundle_snapshot_persisted=snapshot_persisted,
            )
        )

    publish_result = publish_tender_terms(
        bundles=bundles,
        as_of_utc=as_of_utc,
        out_dir=out_dir,
        require_git_ignored=require_git_ignored,
    )

    return AcquireAndPublishResult(
        gate_status=GATE_ACQUIRED_AND_PUBLISHED,
        tender_codes_considered=tuple(tender_codes),
        acquisitions=tuple(outcomes),
        publish=publish_result,
        eligible_count=eligible_count,
        selected_count=len(tender_codes),
        truncated=False,
    )


__all__ = [
    "AcquireAndPublishResult",
    "BUNDLE_SNAPSHOT_DIRNAME",
    "DEFAULT_MAX_TENDERS_PER_RUN",
    "GATE_ACQUIRED_AND_PUBLISHED",
    "GATE_BLOCKED_MAX_TENDERS_EXCEEDED",
    "GATE_BLOCKED_REDUCED_MODE_W1",
    "GATE_NO_CURRENT_OPPORTUNITIES",
    "TenderAcquisitionOutcome",
    "acquire_and_publish_current_tender_terms",
    "select_current_tender_codes",
]
