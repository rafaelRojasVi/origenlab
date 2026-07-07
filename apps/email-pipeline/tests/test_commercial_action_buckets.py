"""Tests for commercial action bucket derivation."""

from __future__ import annotations

from origenlab_email_pipeline.lead_research.commercial_action_buckets import (
    BUCKET_ALREADY_CONTACTED,
    BUCKET_BLOCKED,
    BUCKET_NEEDS_EMAIL_ENRICHMENT,
    BUCKET_READY_TO_CONTACT,
    BUCKET_REVIEW_HISTORY,
    BUCKET_TENDER_OPPORTUNITY,
    derive_commercial_action_bucket,
    derive_operational_next_action,
    enrich_prospect_for_dashboard,
    is_followup_eligible,
    summarize_commercial_action_buckets,
)


def test_ready_to_contact_net_new_with_email() -> None:
    row = {
        "classification": "net_new_safe_review",
        "is_blocked": False,
        "email": "lab@acme.cl",
        "gmail_sent_count": 0,
        "gmail_received_count": 0,
        "source_type": "deepsearch",
    }
    assert derive_commercial_action_bucket(row) == BUCKET_READY_TO_CONTACT
    assert "contacto inicial" in derive_operational_next_action(row).lower()


def test_needs_email_enrichment_classification() -> None:
    row = {
        "classification": "research_only_contact_needed",
        "is_blocked": False,
        "email": None,
        "gmail_sent_count": 0,
        "gmail_received_count": 0,
    }
    assert derive_commercial_action_bucket(row) == BUCKET_NEEDS_EMAIL_ENRICHMENT
    assert "enriquecer contacto" in derive_operational_next_action(row).lower()


def test_tender_opportunity() -> None:
    row = {
        "classification": "public_tender_review",
        "is_blocked": False,
        "email": None,
    }
    assert derive_commercial_action_bucket(row) == BUCKET_TENDER_OPPORTUNITY
    assert "licitación" in derive_operational_next_action(row).lower()


def test_review_history_same_domain() -> None:
    row = {
        "classification": "same_domain_contacted_review",
        "is_blocked": False,
        "email": "new@udec.cl",
        "gmail_sent_count": 0,
        "gmail_received_count": 0,
        "source_type": "deepsearch",
    }
    assert derive_commercial_action_bucket(row) == BUCKET_REVIEW_HISTORY
    assert "historial" in derive_operational_next_action(row).lower()


def test_already_contacted_manual_outreach() -> None:
    row = {
        "classification": "manual_outreach_sent",
        "is_blocked": False,
        "email": "ops@client.cl",
        "gmail_sent_count": 0,
        "gmail_received_count": 0,
    }
    assert derive_commercial_action_bucket(row) == BUCKET_ALREADY_CONTACTED
    assert "no reenviar" in derive_operational_next_action(row).lower()
    assert is_followup_eligible(row) is True


def test_already_contacted_gmail_counts() -> None:
    row = {
        "classification": "old_gmail_prospect_review",
        "is_blocked": False,
        "email": "ana@hist.cl",
        "gmail_sent_count": 4,
        "gmail_received_count": 1,
        "source_type": "gmail_historico",
    }
    assert derive_commercial_action_bucket(row) == BUCKET_ALREADY_CONTACTED
    assert is_followup_eligible(row) is False


def test_followup_eligible_sent_no_reply() -> None:
    row = {
        "classification": "old_gmail_prospect_review",
        "is_blocked": False,
        "email": "ana@hist.cl",
        "gmail_sent_count": 2,
        "gmail_received_count": 0,
        "source_type": "gmail_historico",
    }
    assert derive_commercial_action_bucket(row) == BUCKET_ALREADY_CONTACTED
    assert is_followup_eligible(row) is True


def test_blocked_wins_over_other_signals() -> None:
    row = {
        "classification": "bounced_suppressed",
        "is_blocked": True,
        "email": "bad@x.cl",
        "gmail_sent_count": 5,
    }
    assert derive_commercial_action_bucket(row) == BUCKET_BLOCKED
    assert derive_operational_next_action(row) == "No contactar"


def test_summarize_buckets_partition_total() -> None:
    prospects = [
        enrich_prospect_for_dashboard(
            {
                "classification": "net_new_safe_review",
                "is_blocked": False,
                "email": "a@a.cl",
                "gmail_sent_count": 0,
                "gmail_received_count": 0,
                "source_type": "deepsearch",
            },
        ),
        enrich_prospect_for_dashboard(
            {
                "classification": "research_only_contact_needed",
                "is_blocked": False,
                "email": None,
                "gmail_sent_count": 0,
                "gmail_received_count": 0,
            },
        ),
        enrich_prospect_for_dashboard(
            {
                "classification": "manual_outreach_sent",
                "is_blocked": False,
                "email": "c@c.cl",
                "gmail_sent_count": 1,
                "gmail_received_count": 0,
            },
        ),
    ]
    agg = summarize_commercial_action_buckets(prospects)
    assert agg["ready_to_contact"] == 1
    assert agg["needs_email_enrichment"] == 1
    assert agg["already_contacted"] == 1
    assert agg["followup_eligible"] == 1
    assert sum(agg[k] for k in agg if k != "followup_eligible") == 3
