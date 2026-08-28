from __future__ import annotations

from origenlab_email_pipeline.historical_quote_register.outcome_signals import classify_outcome


def test_explicit_acceptance_phrase():
    outcome = classify_outcome(
        response_state="replied",
        reply_bodies=["Confirmamos la compra, por favor emitan la factura."],
        revision_relationship="first_revision",
    )
    assert outcome == "accepted_explicit"


def test_explicit_rejection_phrase():
    outcome = classify_outcome(
        response_state="replied",
        reply_bodies=["Gracias, pero decidimos no continuar con esta cotización."],
        revision_relationship="first_revision",
    )
    assert outcome == "rejected_explicit"


def test_mere_engagement_is_not_acceptance():
    outcome = classify_outcome(
        response_state="replied",
        reply_bodies=["Gracias por la cotización, quedo atento a más detalles."],
        revision_relationship="first_revision",
    )
    assert outcome == "unknown"


def test_question_is_not_acceptance():
    outcome = classify_outcome(
        response_state="replied",
        reply_bodies=["¿Tienen stock disponible para envío inmediato?"],
        revision_relationship="first_revision",
    )
    assert outcome == "unknown"


def test_corroborated_new_revision_is_superseded():
    outcome = classify_outcome(
        response_state="no_reply", reply_bodies=[], revision_relationship="corroborated_new_revision",
    )
    assert outcome == "superseded_or_requoted"


def test_possible_revision_needs_review_propagates():
    outcome = classify_outcome(
        response_state="no_reply", reply_bodies=[], revision_relationship="possible_revision_needs_review",
    )
    assert outcome == "needs_review"


def test_no_reply_with_first_revision_is_unknown_not_rejected():
    outcome = classify_outcome(response_state="no_reply", reply_bodies=[], revision_relationship="first_revision")
    assert outcome == "unknown"
