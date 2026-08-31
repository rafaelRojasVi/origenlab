"""CRM-Q1C: the provisioning-attempt lease must be a bounded, provable
multiple of the single Drive request/token-refresh timeout -- never an
unexplained arbitrary number. This test asserts that relationship directly
so the two constants can never silently drift apart; it needs no Postgres
and always runs.
"""

from __future__ import annotations

from origenlab_api.drive.factory import (
    _REQUEST_TIMEOUT_SECONDS,
    _TOKEN_REFRESH_TIMEOUT_SECONDS,
)
from origenlab_api.repositories.postgres.customer_quotes import (
    PROVISION_ATTEMPT_LEASE_SECONDS,
)

# Worst-case Drive-facing calls in one provisioning attempt: verify_destination,
# find_folder, create_folder, find_sheet, copy_template_sheet (5 HTTP calls)
# plus at most one credential refresh (a fresh token stays valid for ~1 hour,
# far longer than one attempt, so at most one refresh per attempt).
_MAX_DRIVE_CALLS_PER_ATTEMPT = 5
_MAX_CREDENTIAL_REFRESHES_PER_ATTEMPT = 1


def test_token_refresh_timeout_matches_the_drive_request_timeout() -> None:
    assert _TOKEN_REFRESH_TIMEOUT_SECONDS == _REQUEST_TIMEOUT_SECONDS


def test_provision_attempt_lease_exceeds_worst_case_attempt_duration() -> None:
    worst_case_seconds = (
        _MAX_DRIVE_CALLS_PER_ATTEMPT * _REQUEST_TIMEOUT_SECONDS
        + _MAX_CREDENTIAL_REFRESHES_PER_ATTEMPT * _TOKEN_REFRESH_TIMEOUT_SECONDS
    )

    assert worst_case_seconds == 120.0
    # A stale/expired attempt must remain safely reclaimable well before an
    # operator would plausibly retry by hand, while still giving a real
    # safety margin over the worst case above -- not an unexplained number.
    assert PROVISION_ATTEMPT_LEASE_SECONDS > worst_case_seconds
    assert PROVISION_ATTEMPT_LEASE_SECONDS >= 2 * worst_case_seconds
