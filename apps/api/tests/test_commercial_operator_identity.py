"""ARCH-3B2 trusted operator identity tests."""

from __future__ import annotations

from fastapi import HTTPException
from starlette.requests import Request

from origenlab_api.commercial_operator_identity import (
    OPERATOR_EMAIL_HEADER,
    normalize_operator_email,
    require_commercial_operator,
)
from origenlab_api.settings import Settings


def _request(
    operator_email: str | None = None,
) -> Request:
    headers: list[tuple[bytes, bytes]] = []

    if operator_email is not None:
        headers.append(
            (
                OPERATOR_EMAIL_HEADER.lower().encode(),
                operator_email.encode(),
            )
        )

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/operations/test",
            "headers": headers,
        }
    )


def _settings(
    *,
    enabled: bool = True,
) -> Settings:
    return Settings(
        api_backend="postgres",
        postgres_url="postgresql://readonly/example",
        postgres_write_url="postgresql://writer/example",
        commercial_operations_writes_enabled=enabled,
    )


def test_normalize_operator_email() -> None:
    assert normalize_operator_email(" Tatiana@OrigenLab.CL ") == "tatiana@origenlab.cl"


def test_invalid_operator_email_is_rejected() -> None:
    assert normalize_operator_email("") is None
    assert normalize_operator_email("tatiana") is None
    assert normalize_operator_email("@origenlab.cl") is None
    assert normalize_operator_email("tatiana@localhost") is None


def test_write_requires_feature_enabled() -> None:
    try:
        require_commercial_operator(
            _request("tatiana@origenlab.cl"),
            _settings(enabled=False),
        )
    except HTTPException as exc:
        assert exc.status_code == 503
    else:
        raise AssertionError("disabled writes must fail closed")


def test_write_requires_operator_identity() -> None:
    try:
        require_commercial_operator(
            _request(),
            _settings(enabled=True),
        )
    except HTTPException as exc:
        assert exc.status_code == 401
    else:
        raise AssertionError("missing operator identity must fail closed")


def test_write_returns_normalized_operator() -> None:
    result = require_commercial_operator(
        _request(" TATIANA@ORIGENLAB.CL "),
        _settings(enabled=True),
    )

    assert result == "tatiana@origenlab.cl"
