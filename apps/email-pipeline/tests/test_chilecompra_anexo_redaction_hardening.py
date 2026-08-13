from __future__ import annotations

import pytest

from origenlab_email_pipeline.chilecompra_anexo_evidence.redaction import (
    ArtifactRedactionError,
    assert_no_portal_tokens,
    redact_portal_tokens,
)


def test_portal_token_redaction_is_idempotent_and_exact() -> None:
    raw = "https://portal.invalid/a?ticket=opaque&enc=second"

    redacted = redact_portal_tokens(raw)

    assert redacted == (
        "https://portal.invalid/a?ticket=<redacted>&enc=<redacted>"
    )
    assert redact_portal_tokens(redacted) == redacted
    assert_no_portal_tokens(redacted, where="test")


@pytest.mark.parametrize(
    "unsafe",
    [
        "ticket=<redacted>still-secret",
        "ticket=<redacted><redacted>",
        "ticket=<redacted><secret",
        "ticket=<redacted>\\SECRET",
    ],
)
def test_exact_redaction_marker_rejects_secret_suffix(
    unsafe: str,
) -> None:
    with pytest.raises(
        ArtifactRedactionError,
        match="portal token",
    ):
        assert_no_portal_tokens(unsafe, where="test")


@pytest.mark.parametrize(
    "safe",
    [
        "ticket=<redacted>",
        "ticket=<redacted>&enc=<redacted>",
        'ticket=<redacted> next',
        'ticket=<redacted>"',
    ],
)
def test_exact_redaction_marker_accepts_safe_delimiters(
    safe: str,
) -> None:
    assert_no_portal_tokens(safe, where="test")
