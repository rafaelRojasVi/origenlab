"""Reconciliation proofs for institution prospect plans."""

from __future__ import annotations

from typing import Any


class InstitutionReconciliationError(ValueError):
    """Raised when a tender or signal disappears without an explicit reason."""


def reconcile_institution_prospects(
    *,
    source_tender_ids: set[str],
    assigned_tender_ids: set[str],
    unresolved_tender_ids: set[str],
    excluded: dict[str, str],
) -> dict[str, Any]:
    """
    Prove:
      every source tender
        = assigned to an institution profile
        + explicitly unresolved
        + explicitly excluded with a stable reason
    """
    excluded_ids = set(excluded)
    covered = assigned_tender_ids | unresolved_tender_ids | excluded_ids
    missing = sorted(source_tender_ids - covered)
    extra = sorted(covered - source_tender_ids)
    overlap_assigned_unresolved = sorted(
        assigned_tender_ids & unresolved_tender_ids
    )
    overlap_assigned_excluded = sorted(assigned_tender_ids & excluded_ids)
    if missing or extra or overlap_assigned_unresolved or overlap_assigned_excluded:
        raise InstitutionReconciliationError(
            "institution prospect reconciliation failed: "
            f"missing={len(missing)} extra={len(extra)} "
            f"assigned∩unresolved={len(overlap_assigned_unresolved)} "
            f"assigned∩excluded={len(overlap_assigned_excluded)}"
        )
    for tid, reason in excluded.items():
        if not reason:
            raise InstitutionReconciliationError(
                f"excluded tender {tid} missing stable reason"
            )
    return {
        "ok": True,
        "equation": (
            "source_tenders = assigned + unresolved + excluded_with_reason"
        ),
        "source_tender_count": len(source_tender_ids),
        "assigned_tender_count": len(assigned_tender_ids),
        "unresolved_tender_count": len(unresolved_tender_ids),
        "excluded_tender_count": len(excluded_ids),
        "excluded_reason_counts": _count_values(excluded),
    }


def _count_values(mapping: dict[str, str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for reason in mapping.values():
        out[reason] = out.get(reason, 0) + 1
    return dict(sorted(out.items()))


__all__ = ["InstitutionReconciliationError", "reconcile_institution_prospects"]
