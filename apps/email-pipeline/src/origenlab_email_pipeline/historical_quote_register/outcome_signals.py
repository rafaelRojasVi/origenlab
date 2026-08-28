"""Explicit-evidence-only commercial outcome. Replying, asking a question, or
saying thanks is never acceptance (design: "STATUS MODEL" / commercial
outcome state) — only an explicit accept/reject phrase, or a corroborated
new revision (superseding this one), ever sets a definite outcome.
"""

from __future__ import annotations

from typing import Literal

OutcomeState = Literal[
    "accepted_explicit", "rejected_explicit", "superseded_or_requoted", "unknown", "needs_review"
]

_ACCEPT_PHRASES = (
    "confirmamos la compra",
    "aceptamos la cotización",
    "aceptamos la propuesta",
    "procedan con la compra",
    "emitan la factura",
    "we accept the quote",
    "we confirm the purchase",
    "please proceed with the order",
    "please issue the invoice",
)
_REJECT_PHRASES = (
    "no continuaremos",
    "decidimos no continuar",
    "no seguiremos con esta cotización",
    "rechazamos la cotización",
    "we will not proceed",
    "we decided not to proceed",
    "we reject the quote",
)


def classify_outcome(
    *, response_state: str, reply_bodies: list[str], revision_relationship: str,
) -> OutcomeState:
    if revision_relationship == "possible_revision_needs_review":
        return "needs_review"
    if revision_relationship == "corroborated_new_revision":
        return "superseded_or_requoted"

    haystack = " ".join(b.lower() for b in reply_bodies if b)
    if any(p in haystack for p in _REJECT_PHRASES):
        return "rejected_explicit"
    if any(p in haystack for p in _ACCEPT_PHRASES):
        return "accepted_explicit"
    return "unknown"
