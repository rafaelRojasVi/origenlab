"""How a phone call ended — `crm.note.call_outcome`, said once.

A «Llamada» is an ordinary `crm.note` with `note_kind = 'llamada'`: the owner wants to count calls
by outcome, so the outcome and the agreed next step are columns, not text folded into the body.
The six values spell the `note_call_outcome_check` constraint of migrations
`20261110100000_slice8_note_kind.sql` and `20261123100000_slice9_note_call_whatsapp.sql` (a WhatsApp sent) and `docs/DOMAIN.md` §7.17 exactly.

The columns are written once, when the note is inserted (`note_guard()` freezes them after), and
the shape is all or nothing: a call record is `note_kind = 'llamada'` **with** an outcome (the next
step is optional); an ordinary note has none of the three. `check_call_shape` is that rule in
Python so a bad request is a 422 before it reaches the database.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal, get_args

NoteKind = Literal["llamada"]
NOTE_KIND_LLAMADA: NoteKind = "llamada"

CallOutcome = Literal["contesto", "no_contesto", "buzon", "numero_malo", "reunion", "whatsapp"]
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


#: How far back a call logged after the fact may be dated (the request rule; the table only
#: refuses the future: migration `20261119100000_slice8_note_call_occurred_at.sql`).
CALL_BACKDATE_MAX = timedelta(days=60)
#: Clock slack for «now» between the browser and the API (`note_occurred_at_not_future`).
CALL_CLOCK_SLACK = timedelta(minutes=5)


def check_call_time(note_kind: str | None, occurred_at: datetime | None, now: datetime | None = None) -> None:
    """Raise `ValueError` unless `occurred_at` is absent, or a call's time within the last 60 days."""
    if occurred_at is None:
        return
    if note_kind != NOTE_KIND_LLAMADA:
        raise ValueError("occurred_at is only allowed when note_kind is 'llamada'")
    if occurred_at.tzinfo is None:
        raise ValueError("occurred_at must carry a time zone")
    now = now or datetime.now(UTC)
    if occurred_at > now + CALL_CLOCK_SLACK:
        raise ValueError("occurred_at must not be in the future")
    if occurred_at < now - CALL_BACKDATE_MAX:
        raise ValueError("occurred_at must be within the last 60 days")
