"""Trusted operator identity for durable commercial-operation writes."""

from __future__ import annotations

from fastapi import HTTPException, Request

from origenlab_api.settings import Settings

OPERATOR_EMAIL_HEADER = "X-OriginLab-Operator-Email"


def normalize_operator_email(value: str | None) -> str | None:
    candidate = (value or "").strip().lower()

    if not candidate:
        return None

    if len(candidate) > 320:
        return None

    if candidate.count("@") != 1:
        return None

    local, domain = candidate.split("@", 1)

    if not local or not domain or "." not in domain:
        return None

    return candidate


def require_commercial_operator(
    request: Request,
    settings: Settings,
) -> str:
    """Return trusted operator email for an admitted commercial write."""

    if not settings.commercial_operations_writes_enabled:
        raise HTTPException(
            status_code=503,
            detail="Commercial operations writes are disabled",
        )

    operator = normalize_operator_email(request.headers.get(OPERATOR_EMAIL_HEADER))

    if operator is None:
        raise HTTPException(
            status_code=401,
            detail="Authenticated commercial operator identity required",
        )

    return operator
