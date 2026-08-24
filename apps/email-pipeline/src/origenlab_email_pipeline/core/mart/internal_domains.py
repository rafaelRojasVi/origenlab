"""Canonical internal-domain resolution for business-mart workflows."""

from __future__ import annotations

from collections.abc import Iterable

from origenlab_email_pipeline.warm_case_sender_rules import (
    INTERNAL_OPERATOR_DOMAINS,
)


def resolve_mart_internal_domains(
    explicit_domains: Iterable[str] | None = None,
) -> set[str]:
    """Return canonical operator domains plus explicit approved additions.

    Sender frequency is not an identity signal. High-volume customers,
    suppliers, marketplaces, and relay domains must never become internal
    merely because they dominate the email archive.
    """
    explicit = {
        domain.strip().lower() for domain in (explicit_domains or ()) if domain.strip()
    }
    return set(INTERNAL_OPERATOR_DOMAINS) | explicit
