"""Validated, fail-closed production publication of the institution-prospect read model.

Wraps ``build_institution_prospect_plan`` (the sole source of PR5C/PR5D/PR5E
semantics — this module performs no acquisition and no classification) with:

1. a staged build (writes to a sibling staging directory first, never touching
   the canonical destination directly);
2. post-write validation through the same W1 ``read_model.load_published_read_model``
   loader the API uses, so a bundle that would fail the API's own contract
   checks can never be declared a success here either;
3. an atomic promote-from-staging step (reusing ``output_safety.write_atomically``,
   the same primitive the planner itself uses) so a validated bundle replaces
   the prior one only as a single atomic swap.

If validation fails at any point, the canonical directory is left completely
untouched — the previous valid bundle (if any) remains authoritative.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.commercial_identity.constants import (
    RUN_CONTEXT_PRODUCTION_APPLY,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.output_safety import (
    write_atomically,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.planner import (
    build_institution_prospect_plan,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.read_model import (
    PublishedReadModelError,
    load_published_read_model,
)

# Matches apps/api Settings._DEFAULT_INSTITUTION_PROSPECT_DIR
# (<active/current> / INSTITUTION_PROSPECTS_DIRNAME) exactly — this is the
# single canonical basename both sides must agree on.
INSTITUTION_PROSPECTS_DIRNAME = "institution_prospects"

DEFAULT_FRESHNESS_THRESHOLD_HOURS = 48
DEFAULT_MAX_FEED_AGE_HOURS = 36

_STAGING_SUFFIX = ".publish_staging"


def _email_pipeline_root() -> Path:
    return Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class InstitutionProspectPublicationResult:
    result: str  # "applied" | "failed"
    output_dir_basename: str
    contract_version: str | None = None
    as_of_utc: str | None = None
    source_digest: str | None = None
    institution_count: int | None = None
    operator_queue_sizes: dict[str, int] | None = None
    current_opportunity_count: int | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "result": self.result,
            "output_dir_basename": self.output_dir_basename,
            "contract_version": self.contract_version,
            "as_of_utc": self.as_of_utc,
            "source_digest": self.source_digest,
            "institution_count": self.institution_count,
            "operator_queue_sizes": self.operator_queue_sizes,
            "current_opportunity_count": self.current_opportunity_count,
            "error": self.error,
        }


def _staging_dir_for(out_dir: Path) -> Path:
    return out_dir.parent / f".{out_dir.name}{_STAGING_SUFFIX}"


def _promote_staging_to_canonical(staging_dir: Path, out_dir: Path) -> None:
    def _writer(tmp: Path) -> dict[str, str]:
        written: dict[str, str] = {}
        for child in staging_dir.iterdir():
            dest = tmp / child.name
            if child.is_dir():
                shutil.copytree(child, dest)
            else:
                shutil.copy2(child, dest)
            written[child.name] = str(dest)
        return written

    write_atomically(
        out_dir,
        repo_email_pipeline_root=_email_pipeline_root(),
        writer=_writer,
    )


def publish_current_institution_prospects(
    *,
    sqlite_path: Path,
    detail_cache_dir: Path,
    equipment_manifest_path: Path,
    as_of_utc: str,
    out_dir: Path,
    run_context: str = RUN_CONTEXT_PRODUCTION_APPLY,
    freshness_threshold_hours: int = DEFAULT_FRESHNESS_THRESHOLD_HOURS,
    max_feed_age_hours: int = DEFAULT_MAX_FEED_AGE_HOURS,
    allow_stale_feed: bool = False,
) -> InstitutionProspectPublicationResult:
    """Build, validate, and atomically publish the institution-prospect read model.

    Deliberately does not accept ``refresh_state_path``: the freshness check in
    ``build_institution_prospect_plan`` prefers ``refresh_state.last_successful_refresh_at``
    over ``manifest.generated_at_utc`` when both are given, so passing a
    refresh-state snapshot that has not yet been updated for *this* transaction
    would anchor freshness to a stale prior run instead of the manifest this
    call is actually publishing from. ``equipment_manifest_path`` (the manifest
    this exact refresh just wrote) is the freshness authority here.

    Performs zero ChileCompra/network calls, zero DB writes, zero ANEXO
    acquisition — annex evidence stays disabled. On any failure (planner error,
    or the newly-built bundle failing the W1 loader's own validation), the
    canonical ``out_dir`` is left byte-for-byte untouched.
    """
    staging_dir = _staging_dir_for(out_dir)
    basename = out_dir.name

    try:
        build_institution_prospect_plan(
            sqlite_path=sqlite_path,
            as_of_utc=as_of_utc,
            out_dir=staging_dir,
            run_context=run_context,
            freshness_threshold_hours=freshness_threshold_hours,
            detail_cache_dir=detail_cache_dir,
            equipment_manifest_path=equipment_manifest_path,
            refresh_state_path=None,
            allow_stale_feed=allow_stale_feed,
            max_feed_age_hours=max_feed_age_hours,
            enable_annex_opportunity_evidence=False,
        )
    except Exception as exc:  # noqa: BLE001 - fail closed, never propagate raw internals as success
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        return InstitutionProspectPublicationResult(
            result="failed",
            output_dir_basename=basename,
            error=f"build_failed: {exc}",
        )

    try:
        model = load_published_read_model(staging_dir)
    except PublishedReadModelError as exc:
        shutil.rmtree(staging_dir, ignore_errors=True)
        return InstitutionProspectPublicationResult(
            result="failed",
            output_dir_basename=basename,
            error=f"validation_failed: {exc}",
        )

    # A successful load_published_read_model() already proves everything item 8
    # requires: the packet parsed, the contract version is supported, all six
    # OPERATOR_QUEUE_NAMES CSVs were present and parseable (it raises
    # PublishedReadModelMissingError otherwise), and contact/outreach
    # authorization are unconditionally normalized to False in the returned
    # model. There is nothing left to re-check here without duplicating the
    # loader's own guarantees as dead code.
    queue_sizes = dict(model.get("operator_queue_sizes") or {})
    current_opportunity_count = int(queue_sizes.get("current_opportunity_queue") or 0)
    profiles = model.get("profiles") or []

    try:
        _promote_staging_to_canonical(staging_dir, out_dir)
    except Exception as exc:  # noqa: BLE001 - fail closed; prior canonical bundle preserved by write_atomically
        return InstitutionProspectPublicationResult(
            result="failed",
            output_dir_basename=basename,
            error=f"promote_failed: {exc}",
        )
    finally:
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)

    return InstitutionProspectPublicationResult(
        result="applied",
        output_dir_basename=basename,
        contract_version=str(model.get("contract_version") or ""),
        as_of_utc=str(model.get("as_of_utc") or ""),
        source_digest=str(model.get("source_digest") or ""),
        institution_count=len(profiles),
        operator_queue_sizes=queue_sizes,
        current_opportunity_count=current_opportunity_count,
    )


__all__ = [
    "DEFAULT_FRESHNESS_THRESHOLD_HOURS",
    "DEFAULT_MAX_FEED_AGE_HOURS",
    "INSTITUTION_PROSPECTS_DIRNAME",
    "InstitutionProspectPublicationResult",
    "publish_current_institution_prospects",
]
