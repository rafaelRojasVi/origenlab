"""The free-text policy: what is digested, what survives, what refuses.

Every fixture string here is deliberately sensitive-looking. None of it is
real, and none of it may survive :func:`redact_row`.
"""

from __future__ import annotations

import json

import pytest

from origenlab_email_pipeline.contact_email_suppression import SUPPRESSION_REASON_CODES
from origenlab_email_pipeline.migration.free_text import (
    CLOSED_VOCABULARY_COLUMNS,
    FREE_TEXT_COLUMNS,
    assert_no_free_text_remains,
    free_text_digest,
    free_text_policy_document,
    normalize_free_text,
    redact_row,
)

SECRET = "Called Dr. Quiroga on +56 9 1111 2222 — do not contact until the audit ends"


def test_a_free_text_column_becomes_a_presence_flag_and_a_digest() -> None:
    out = redact_row("outreach_contact_state", {"contact_email_norm": "a@example.invalid",
                                                "notes": SECRET})
    assert "notes" not in out
    assert out["notes_present"] is True
    assert out["notes_sha256"] == free_text_digest(SECRET)
    assert SECRET not in json.dumps(out)


def test_an_empty_or_absent_value_records_presence_false_without_a_digest() -> None:
    for value in (None, "", "   ", "\n\t "):
        out = redact_row("manual_contact_status", {"email_norm": "a@example.invalid",
                                                   "reason": value})
        assert out["reason_present"] is False
        assert "reason_sha256" not in out


def test_the_digest_proves_equality_and_ignores_cosmetic_differences() -> None:
    assert free_text_digest(SECRET) == free_text_digest(f"  {SECRET}  ")
    assert free_text_digest("a  b\n c") == free_text_digest("a b c")
    assert free_text_digest("Caso") != free_text_digest("caso")
    # Unicode composition is normalized; the two spellings of "ó" agree.
    assert free_text_digest("acción") == free_text_digest("acción")


def test_the_digest_is_not_the_text_and_carries_no_fragment_of_it() -> None:
    digest = free_text_digest(SECRET)
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)
    for word in ("Quiroga", "1111", "audit"):
        assert word not in digest


def test_normalization_is_documented_and_idempotent() -> None:
    once = normalize_free_text(f"  {SECRET}\n\n ")
    assert normalize_free_text(once) == once


def test_every_named_free_text_column_is_covered_for_every_read_table() -> None:
    from origenlab_email_pipeline.migration.wave1b_extract import SOURCE_TABLES

    assert set(FREE_TEXT_COLUMNS) == set(SOURCE_TABLES)
    # The three columns the hardening brief names explicitly.
    assert "notes" in FREE_TEXT_COLUMNS["outreach_contact_state"]
    assert "reason" in FREE_TEXT_COLUMNS["manual_contact_status"]
    assert "evidence" in FREE_TEXT_COLUMNS["manual_contact_status"]
    # Plus the unrestricted ones discovered in the same tables.
    assert "suppression_reason_text" in FREE_TEXT_COLUMNS["contact_email_suppression"]
    assert "suppression_reason_text" in FREE_TEXT_COLUMNS["contact_domain_suppression"]
    assert "error_detail" in FREE_TEXT_COLUMNS["outbound_send_attempt"]


@pytest.mark.parametrize("code", SUPPRESSION_REASON_CODES)
def test_a_closed_reason_code_inside_its_vocabulary_survives(code: str) -> None:
    out = redact_row("contact_email_suppression", {"suppression_reason_code": code})
    assert out["suppression_reason_code"] == code
    assert "suppression_reason_code_sha256" not in out


def test_an_out_of_vocabulary_reason_code_is_demoted_to_free_text() -> None:
    rogue = "operator typed a whole paragraph about Dr. Quiroga here"
    out = redact_row("contact_email_suppression", {"suppression_reason_code": rogue})
    assert "suppression_reason_code" not in out
    assert out["suppression_reason_code_present"] is True
    assert out["suppression_reason_code_sha256"] == free_text_digest(rogue)


def test_block_and_selection_reasons_follow_the_gate_vocabulary() -> None:
    kept = redact_row(
        "outbound_campaign_recipient",
        {"block_reason": "sent_history", "selection_reason": "gate_eligible"},
    )
    assert kept["block_reason"] == "sent_history"
    assert kept["selection_reason"] == "gate_eligible"

    demoted = redact_row("outbound_campaign_recipient", {"block_reason": "porque sí"})
    assert "block_reason" not in demoted
    assert demoted["block_reason_present"] is True


def test_a_null_closed_vocabulary_value_stays_null() -> None:
    out = redact_row("outbound_campaign_recipient", {"block_reason": None})
    assert out["block_reason"] is None


def test_the_survivor_guard_refuses_a_row_that_kept_its_free_text() -> None:
    with pytest.raises(ValueError, match="notes"):
        assert_no_free_text_remains("outreach_contact_state", {"notes": SECRET})
    assert_no_free_text_remains("outreach_contact_state", {"notes_present": True})


def test_the_policy_document_states_that_a_digest_cannot_be_reversed() -> None:
    policy = free_text_policy_document()
    assert "CANNOT reconstruct" in policy["digest_semantics"]
    assert "never written" in policy["never_exported"]
    assert set(policy["closed_vocabulary_columns"]) == set(CLOSED_VOCABULARY_COLUMNS)
