from __future__ import annotations

from origenlab_email_pipeline.historical_quote_register.revision_linking import (
    PriorSend,
    link_revision,
)


def test_first_send_under_a_key_has_no_prior():
    result = link_revision(
        prior_send=None, current_attachment_sha256="abc123",
        current_historical_quote_key="hqk-1", evidence_text="Cotización COT-2026-014",
    )
    assert result.relationship == "first_revision"
    assert result.supersedes_historical_quote_key is None


def test_identical_hash_resend_is_same_revision_not_new():
    prior = PriorSend(historical_quote_key="hqk-1", attachment_sha256="abc123")
    result = link_revision(
        prior_send=prior, current_attachment_sha256="abc123",
        current_historical_quote_key="hqk-1", evidence_text="Cotización COT-2026-014 (recordatorio)",
    )
    assert result.relationship == "same_revision_resend"
    assert result.supersedes_historical_quote_key is None


def test_different_hash_with_explicit_marker_is_corroborated_revision():
    prior = PriorSend(historical_quote_key="hqk-1", attachment_sha256="abc123")
    result = link_revision(
        prior_send=prior, current_attachment_sha256="def456",
        current_historical_quote_key="hqk-1",
        evidence_text="Cotización COT-2026-014 Rev. 2 - precios actualizados",
    )
    assert result.relationship == "corroborated_new_revision"
    assert result.supersedes_historical_quote_key == "hqk-1"


def test_different_hash_without_marker_needs_review_and_no_auto_link():
    prior = PriorSend(historical_quote_key="hqk-1", attachment_sha256="abc123")
    result = link_revision(
        prior_send=prior, current_attachment_sha256="def456",
        current_historical_quote_key="hqk-1", evidence_text="Cotización COT-2026-014",
    )
    assert result.relationship == "possible_revision_needs_review"
    assert result.supersedes_historical_quote_key is None  # never auto-linked without evidence


def test_missing_hash_on_either_side_needs_review():
    prior = PriorSend(historical_quote_key="hqk-1", attachment_sha256=None)
    result = link_revision(
        prior_send=prior, current_attachment_sha256="def456",
        current_historical_quote_key="hqk-1", evidence_text="Cotización COT-2026-014",
    )
    assert result.relationship == "possible_revision_needs_review"
