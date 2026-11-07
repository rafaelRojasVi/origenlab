"""How a phone call ended — `crm.note.call_outcome`, said once.

A «Llamada» is an ordinary `crm.note` with `note_kind = 'llamada'`: the owner wants to count calls
by outcome, so the outcome and the agreed next step are columns, not text folded into the body.
The five values spell the `note_call_outcome_check` constraint of migration
`20261110100000_slice8_note_kind.sql` and `docs/DOMAIN.md` §7.17 exactly.

The columns are written once, when the note is inserted (`note_guard()` freezes them after), and
the shape is all or nothing: a call record is `note_kind = 'llamada'` **with** an outcome (the next
step is optional); an ordinary note has none of the three. `check_call_shape` is that rule in
Python so a bad request is a 422 before it reaches the database.
"""

from __future__ import annotations

from typing import Literal, get_args

NoteKind = Literal["llamada"]
NOTE_KIND_LLAMADA: NoteKind = "llamada"

CallOutcome = Literal["contesto", "no_contesto", "buzon", "numero_malo", "reunion"]
CALL_OUTCOMES: tuple[str, ...] = get_args(CallOutcome)

#: `note_call_next_step_check`: at most this many characters.
CALL_NEXT_STEP_MAX = 500


def check_call_shape(note_kind: str | None, call_outcome: str | None, call_next_step: str | None) -> None:
    """Raise `ValueError` unless the three values are a call record or an ordinary note."""
    if note_kind == NOTE_KIND_LLAMADA:
        if call_outcome is None:
            raise ValueError("call_outcome is required when note_kind is 'llamada'")
        return
    if call_outcome is not None or call_next_step is not None:
        raise ValueError("call_outcome and call_next_step are only allowed when note_kind is 'llamada'")
