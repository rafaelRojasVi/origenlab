"""Explicit-evidence-only commercial outcome. Replying, asking a question, or
saying thanks is never acceptance (design: "STATUS MODEL" / commercial
outcome state) — only an explicit accept/reject phrase, or a corroborated
new revision (superseding this one), ever sets a definite outcome.
"""

from __future__ import annotations

import re
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

# Negation and conditional markers that guard (disqualify) a phrase match.
# These are matched with word boundaries (\b) to avoid false positives from substrings.
# NOTE: This is a best-effort heuristic, not a complete negation/conditional grammar.
# Classification outputs (accepted_explicit/rejected_explicit) are reviewed by humans
# before any durable CRM import, so incomplete coverage here is acceptable.
_NEGATION_MARKERS = (
    "no", "aún no", "todavía no", "nunca", "jamás", "sin",
    "tampoco", "para nada", "de ninguna manera", "en absoluto", "ni",
    "not", "don't", "won't", "haven't",
)
_CONDITIONAL_MARKERS = (
    "si", "en caso de", "si llegáramos a", "de llegar a",
    "if we", "in case",
)


def _is_text_guarded(preceding_text: str) -> bool:
    """Check if preceding text contains negation or conditional markers.

    Uses word-boundary matching (\b) to avoid false positives from substrings
    (e.g., "si" inside "revisión" or "positivo").
    """
    all_markers = _NEGATION_MARKERS + _CONDITIONAL_MARKERS
    for marker in all_markers:
        # Word boundaries protect against substring matches within words
        pattern = rf"\b{re.escape(marker)}\b"
        if re.search(pattern, preceding_text, re.IGNORECASE):
            return True
    return False


def _phrase_has_unguarded_occurrence(haystack: str, phrase: str) -> bool:
    """Check if phrase appears in haystack without negation/conditional guard.

    Returns True if there's at least one occurrence that's not preceded by
    a negation or conditional marker within ~60 characters (~8-10 words).
    """
    start = 0
    while True:
        pos = haystack.find(phrase, start)
        if pos < 0:
            break  # No more occurrences

        # Check the text before this occurrence (up to 60 chars back)
        preceding_start = max(0, pos - 60)
        preceding_text = haystack[preceding_start:pos].strip()

        # If this occurrence is not guarded, return True (phrase is evidence)
        if not _is_text_guarded(preceding_text):
            return True

        start = pos + 1

    return False  # All occurrences (if any) are guarded


def classify_outcome(
    *, response_state: str, reply_bodies: list[str], revision_relationship: str,
) -> OutcomeState:
    if revision_relationship == "possible_revision_needs_review":
        return "needs_review"
    if revision_relationship == "corroborated_new_revision":
        return "superseded_or_requoted"

    haystack = " ".join(b.lower() for b in reply_bodies if b)

    # Check reject phrases first
    if any(_phrase_has_unguarded_occurrence(haystack, p) for p in _REJECT_PHRASES):
        return "rejected_explicit"

    # Check accept phrases
    if any(_phrase_has_unguarded_occurrence(haystack, p) for p in _ACCEPT_PHRASES):
        return "accepted_explicit"

    return "unknown"
