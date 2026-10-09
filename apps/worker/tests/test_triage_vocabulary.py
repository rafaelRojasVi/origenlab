"""The worker writes the readings the API's review validates corrections against: one vocabulary."""

from __future__ import annotations

from origenlab_api.v2 import triage_review as api
from origenlab_worker.triage_model import INTENTS, STAGE_ANSWERS
from origenlab_worker.triage_rules import CLASSES


def test_classes_stages_and_intents_are_the_same_on_both_sides() -> None:
    assert set(CLASSES) == set(api.TRIAGE_CLASSES)
    assert STAGE_ANSWERS == api.TRIAGE_STAGES
    assert INTENTS == api.TRIAGE_INTENTS
