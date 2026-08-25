from __future__ import annotations

from origenlab_api.errors import (
    sanitize_text,
    sanitize_value,
)


def test_write_postgres_dsn_is_redacted_from_text() -> None:
    secret = "postgresql://crm_writer:super-secret@db.internal:5432/origenlab"

    rendered = sanitize_text(f"ORIGENLAB_POSTGRES_WRITE_URL={secret}")

    assert secret not in rendered
    assert "super-secret" not in rendered
    assert "ORIGENLAB_POSTGRES_WRITE_URL" in rendered
    assert "<redacted" in rendered


def test_postgres_write_url_detail_key_is_redacted() -> None:
    secret = "postgresql://writer:another-secret@db.internal:5432/origenlab"

    rendered = sanitize_value(
        {
            "postgres_write_url": secret,
            "safe": "visible",
        }
    )

    assert rendered["safe"] == "visible"
    assert rendered["redacted"] == "<redacted>"

    text = repr(rendered)

    assert secret not in text
    assert "another-secret" not in text
    assert "postgres_write_url" not in text
