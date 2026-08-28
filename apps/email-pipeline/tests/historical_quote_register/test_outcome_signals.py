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


# Regression tests for negation guard (Spanish negation before phrase)
def test_negation_guard_no_emitan_la_factura():
    """Spanish negation 'no' before accept phrase must not trigger accepted_explicit.

    This was a critical bug: "No emitan la factura" contains the literal substring
    "emitan la factura" but is explicitly deferring/rejecting invoice issuance.
    """
    outcome = classify_outcome(
        response_state="replied",
        reply_bodies=["No emitan la factura todavía, esperamos confirmar el presupuesto con gerencia."],
        revision_relationship="first_revision",
    )
    assert outcome == "unknown", "Negation 'no' before accept phrase must not trigger accepted_explicit"


def test_negation_guard_no_procedan_con_la_compra():
    """Spanish negation 'no' before accept phrase in middle of sentence."""
    outcome = classify_outcome(
        response_state="replied",
        reply_bodies=["Por ahora no procedan con la compra hasta nuevo aviso."],
        revision_relationship="first_revision",
    )
    assert outcome == "unknown", "Negation 'no' before accept phrase must not trigger accepted_explicit"


def test_negation_guard_no_aceptamos_la_propuesta():
    """Spanish negation 'no' before reject phrase changes meaning from reject to non-decision."""
    outcome = classify_outcome(
        response_state="replied",
        reply_bodies=["No aceptamos la propuesta en estos términos, necesitamos un descuento."],
        revision_relationship="first_revision",
    )
    assert outcome == "unknown", "Negation before reject phrase is a negotiation, not acceptance"


# Regression tests for conditional guard (conditional markers)
def test_conditional_guard_si_decidimos_no_continuar():
    """Conditional phrasing 'si' (if) before reject phrase is hypothetical, not a decision."""
    outcome = classify_outcome(
        response_state="replied",
        reply_bodies=["Si decidimos no continuar, les avisaremos, pero por ahora seguimos revisando."],
        revision_relationship="first_revision",
    )
    assert outcome == "unknown", "Conditional 'si' before reject phrase is hypothetical, not a decision"


# Round 2: Regression tests for word-boundary anchoring (false guards from substrings)
def test_word_boundary_positivo_contains_si_but_not_word_si():
    """'positivo' contains substring 'si' but is not the word 'si'.

    This was a regression from round 1: marker in preceding_text
    matches 'si' inside 'posi**tivo**', falsely guarding genuine accept phrase.
    """
    outcome = classify_outcome(
        response_state="replied",
        reply_bodies=["El presupuesto nos parece positivo, confirmamos la compra."],
        revision_relationship="first_revision",
    )
    assert outcome == "accepted_explicit", "'positivo' should not trigger 'si' guard"


def test_word_boundary_revision_contains_si_but_not_word_si():
    """'revisión' contains substring 'si' but is not the word 'si'."""
    outcome = classify_outcome(
        response_state="replied",
        reply_bodies=["Estimados, tras revisión, aceptamos la propuesta."],
        revision_relationship="first_revision",
    )
    assert outcome == "accepted_explicit", "'revisión' should not trigger 'si' guard"


def test_word_boundary_consideramos_contains_si_but_not_word_si():
    """'consideramos' contains substring 'si' but is not the word 'si'."""
    outcome = classify_outcome(
        response_state="replied",
        reply_bodies=["Lo consideramos internamente y confirmamos la compra, gracias."],
        revision_relationship="first_revision",
    )
    assert outcome == "accepted_explicit", "'consideramos' should not trigger 'si' guard"


# Round 2: Tests for expanded negation/conditional marker lists
def test_negation_guard_tampoco():
    """Negation word 'tampoco' (nor, neither) should guard reject phrase."""
    outcome = classify_outcome(
        response_state="replied",
        reply_bodies=["Tampoco confirmamos la compra en esta ocasión."],
        revision_relationship="first_revision",
    )
    assert outcome == "unknown", "Negation 'tampoco' should guard accept phrase"


def test_conditional_guard_de_llegar_a():
    """Conditional phrase 'de llegar a' should guard accept phrase."""
    outcome = classify_outcome(
        response_state="replied",
        reply_bodies=["De llegar a concretarse otros cambios, aceptamos la propuesta."],
        revision_relationship="first_revision",
    )
    assert outcome == "unknown", "Conditional 'de llegar a' should guard accept phrase"
